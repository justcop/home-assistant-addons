"""Validate Cloudflare Access Google sign-in for this private financial tracker.

Money Locations has its own Cloudflare Access application audience. A session
cookie or a claimed identity header is never evidence of authorization.
"""
import re
import jwt


class AccessDenied(Exception):
    """Request lacks a verified, approved human Cloudflare identity."""


class CloudflareAccess:
    def __init__(self, options):
        self.team = str(options.get("cloudflare_team_domain") or "").strip().removeprefix("https://").rstrip("/")
        self.audience = str(options.get("cloudflare_audience") or "").strip()
        self.owner_email = str(options.get("cloudflare_owner_email") or "").strip().lower()
        self.enabled = bool(self.team or self.audience or self.owner_email)
        if self.enabled and (
            not re.fullmatch(r"[a-z0-9-]+\\.cloudflareaccess\\.com", self.team)
            or not self.audience
            or not re.fullmatch(r"[^@\\s]+@[^@\\s]+", self.owner_email)
        ):
            raise ValueError("Cloudflare login requires the team domain, Money Locations application audience and owner email.")
        self.issuer = "https://" + self.team if self.enabled else ""
        self.keys = jwt.PyJWKClient(self.issuer + "/cdn-cgi/access/certs", timeout=10) if self.enabled else None

    def verify(self, token):
        if not isinstance(token, str) or not token or len(token) > 16384:
            raise AccessDenied("Sign in through Cloudflare with Google to open Money Locations.")
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"],
                audience=self.audience, issuer=self.issuer,
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
                raise AccessDenied("This Google account is not approved for Money Locations.")
            return claims
        except AccessDenied:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise AccessDenied("Cloudflare login could not be verified. Sign in again.") from None
