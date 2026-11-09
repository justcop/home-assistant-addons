"""Verify Access assertions against the configured team, never untrusted headers."""
import re
import jwt
from .errors import AppError


class CloudflareAccess:
    def __init__(self, options):
        self.team = str(options.get('cloudflare_team_domain', '')).strip().removeprefix('https://').rstrip('/')
        self.audience = str(options.get('cloudflare_audience', '')).strip()
        self.owner_email = str(options.get('cloudflare_owner_email', '')).strip().lower()
        self.enabled = bool(self.team or self.audience or self.owner_email)
        if self.enabled and (not re.fullmatch(r'[a-z0-9-]+\.cloudflareaccess\.com', self.team)
                             or not self.audience or '@' not in self.owner_email):
            raise RuntimeError('Cloudflare login requires a team domain, application audience and owner email.')
        self.issuer = 'https://' + self.team
        self.keys = jwt.PyJWKClient(self.issuer + '/cdn-cgi/access/certs', timeout=10) if self.enabled else None

    def verify(self, token):
        if not token or len(token) > 16384:
            raise AppError('Sign in through Cloudflare with Google to open AudioShelf.', 401)
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=self.audience,
                                issuer=self.issuer, options={'require': ['exp', 'iat', 'sub', 'email', 'iss', 'aud']})
            if (claims.get('type') != 'app' or not isinstance(claims['sub'], str) or not claims['sub']
                    or not isinstance(claims['email'], str) or '@' not in claims['email']
                    or len(claims['email']) > 254 or claims.get('common_name')):
                raise ValueError('Human application identity required')
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AppError('Cloudflare login could not be verified. Sign in again.', 401) from None
