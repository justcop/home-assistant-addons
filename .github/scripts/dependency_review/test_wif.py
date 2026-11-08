"""Mocked keyless GitHub OIDC -> OpenAI exchange tests. No real tokens used."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from review import github_oidc_access_token, _required_wif_settings

SETTINGS = {
    "OPENAI_IDENTITY_PROVIDER_ID": "wip_example",
    "OPENAI_SERVICE_ACCOUNT_ID": "svc_example",
    "OPENAI_WIF_AUDIENCE": "https://api.openai.com/v1",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/abc?id=1",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "github-runtime-request-token",
}


class StubResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return json.dumps(self.data).encode()


class WifTests(unittest.TestCase):
    def test_requires_all_configuration(self):
        for name in ("OPENAI_IDENTITY_PROVIDER_ID", "OPENAI_SERVICE_ACCOUNT_ID", "OPENAI_WIF_AUDIENCE"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    _required_wif_settings({k: v for k, v in SETTINGS.items() if k != name})
        with self.assertRaises(ValueError):
            _required_wif_settings({**SETTINGS, "OPENAI_WIF_AUDIENCE": "incorrect"})

    def test_rejects_unexpected_oidc_url(self):
        with self.assertRaises(ValueError):
            github_oidc_access_token({**SETTINGS, "ACTIONS_ID_TOKEN_REQUEST_URL": "https://evil.example/id"})

    def test_exact_token_exchange_payload(self):
        requests = []

        def urlopen(req, timeout=25):
            requests.append(req)
            if len(requests) == 1:
                return StubResponse({"value": "github-short-lived-jwt"})
            return StubResponse({"access_token": "openai-short-lived-access-token"})

        with patch("urllib.request.urlopen", side_effect=urlopen):
            token = github_oidc_access_token(SETTINGS)
        self.assertEqual(token, "openai-short-lived-access-token")
        self.assertIn("audience=https%3A%2F%2Fapi.openai.com%2Fv1", requests[0].full_url)
        self.assertEqual(requests[0].get_header("Authorization"), "bearer github-runtime-request-token")
        self.assertEqual(requests[1].full_url, "https://auth.openai.com/oauth/token")
        body = json.loads(requests[1].data)
        self.assertEqual(body["grant_type"], "urn:ietf:params:oauth:grant-type:token-exchange")
        self.assertEqual(body["subject_token_type"], "urn:ietf:params:oauth:token-type:jwt")
        self.assertEqual(body["subject_token"], "github-short-lived-jwt")
        self.assertEqual(body["identity_provider_id"], "wip_example")
        self.assertEqual(body["service_account_id"], "svc_example")

    def test_missing_github_token_fails_closed(self):
        with self.assertRaises(ValueError):
            github_oidc_access_token({k: v for k, v in SETTINGS.items() if k != "ACTIONS_ID_TOKEN_REQUEST_TOKEN"})

    def test_missing_openai_token_fails_closed(self):
        with patch("urllib.request.urlopen", side_effect=[
            StubResponse({"value": "jwt"}), StubResponse({})
        ]):
            with self.assertRaises(RuntimeError):
                github_oidc_access_token(SETTINGS)


if __name__ == "__main__":
    unittest.main()
