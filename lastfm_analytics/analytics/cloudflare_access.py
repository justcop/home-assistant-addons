"""Verify the same Cloudflare Access Google SSO used by AudioShelf.

Listening has its own Access application and audience. Never trust identity headers,
OAuth cookies or unsigned email addresses as substitutes for a signed assertion.
"""
import re

import jwt


class AccessDenied(Exception):
    """A request could not prove the approved Google identity."""


class CloudflareAccess:
    def __init__(self, options):
        self.team = str(options.get("cloudflare_team_domain") or "").strip().removeprefix("https://").rstrip("/")
        self.audience = str(options.get("cloudflare_audience") or "").strip()
        self.owner_email = str(options.get("cloudflare_owner_email") or "").strip().lower()
        self.enabled = bool(self.team or self.audience or self.owner_email)
        if self.enabled and (
            not re.fullmatch(r"[a-z0-9-]+\.cloudflareaccess\.com", self.team)
            or not self.audience
            or not re.fullmatch(r"[^@\s]+@[^@\s]+", self.owner_email)
        ):
            raise ValueError("Cloudflare login requires a team domain, Listening application audience and owner email.")
        self.issuer = "https://" + self.team if self.enabled else ""
        self.keys = jwt.PyJWKClient(self.issuer + "/cdn-cgi/access/certs", timeout=10) if self.enabled else None

    def verify(self, token):
        if not token or not isinstance(token, str) or len(token) > 16384:
            raise AccessDenied("Sign in through Cloudflare with Google to open Listening.")
        try:
            signing_key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "email", "iss", "aud"]},
            )
            if (
                claims.get("type") != "app"
                or not isinstance(claims.get("sub"), str)
                or not claims["sub"]
                or not isinstance(claims.get("email"), str)
                or claims["email"].lower() != self.owner_email
                or claims.get("common_name")
            ):
                raise AccessDenied("This Google account is not approved for Listening.")
            return claims
        except AccessDenied:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise AccessDenied("Cloudflare login could not be verified. Sign in again.") from None
