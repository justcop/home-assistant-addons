"""Regression tests for Listening's Cloudflare Access Google SSO mode."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from analytics.web import create_app, validate_config, ConfigurationError


OPTIONS = {
    "web_password": "a-password-that-used-to-work",
    "cloudflare_team_domain": "justcop.cloudflareaccess.com",
    "cloudflare_audience": "listening-audience",
    "cloudflare_owner_email": "owner@example.com",
}
EXTERNAL = {"REMOTE_ADDR": "192.0.2.10", "wsgi.url_scheme": "https"}
INGRESS = {"REMOTE_ADDR": "172.30.32.2"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app(tmp_path, config=OPTIONS, start_worker=False)
    app.config["TESTING"] = True
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    access = app.extensions["cloudflare_access"]
    monkeypatch.setattr(access.keys, "get_signing_key_from_jwt", lambda token: SimpleNamespace(key=private.public_key()))

    def signed(**changes):
        now = int(time.time())
        payload = {
            "iss": "https://justcop.cloudflareaccess.com",
            "aud": "listening-audience",
            "iat": now,
            "exp": now + 3600,
            "sub": "owner-google-subject",
            "email": "owner@example.com",
            "type": "app",
        }
        payload.update(changes)
        return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "test"})

    return app.test_client(), signed


def get(client, path, token=None):
    return client.get(
        path,
        environ_overrides=EXTERNAL,
        headers={"Cf-Access-Jwt-Assertion": token} if token else {},
    )


def test_valid_signed_jwt_grants_access_without_password(setup):
    client, signed = setup
    assert get(client, "/", signed()).status_code == 200
    assert get(client, "/api/status", signed()).status_code == 200
    assert get(client, "/manifest.webmanifest", signed()).status_code == 200
    assert get(client, "/login", signed()).status_code == 302
    assert get(client, "/login", signed()).location == "/"
    assert get(client, "/static/style.css", signed()).status_code == 200
    assert get(client, "/").status_code == 401
    assert get(client, "/api/status").status_code == 401
    assert get(client, "/manifest.webmanifest").status_code == 401
    assert get(client, "/static/style.css").status_code == 401


@pytest.mark.parametrize("payload", [
    {"aud": "audioshelf-audience"},
    {"iss": "https://untrusted.cloudflareaccess.com"},
    {"email": "other@example.com"},
    {"email": "owner@example.com.evil"},
    {"type": "service"},
    {"common_name": "machine"},
    {"sub": ""},
    {"exp": 1},
])
def test_invalid_identity_is_rejected(setup, payload):
    client, signed = setup
    assert get(client, "/api/status", signed(**payload)).status_code == 401


def test_invalid_signature_and_old_password_session_do_not_bypass(setup):
    client, signed = setup
    with client.session_transaction() as session:
        session["authenticated"] = True
    assert get(client, "/api/status").status_code == 401
    good = signed()
    tampered = good[:-2] + ("aa" if good[-2:] != "aa" else "bb")
    assert get(client, "/api/status", tampered).status_code == 401
    assert client.post("/login", environ_overrides=EXTERNAL,
                       headers={"Cf-Access-Jwt-Assertion": good},
                       data={"password": OPTIONS["web_password"]}).status_code == 403


def test_ingress_and_health_still_work_without_access_jwt(setup):
    client, _ = setup
    assert client.get("/health", environ_overrides=EXTERNAL).status_code == 200
    assert client.get("/api/status", environ_overrides=INGRESS).status_code == 200


def test_incomplete_cloudflare_configuration_fails_closed():
    for key in ("cloudflare_team_domain", "cloudflare_audience", "cloudflare_owner_email"):
        invalid = {**OPTIONS, key: ""}
        with pytest.raises(ConfigurationError):
            validate_config(invalid)
