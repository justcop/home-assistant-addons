"""Read-only LifeStage client based on the browser protocol verified 8 Oct 2026.
Public MHCT2 constants are protocol parameters, not account credentials.
"""
from __future__ import annotations
import base64
from http.cookiejar import Cookie, CookieJar
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib import error, parse, request
import uuid

API_BASE = "https://asm.wpsa-app.com"
TENANT_ID = "33077306fa09b17e25987df99156d13b68f735de5f044c932ba32fed69dd5b7d"
SCHEME_VERSION = "MHCT2"
PEPPER_HEX = "1005b11c9175ee361e8033c957830788d146a2f443e877212d2189c4c5c7761a"
INTERMEDIATE_MAC_HEX = "4aebcd8d60ee3136ca29ec3d31d506ee67fc1f2f0d3ecf97e9d829f87e0a5a64"
PUBLIC_SIGNING_KEY = "d82566e4fbed74f114bfb98f6d88b220c268d16612a714619cb63a80d25b27db"
INTERMEDIATE_ROUNDS = 957
# In the captured browser requests, x-yw-device-id is identical to User-Agent.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json_bytes(obj: dict) -> bytes:
    # The website uses JSON.stringify and unpadded base64url.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def derive_intermediate_secret(email: str, password: str, *, issued_at: int | None = None, jti: str | None = None) -> str:
    """Reproduce the browser's MHCT2 intermediateUserSecret JWT."""
    key_material = password.encode("utf-8") + b"::" + bytes.fromhex(PEPPER_HEX)
    salt = hmac.new(bytes.fromhex(INTERMEDIATE_MAC_HEX), email.encode("utf-8"), hashlib.sha256).digest()
    intermediate_key = hashlib.pbkdf2_hmac("sha256", key_material, salt, INTERMEDIATE_ROUNDS, dklen=32)
    payload = {
        "username": email,
        "intermediateUserSecretKey": intermediate_key.hex(),
        "schemeVersion": SCHEME_VERSION,
        "iat": int(time.time()) if issued_at is None else issued_at,
        "jti": secrets.token_hex(8) if jti is None else jti,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    unsigned_token = _b64url(_json_bytes(header)) + "." + _b64url(_json_bytes(payload))
    # Important: the site's crypto module signs using the UTF-8 *text* of
    # PUBLIC_SIGNING_KEY, not bytes.fromhex(PUBLIC_SIGNING_KEY).
    signature = hmac.new(PUBLIC_SIGNING_KEY.encode("utf-8"), unsigned_token.encode("utf-8"), hashlib.sha256).digest()
    return unsigned_token + "." + _b64url(signature)


class LifeStageError(ValueError):
    pass


class Reauthenticate(LifeStageError):
    pass


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Reauthenticate('LifeStage redirected the request. Please reconnect.')


class LifeStageClient:
    """A session is only used on a fixed HTTPS origin; no caller-supplied URLs."""
    def __init__(self, session_path):
        self.session_path = Path(session_path)
        self.device_id = str(uuid.uuid4())
        self.csrf_token = None
        self.cookies = CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookies), NoRedirects())
        self.login_token = None
        self.challenge_until = 0
        self.connected = False
        self.owner_hash = None
        if self.session_path.exists():
            try:
                saved = json.loads(self.session_path.read_text())
                self.device_id = saved['device_id']
                self.csrf_token = saved['csrf_token']
                self.owner_hash = saved['owner_hash']
                for cookie in saved['cookies']:
                    if cookie['domain'].lstrip('.') not in ('asm.wpsa-app.com', 'wpsa-app.com'):
                        raise ValueError('Unexpected session domain')
                    self.cookies.set_cookie(Cookie(**cookie))
                self.connected = bool(self.csrf_token)
            except (ValueError, KeyError, TypeError):
                self.forget()

    def save(self):
        payload = {'device_id': self.device_id, 'csrf_token': self.csrf_token, 'cookies': [], 'owner_hash': self.owner_hash}
        for c in self.cookies:
            payload['cookies'].append({k: getattr(c, k) for k in (
                'version', 'name', 'value', 'port', 'port_specified', 'domain',
                'domain_specified', 'domain_initial_dot', 'path', 'path_specified',
                'secure', 'expires', 'discard', 'comment', 'comment_url') } | {'rest': c._rest})
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.session_path.with_suffix('.tmp')
        with open(temp, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        temp.replace(self.session_path)

    def forget(self):
        self.csrf_token = None
        self.cookies.clear()
        self.login_token = None
        self.challenge_until = 0
        self.connected = False
        self.session_path.unlink(missing_ok=True)

    def api_call(self, method, path, data=None, params=None):
        if (method, path) not in {('POST', '/login'), ('GET', '/apiv2/accounts'),
                                 ('GET', '/apiV2/transactions')}:
            raise LifeStageError('Unsupported LifeStage operation.')
        url = API_BASE + path + ('?' + parse.urlencode(params) if params else '')
        headers = {'Accept': 'application/json', 'Origin': 'https://wpsa-app.com',
                   'authorization-mode': 'v2', 'x-yw-client': '2.7.1',
                   'x-requested-with': 'XMLHttpRequest', 'User-Agent': BROWSER_USER_AGENT,
                   'x-yw-device-id': BROWSER_USER_AGENT, 'x-force-date': 'undefined'}
        if self.csrf_token:
            headers['csrf-token'] = self.csrf_token
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = _json_bytes(data)
        try:
            with self.opener.open(request.Request(url, data=body, headers=headers, method=method), timeout=25) as resp:
                self.csrf_token = resp.headers.get('csrf-token') or self.csrf_token
                raw = resp.read(20 * 1024 * 1024 + 1)
                if len(raw) > 20 * 1024 * 1024:
                    raise LifeStageError('LifeStage response is too large. Pull a shorter date range.')
                if 'json' not in resp.headers.get('Content-Type', ''):
                    raise Reauthenticate('LifeStage did not return data. Please reconnect.')
                result = json.loads(raw, parse_float=str)
                if not isinstance(result, dict):
                    raise LifeStageError('LifeStage returned an unexpected response format.')
                return result
        except error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise Reauthenticate('LifeStage rejected the login or the session expired. Please reconnect.') from None
            if code == 429:
                raise LifeStageError('LifeStage is limiting requests. Please try again later.') from None
            raise LifeStageError(f'LifeStage returned HTTP {code}. No imported records were changed.') from None
        except (error.URLError, TimeoutError, OSError):
            raise LifeStageError('Could not reach LifeStage. Check the connection and try again.') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LifeStageError('LifeStage returned an unreadable response.') from None

    def login(self, email, password):
        if not isinstance(email, str) or not isinstance(password, str) or not email.strip() or not password or len(email)>254 or len(password)>1024:
            raise LifeStageError('Enter your LifeStage email and password.')
        self.forget()
        result = self.api_call('POST', '/login', {
            'email': email.strip(), 'intermediateUserSecret': derive_intermediate_secret(email.strip(), password),
            'hash': hashlib.sha256(password.encode()).hexdigest(),
            'tenantId': TENANT_ID, 'deviceId': self.device_id})
        challenge = result.get('loginToken') or (result.get('data',{}).get('loginToken') if isinstance(result.get('data'),dict) else None)
        if challenge:
            self.login_token = challenge
            self.challenge_until = time.time() + 600
            return {'needs_code': True}
        return self.finish_login()

    def verify(self, code):
        if not self.login_token or time.time() > self.challenge_until:
            self.login_token = None
            raise LifeStageError('The verification request expired. Enter your login details again.')
        if not isinstance(code, str) or not code.isdigit() or not 4 <= len(code) <= 10:
            raise LifeStageError('Enter the verification code from LifeStage.')
        result = self.api_call('POST', '/login', {'totpCode': code, 'deviceId': self.device_id,
                    'loginToken': self.login_token, 'mfa': False, 'tenantId': TENANT_ID})
        if result.get('loginToken') or (isinstance(result.get('data'),dict) and result['data'].get('loginToken')):
            raise LifeStageError('Verification is still required. Check the code and try again.')
        return self.finish_login()

    def finish_login(self):
        if not self.csrf_token:
            raise LifeStageError('LifeStage did not return a session. Please reconnect.')
        # A successful login response alone does not prove authenticated access.
        self.records('/apiv2/accounts')
        self.connected = True
        self.login_token = None
        self.save()
        return {'needs_code': False}

    def records(self, path, params=None):
        result = self.api_call('GET', path, params=params)
        data = result.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('result'), list):
            raise LifeStageError('LifeStage data format changed. No imported records were changed.')
        meta = data.get('meta', {})
        # Observed endpoints return an empty meta object. Never silently assume
        # completeness if the provider introduces pagination or truncation.
        if meta or result.get('meta'):
            raise LifeStageError('LifeStage returned pagination metadata this version cannot verify. No records were changed.')
        return data['result']
