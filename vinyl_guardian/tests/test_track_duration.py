import ast
import math
import json
import re
import threading
from types import SimpleNamespace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from track_reasoning import identity_key


class RequestException(Exception):
    pass


class Timeout(RequestException):
    pass


def row(title="Fluorescent Adolescent", artist="Arctic Monkeys", duration=183000,
        album="Favourite Worst Nightmare"):
    return dict(kind="song", trackName=title, artistName=artist,
                trackTimeMillis=duration, collectionName=album)


def response(rows):
    result = Mock()
    result.json.return_value = {"resultCount": len(rows), "results": rows}
    return result


class DurationTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "integrations.py").read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("get_track_duration", "_duration_from_rows", "_lastfm_track_duration",
                                   "_musicbrainz_track_duration", "_musicbrainz_duration_rows")]
        self.get = Mock()
        self.logs = []
        self.clock = [0.0]
        self.sleep = Mock(side_effect=lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds))
        self.env = dict(math=math, json=json, re=re, identity_key=identity_key, _duration_cache={},
                        LFM_KEY="", _musicbrainz_lock=threading.Lock(), _musicbrainz_next_call=0.0,
                        _musicbrainz_backoff_until=0.0,
                        time=SimpleNamespace(monotonic=lambda: self.clock[0], sleep=self.sleep),
                        requests=Mock(get=self.get, RequestException=RequestException),
                        log=self.logs.append)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "duration", "exec"), self.env)

    def lookup(self, adamid="missing", album="Favourite Worst Nightmare"):
        return self.env["get_track_duration"]("Fluorescent Adolescent", "Arctic Monkeys", adamid, album)

    def test_empty_id_lookup_falls_back_to_search_and_caches_success(self):
        self.get.side_effect = [response([]), response([]), response([row()])]
        self.assertEqual(self.lookup(), 183)
        calls = self.get.call_args_list
        self.assertEqual([c.kwargs["params"]["country"] for c in calls], ["GB", "US", "GB"])
        self.assertTrue(calls[-1].args[0].endswith("/search"))
        self.assertEqual(calls[-1].kwargs["params"]["limit"], 25)
        self.assertEqual(self.lookup(), 183)
        self.assertEqual(self.get.call_count, 3)

    def test_missing_duration_on_matching_id_also_uses_search(self):
        self.get.side_effect = [response([row(duration=0)]), response([row(duration=0)]), response([row()])]
        self.assertEqual(self.lookup(), 183)

    def test_no_id_search_validates_all_results_instead_of_taking_first(self):
        self.get.return_value = response([row(artist="Cover Artist", duration=999000),
                                         row(title="Fluorescent Adolescent (Live)", duration=250000), row()])
        self.assertEqual(self.lookup(adamid=None), 183)
        self.assertEqual(self.get.call_count, 1)

    def test_album_match_wins_over_other_release_with_different_duration(self):
        self.get.return_value = response([row(duration=220000, album="Compilation"), row()])
        self.assertEqual(self.lookup(adamid=None), 183)

    def test_network_error_still_reaches_search_and_logs_reason(self):
        self.get.side_effect = [Timeout(), response([]), response([row()])]
        self.assertEqual(self.lookup(), 183)
        self.assertTrue(any("Timeout" in line for line in self.logs))

    def test_ambiguous_or_invalid_lengths_remain_unknown_and_failure_is_not_cached(self):
        self.get.return_value = response([row(duration=183000, album="Compilation"),
                                         row(duration=230000, album="Other"),
                                         row(duration="bad"), row(duration=float("inf"))])
        self.assertEqual(self.lookup(adamid=None, album=None), 0)
        self.assertEqual(self.env["_duration_cache"], {})
        self.assertIn("using periodic Shazam checks", self.logs[-1])
        self.get.return_value = response([row()])
        self.assertEqual(self.lookup(adamid=None, album=None), 183)

    def test_valid_id_needs_no_search(self):
        self.get.return_value = response([row()])
        self.assertEqual(self.lookup(), 183)
        self.assertEqual(self.get.call_count, 1)

    def test_lastfm_uses_existing_key_after_apple_and_caches_validated_duration(self):
        self.env["LFM_KEY"] = "secret-test-key"
        lastfm = response([])
        lastfm.json.return_value = {"track": {"name": "Fluorescent Adolescent",
            "artist": {"name": "Arctic Monkeys"}, "duration": "183000"}}
        self.get.side_effect = [response([])] * 4 + [lastfm]
        self.assertEqual(self.lookup(), 183)
        self.assertEqual(self.get.call_count, 5)
        call = self.get.call_args
        self.assertEqual(call.kwargs["params"]["api_key"], "secret-test-key")
        self.assertEqual(call.kwargs["params"]["method"], "track.getInfo")
        self.assertFalse(any("secret-test-key" in line for line in self.logs))
        self.assertEqual(self.lookup(), 183)
        self.assertEqual(self.get.call_count, 5)

    def musicbrainz_response(self, title="Fluorescent Adolescent", duration=183000, **extra):
        result = response([])
        result.json.return_value = {"recordings": [{"score": 100, "title": title,
            "length": duration, "artist-credit": [{"artist": {"name": "Arctic Monkeys"}}], **extra}]}
        return result

    def test_musicbrainz_is_used_when_lastfm_identity_is_wrong(self):
        self.env["LFM_KEY"] = "test"
        lastfm = response([])
        lastfm.json.return_value = {"track": {"name": "Fluorescent Adolescent (Live)",
            "artist": {"name": "Arctic Monkeys"}, "duration": "250000"}}
        self.get.side_effect = [response([])] * 4 + [lastfm, self.musicbrainz_response()]
        self.assertEqual(self.lookup(), 183)
        self.assertTrue(self.get.call_args.args[0].startswith("https://musicbrainz.org/"))
        self.assertIn("User-Agent", self.get.call_args.kwargs["headers"])

    def test_missing_lastfm_key_skips_lastfm_and_reaches_musicbrainz(self):
        self.get.side_effect = [response([])] * 4 + [self.musicbrainz_response()]
        self.assertEqual(self.lookup(), 183)
        self.assertEqual(self.get.call_count, 5)

    def test_musicbrainz_album_track_length_can_fill_missing_recording_length(self):
        result = self.musicbrainz_response(duration=None, releases=[{
            "title": "Favourite Worst Nightmare", "media": [{"track": [{
                "title": "Fluorescent Adolescent", "length": 183000}]}]}])
        self.get.return_value = result
        self.assertEqual(self.env["_musicbrainz_track_duration"](
            "Fluorescent Adolescent", "Arctic Monkeys", "Favourite Worst Nightmare"), 183)

    def test_musicbrainz_rejects_low_confidence_live_and_ambiguous_recordings(self):
        for extra in ({"score": 70}, {"disambiguation": "live at Glastonbury"}):
            self.get.return_value = self.musicbrainz_response(**extra)
            self.assertEqual(self.env["_musicbrainz_track_duration"](
                "Fluorescent Adolescent", "Arctic Monkeys"), 0)
        result = self.musicbrainz_response()
        result.json.return_value["recordings"].append({"score": 100, "title": "Fluorescent Adolescent",
            "length": 240000, "artist-credit": [{"artist": {"name": "Arctic Monkeys"}}]})
        self.get.return_value = result
        self.assertEqual(self.env["_musicbrainz_track_duration"](
            "Fluorescent Adolescent", "Arctic Monkeys"), 0)

    def test_musicbrainz_requests_are_spaced_and_429_backs_off_without_sleeping_a_minute(self):
        lookup = self.env["_musicbrainz_track_duration"]
        self.get.return_value = self.musicbrainz_response()
        self.assertEqual(lookup("Fluorescent Adolescent", "Arctic Monkeys"), 183)
        self.assertEqual(lookup("Fluorescent Adolescent", "Arctic Monkeys"), 183)
        self.sleep.assert_called_once_with(1.1)
        self.get.return_value.status_code = 429
        self.assertEqual(lookup("Fluorescent Adolescent", "Arctic Monkeys"), 0)
        count = self.get.call_count
        self.assertEqual(lookup("Fluorescent Adolescent", "Arctic Monkeys"), 0)
        self.assertEqual(self.get.call_count, count)
        self.assertTrue(all(call.args[0] <= 1.1 for call in self.sleep.call_args_list))

    def test_both_provider_failures_preserve_unknown_duration_fallback_without_secrets(self):
        self.env["LFM_KEY"] = "secret-test-key"
        self.get.side_effect = Timeout("https://example/?api_key=secret-test-key")
        self.assertEqual(self.lookup(), 0)
        self.assertEqual(self.env["_duration_cache"], {})
        self.assertIn("using periodic Shazam checks", self.logs[-1])
        self.assertFalse(any("secret-test-key" in line for line in self.logs))

    def test_logs_distinguish_missing_lengths_mismatches_and_ambiguity(self):
        choose=self.env["_duration_from_rows"]
        for rows, expected in [([],"no results returned"),([row(artist="Other")],"artist/title mismatches"),
                               ([row(duration=0)],"missing, zero or invalid length"),
                               ([row(duration=183000),row(duration=230000)],"183.0s to 230.0s")]:
            self.assertEqual(choose(rows,"Fluorescent Adolescent","Arctic Monkeys",provider="Apple search GB"),0)
            self.assertIn(expected,self.logs[-1])
            self.assertIn("Apple search GB",self.logs[-1])

    def test_lastfm_skipped_key_and_api_error_are_explicit(self):
        lookup=self.env["_lastfm_track_duration"]
        self.assertEqual(lookup("Song","Artist"),0)
        self.assertIn("no API key configured",self.logs[-1])
        self.env["LFM_KEY"]="private-key"
        result=response([])
        result.json.return_value={"error":6,"message":"private-key should not appear"}
        self.get.return_value=result
        self.assertEqual(lookup("Song","Artist"),0)
        self.assertIn("API error code 6",self.logs[-1])
        self.assertNotIn("private-key"," ".join(self.logs))

    def test_http_status_is_logged_without_exception_url(self):
        result=response([])
        result.status_code=503
        result.raise_for_status.side_effect=RequestException("https://private-key")
        self.get.return_value=result
        self.assertEqual(self.lookup(),0)
        self.assertTrue(any("HTTP 503" in line for line in self.logs))
        self.assertNotIn("private-key"," ".join(self.logs))

    def test_musicbrainz_logs_rejected_scores_and_cooldown(self):
        result=response([])
        result.status_code=200
        result.json.return_value={"recordings":[{"score":70,"title":"Song"}]}
        self.get.return_value=result
        self.assertEqual(self.env["_musicbrainz_track_duration"]("Song","Artist"),0)
        self.assertIn("below 95% search score",self.logs[-1])
        self.env["_musicbrainz_backoff_until"]=60
        self.assertEqual(self.env["_musicbrainz_track_duration"]("Song","Artist"),0)
        self.assertIn("cooldown",self.logs[-1])


if __name__ == "__main__":
    unittest.main()
