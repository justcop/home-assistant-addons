"""Regression tests for the Cloudflare Access Google authentication boundary."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from cloudflare_access import CloudflareAccess
from server import create_server


class CloudflareLogin(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.access = CloudflareAccess({
            "cloudflare_team_domain": "example.cloudflareaccess.com",
            "cloudflare_audience": "money-locations-audience",
            "cloudflare_owner_email": "owner@example.com",
        })
        # Never request real Cloudflare keys during the test.
        self.access.keys.get_signing_key_from_jwt = lambda token: type(
            "Key", (), {"key": self.key.public_key()}
        )()
        self.server = create_server(
            Path(self.temp.name) / "money.sqlite", host="127.0.0.1",
            port=0, local=False, direct=True, password="old-password",
            access=self.access,
        )
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.worker.join()
        self.server.server_close()
        self.temp.cleanup()

    def token(self, **changes):
        now = int(time.time())
        claims = {
            "iss": "https://example.cloudflareaccess.com",
            "aud": "money-locations-audience",
            "exp": now + 3600,
            "iat": now,
            "sub": "google-human-123",
            "email": "owner@example.com",
            "type": "app",
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256")

    def request(self, path, token=None, body=None, extra=None):
        headers = {"X-Money-Request": "1"}
        if token:
            headers["Cf-Access-Jwt-Assertion"] = token
        if extra:
            headers.update(extra)
        if body is not None:
            headers["Content-Type"] = "application/json"
        return urlopen(Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        ))

    def denied(self, path, code=401, token=None, body=None, extra=None):
        with self.assertRaises(HTTPError) as exc:
            self.request(path, token=token, body=body, extra=extra)
        self.assertEqual(exc.exception.code, code)

    def test_owner_can_read_and_write_without_local_password(self):
        token = self.token()
        status = json.load(self.request("/api/auth/status", token=token))
        self.assertEqual(status["provider"], "cloudflare")
        self.assertTrue(status["authenticated"])
        self.assertEqual(self.request("/", token=token).status, 200)
        self.assertEqual(self.request("/app.js", token=token).status, 200)
        self.assertEqual(self.request("/api/state", token=token).status, 200)
        result = json.load(self.request("/api/action", token=token,
            body={"action": "income_source", "name": "Example source", "revision": 0}))
        self.assertEqual(result["revision"], 1)
        self.assertIn("Example source", json.load(self.request("/api/state", token=token))["state"]["income_sources"])

    def test_signed_owner_required_even_for_html_and_auth_status(self):
        for path in ("/", "/app.js", "/api/auth/status", "/api/state", "/api/export", "/api/backups"):
            self.denied(path)
            self.denied(path, extra={
                "Cf-Access-Authenticated-User-Email": "owner@example.com",
                "Cookie": "money_session=old-password-session",
                "X-Ingress-Path": "/api/hassio_ingress/not-trusted/",
            })
            self.denied(path, token="forged.jwt.assertion")
        self.server.sessions["old-password-session"] = time.time() + 3600
        self.denied("/api/state", extra={"Cookie": "money_session=old-password-session"})

    def test_refuses_wrong_user_audience_issuer_expiry_and_service_tokens(self):
        for token in (
            self.token(email="someone-else@example.com"),
            self.token(aud="audioshelf-audience"),
            self.token(iss="https://other.cloudflareaccess.com"),
            self.token(exp=int(time.time()) - 10),
            self.token(type="service"),
            self.token(common_name="service-token"),
            self.token(sub=""),
            self.token(email=""),
        ):
            self.denied("/api/state", token=token)
            self.denied("/api/action", token=token,
                body={"action": "income_source", "name": "Bad", "revision": 0})
        self.assertEqual(self.server.store.read()[0], 0)

    def test_old_password_login_and_logout_cannot_bypass_access(self):
        token = self.token()
        self.denied("/api/auth/login", code=403, token=token, body={"password": "old-password"})
        self.denied("/api/auth/logout", code=403, token=token, body={})
        self.denied("/api/auth/login", body={"password": "old-password"})

    def test_disabled_and_incomplete_options(self):
        self.assertFalse(CloudflareAccess({}).enabled)
        with self.assertRaises(ValueError):
            CloudflareAccess({"cloudflare_team_domain": "example.cloudflareaccess.com"})
        with self.assertRaises(ValueError):
            CloudflareAccess({
                "cloudflare_team_domain": "attacker.example",
                "cloudflare_audience": "aud", "cloudflare_owner_email": "owner@example.com"
            })


if __name__ == "__main__":
    unittest.main()
