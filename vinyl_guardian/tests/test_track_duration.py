import ast
import math
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
                 and node.name in ("get_track_duration", "_duration_from_rows")]
        self.get = Mock()
        self.logs = []
        self.env = dict(math=math, identity_key=identity_key, _duration_cache={},
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


if __name__ == "__main__":
    unittest.main()
