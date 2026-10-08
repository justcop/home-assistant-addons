"""Read-only LifeStage / former Moneyhub web client.

This mirrors the web application's password + TOTP flow. Credentials and
authentication tokens are deliberately ephemeral; callers may persist only
non-secret connector settings such as email, tenant ID and device ID.
"""
from dataclasses import dataclass
import base64
import hashlib
import hmac
from http.cookiejar import CookieJar
import json
import secrets
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, Request

BASE_URL = "https://asm.wpsa-app.com/"
PEPPER = "1005b11c9175ee361e8033c957830788d146a2f443e877212d2189c4c5c7761a"
MAC_KEY = bytes.fromhex("4aebcd8d60ee3136ca29ec3d31d506ee67fc1f2f0d3ecf97e9d829f87e0a5a64")
SIGNING_KEY = bytes.fromhex("d82566e4fbed74f114bfb98f6d88b220c268d16612a714619cb63a80d25b27db")
PBKDF2_ROUNDS = 957
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"


class MoneyhubError(ValueError):
    pass


@dataclass
class ApiResponse:
    body: object
    headers: object


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def derive_intermediate_secret(email, password, now=None, jti=None):
    """Reproduce the browser's MHCT2 intermediateUserSecret."""
    if not isinstance(email, str) or not email:
        raise ValueError("Enter your LifeStage email address.")
    if not isinstance(password, str) or not password:
        raise ValueError("Enter your LifeStage password.")
    salt = hmac.new(MAC_KEY, email.encode("utf-8"), hashlib.sha256).digest()
    peppered = (password + "::" + PEPPER).encode("utf-8")
    intermediate_key = hashlib.pbkdf2_hmac(
        "sha256", peppered, salt, PBKDF2_ROUNDS, dklen=32
    ).hex()
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "intermediateUserSecretKey": intermediate_key,
        "iat": int(time.time() if now is None else now),
        "jti": jti or secrets.token_hex(8),
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = (encoded_header + "." + encoded_payload).encode("ascii")
    signature = _b64url(hmac.new(SIGNING_KEY, signing_input, hashlib.sha256).digest())
    return encoded_header + "." + encoded_payload + "." + signature


def extract_rows(payload, *keys):
    """Accept the response shapes seen across LifeStage endpoints."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


class MoneyhubClient:
    def __init__(self, email, tenant_id, device_id=None, opener=None, base_url=BASE_URL):
        self.email = str(email or "").strip()
        self.tenant_id = str(tenant_id or "").strip()
        self.device_id = str(device_id or "").strip() or str(uuid.uuid4())
        self.base_url = base_url.rstrip("/") + "/"
        self.opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))
        self.login_token = None
        self.csrf_token = None
        self.authenticated = False

    @property
    def status(self):
        if self.authenticated:
            return "authenticated"
        if self.login_token:
            return "totp_required"
        return "signed_out"

    def _request(self, path, method="GET", payload=None, query=None, protected=False):
        if query:
            path += ("&" if "?" in path else "?") + urlencode(query)
        url = self.base_url + path.lstrip("/")
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Origin": self.base_url.rstrip("/"),
            "Referer": self.base_url,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if protected:
            if not self.csrf_token:
                raise MoneyhubError("LifeStage authentication is required before pulling data.")
            headers["csrf-token"] = self.csrf_token
            # This matched the browser request observed during investigation.
            headers["x-yw-device-id"] = USER_AGENT
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
                return ApiResponse(decoded, response.headers)
        except HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                message = parsed.get("message") or parsed.get("error") or parsed.get("text")
            except Exception:
                message = None
            detail = ": " + str(message) if message else ""
            raise MoneyhubError(f"LifeStage returned HTTP {exc.code}{detail}") from None
        except URLError as exc:
            raise MoneyhubError("Could not reach LifeStage: " + str(exc.reason)) from None

    def start_login(self, password):
        if not self.email:
            raise MoneyhubError("Enter your LifeStage email address.")
        if not self.tenant_id:
            raise MoneyhubError("Enter the LifeStage tenant ID.")
        self.login_token = None
        self.csrf_token = None
        self.authenticated = False
        response = self._request(
            "login",
            "POST",
            {
                "email": self.email,
                "intermediateUserSecret": derive_intermediate_secret(self.email, password),
                "hash": hashlib.sha256(password.encode("utf-8")).hexdigest(),
                "tenantId": self.tenant_id,
                "deviceId": self.device_id,
            },
        )
        if hasattr(response.headers, "get"):
            self.csrf_token = response.headers.get("csrf-token")
        if isinstance(response.body, dict):
            self.login_token = response.body.get("loginToken")
            if not self.login_token and isinstance(response.body.get("data"), dict):
                self.login_token = response.body["data"].get("loginToken")
        if self.login_token:
            return {"status": "totp_required"}
        if self.csrf_token:
            self.authenticated = True
            return {"status": "authenticated"}
        raise MoneyhubError("LifeStage login did not return a 2FA challenge or authenticated session.")

    def verify_totp(self, code):
        if not self.login_token:
            raise MoneyhubError("Start LifeStage login before entering the 2FA code.")
        code = "".join(str(code or "").split())
        if not code:
            raise MoneyhubError("Enter the LifeStage 2FA code.")
        response = self._request(
            "login",
            "POST",
            {
                "totpCode": code,
                "deviceId": self.device_id,
                "loginToken": self.login_token,
                "mfa": False,
                "tenantId": self.tenant_id,
            },
        )
        token = response.headers.get("csrf-token") if hasattr(response.headers, "get") else None
        if not token:
            raise MoneyhubError("LifeStage accepted the request but did not return an authentication token.")
        self.csrf_token = token
        self.login_token = None
        self.authenticated = True
        return {"status": "authenticated"}

    def pull(self, start_date, end_date):
        if not self.authenticated:
            raise MoneyhubError("Authenticate with LifeStage before pulling data.")
        active = self._request("apiv2/accounts/active", protected=True).body
        accounts = self._request("apiv2/accounts", protected=True).body
        transactions = self._request(
            "apiV2/transactions",
            query={"startDate": start_date, "endDate": end_date},
            protected=True,
        ).body
        return {
            "active_accounts": active,
            "accounts": accounts,
            "transactions": transactions,
        }
