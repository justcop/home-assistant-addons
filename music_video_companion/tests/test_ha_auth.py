"""Regression tests for Supervisor proxy auth and direct-Core fallback."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from providers import ApiHTTPError, HomeAssistant

class HomeAssistantAuthTests(unittest.TestCase):
    def test_proxy_token_used_before_fallback(self):
        with patch("providers.json_request", return_value=[]) as request:
            client = HomeAssistant(token="supervisor", alternate_token="core")
            self.assertEqual(client.states(), [])
            request.assert_called_once_with("http://supervisor/core/api/states", "supervisor", method="GET", data=None)

    def test_empty_supervisor_token_uses_direct_core(self):
        with patch("providers.json_request", return_value=[]) as request:
            client = HomeAssistant(token="", alternate_token="core")
            client.states()
            request.assert_called_once_with("http://homeassistant:8123/api/states", "core", method="GET", data=None)

    def test_401_uses_direct_core_and_not_supervisor(self):
        calls = []
        def fake_request(url, token, method="GET", data=None):
            calls.append((url, token))
            if url.startswith("http://supervisor/"):
                raise ApiHTTPError(401, "Unauthorized")
            return []
        with patch("providers.json_request", side_effect=fake_request):
            client = HomeAssistant(token="supervisor", alternate_token="core")
            self.assertEqual(client.states(), [])
        self.assertEqual(calls, [
            ("http://supervisor/core/api/states", "supervisor"),
            ("http://homeassistant:8123/api/states", "core")])

    def test_no_fallback_explains_problem(self):
        with patch("providers.json_request", side_effect=ApiHTTPError(401, "Unauthorized")):
            with self.assertRaisesRegex(RuntimeError, "Home Assistant refused"):
                HomeAssistant(token="bad").states()

    def test_missing_tokens_explains_problem(self):
        with self.assertRaisesRegex(RuntimeError, "Supervisor token missing"):
            HomeAssistant(token="", alternate_token="").states()

    def test_direct_401_explains_home_assistant_token(self):
        with patch("providers.json_request", side_effect=ApiHTTPError(401, "Unauthorized")):
            with self.assertRaisesRegex(RuntimeError, "Direct Home Assistant API"):
                HomeAssistant(token="", alternate_token="incorrect").states()
