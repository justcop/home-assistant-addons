from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from analytics.db import Database
from analytics.eras import analyse


TZ = ZoneInfo("Europe/London")


def plays(day, artist, count):
    stamp = int(datetime(day.year, day.month, day.day, 12, tzinfo=TZ).timestamp())
    return [
        {"ts":stamp + i, "title":"A song", "artist":artist,
         "album":"An album", "raw":{"name":"A song"}}
        for i in range(count)
    ]


def seed_history(db):
    rows = []
    for year in (2022, 2023, 2024):
        for month in range(1, 13):
            rows += plays(date(year, month, 15), "Old favourite", 35)
            rows += plays(date(year, month, 15), "Other", 35)
    for year in (2025, 2026):
        for month in range(1, 11):
            rows += plays(date(year, month, 5), "New favourite", 60)
            rows += plays(date(year, month, 5), "Other", 55)
    db.apply_window(0, int(datetime(2026, 10, 14, tzinfo=TZ).timestamp()), rows)
    db.set_meta("import", {"complete": True})


def test_365_day_rolling_history_and_neglect(tmp_path):
    db = Database(tmp_path / "eras.sqlite3")
    seed_history(db)
    now = datetime(2026, 10, 13, 17, tzinfo=TZ)
    current = analyse(db, {}, "Europe/London", now)
    assert current["complete_history"] and current["sufficient"]
    assert current["points"][-1]["date"] == "2026-10-13"
    old = next(a for a in current["artists"] if a["name"] == "Old favourite")
    assert old["current_share"] == 0
    assert old["peak_share"] > 40
    assert current["neglected"][0]["name"] == "Old favourite"
    assert current["neglected"][0]["favourite_years"] >= 2
    assert current["points"][-1]["shares"][old["id"]] == 0
    past = analyse(db, {"as_of":"2024-06-30", "artist":old["id"]}, "Europe/London", now)
    assert past["points"][-1]["date"] == "2024-06-30"
    assert past["artists"][0]["current_share"] > 35


def test_eras_require_full_window_and_completed_import(tmp_path):
    db = Database(tmp_path / "eras.sqlite3")
    seed_history(db)
    now = datetime(2026, 10, 13, 12, tzinfo=TZ)
    with pytest.raises(ValueError):
        analyse(db, {"as_of":"2028-01-01"}, "Europe/London", now)
    assert not analyse(db, {"as_of":"2022-10-01"}, "Europe/London", now)["sufficient"]
    db.set_meta("import", {"complete":False})
    assert analyse(db, {}, "Europe/London", now)["neglected"] == []
