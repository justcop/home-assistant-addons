import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from source_reporting import SourceReporter, submit_scrobble


def test_retry_restart_duplicate_and_acknowledgement(tmp_path):
    path = tmp_path / "pending.sqlite3"
    reporter = SourceReporter(path, "http://analyser:8099", "secret", "Justin", log=lambda _: None)
    row = {"source": "vinyl", "timestamp": 1000, "artist": "Beatles", "title": "Come Together"}
    reporter.enqueue(row); reporter.enqueue(row)
    assert reporter.flush(Mock(side_effect=TimeoutError)) is False
    reporter = SourceReporter(path, "http://analyser:8099", "secret", "Justin", log=lambda _: None)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 1
    # Neither a redirect nor an HTML login response acknowledges delivery.
    assert reporter.flush(Mock(return_value=SimpleNamespace(status_code=302))) is False
    post = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"ok": True}))
    assert reporter.flush(post)
    assert post.call_args.kwargs["json"]["username"] == "Justin"
    assert post.call_args.kwargs["allow_redirects"] is False
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0


def test_lastfm_acceptance_and_corrections():
    network = SimpleNamespace(api_key="key", api_secret="secret", session_key="session")
    payload = {"scrobbles": {"@attr": {"accepted": "1", "ignored": "0"},
        "scrobble": {"ignoredMessage": {"code": "0"},
                     "artist": {"#text": "The Beatles", "corrected": "1"},
                     "track": {"#text": "Come Together", "corrected": "0"}}}}
    response = Mock(); response.json.return_value = payload
    post = Mock(return_value=response)
    row = submit_scrobble(network, "Beatles", "Come Together", 1000, post=post)
    assert row == {"source": "vinyl", "timestamp": 1000, "artist": "The Beatles", "title": "Come Together"}
    assert post.call_args.kwargs["timeout"] == 15
    payload["scrobbles"]["@attr"]["accepted"] = "0"
    payload["scrobbles"]["scrobble"]["ignoredMessage"]["code"] = "3"
    assert submit_scrobble(network, "Beatles", "Come Together", 1000, post=post) is None
    response.json.return_value = {"error": 9}
    with pytest.raises(ValueError):
        submit_scrobble(network, "Beatles", "Come Together", 1000, post=post)


def test_reporter_rejects_credential_urls(tmp_path):
    for url in ("file:///tmp/secret", "http://user:password@host", "http://host?token=secret"):
        with pytest.raises(ValueError):
            SourceReporter(tmp_path / "pending.sqlite3", url, "token", "Justin")
