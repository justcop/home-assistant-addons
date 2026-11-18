"""Time-aligned listening history and attended concert/festival overview.

All event metadata comes from the user's attendance catalogue. This endpoint
does not use or persist cached setlist.fm responses.
"""
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .grouping import normalise


def next_month(day):
    year = day.year + (day.month == 12)
    return date(year, 1 if day.month == 12 else day.month + 1, 1)


def year_start(day):
    return date(day.year, 1, 1)


def week_start(day):
    return day - timedelta(days=day.weekday())


def period_start(day, scale):
    if scale == "year":
        return year_start(day)
    if scale == "month":
        return date(day.year, day.month, 1)
    if scale == "week":
        return week_start(day)
    return day


def period_end(day, scale):
    if scale == "year":
        return date(day.year + 1, 1, 1)
    if scale == "month":
        return next_month(day)
    if scale == "week":
        return day + timedelta(days=7)
    return day + timedelta(days=1)


def group_events(gigs):
    """Combine one festival across adjacent dates, or a single-day concert bill.

    Each attendance performance is still included, uniquely, within an event.
    Festivals are grouped by explicit festival name (across stages) or venue.
    Multiple concerts at the same venue on one day form one bill, not a festival.
    """
    groups = defaultdict(list)
    for gig in gigs:
        kind = gig["show_type"]
        label = gig["festival"].strip() if kind == "festival" else ""
        venue = gig["venue"].strip()
        if kind == "festival":
            key = ("festival", normalise(label) if label else normalise(venue),
                   "named" if label else "venue")
        elif kind == "concert":
            # If there's no venue, do not wrongly join two unrelated gigs.
            key = ("concert", normalise(venue) if venue else str(gig["id"]), "venue")
        else:
            key = ("unknown", str(gig["id"]), "unknown")
        groups[key].append(gig)

    output = []
    for (kind, _, _), members in groups.items():
        members.sort(key=lambda item: (item["date"], item["artist"].casefold(), item["id"]))
        chunk = []
        for gig in members:
            today = date.fromisoformat(gig["date"])
            previous = date.fromisoformat(chunk[-1]["date"]) if chunk else None
            if chunk and (kind != "festival" and today != previous or
                          kind == "festival" and (today - previous).days > 1):
                output.append(_event(chunk, kind))
                chunk = []
            chunk.append(gig)
        if chunk:
            output.append(_event(chunk, kind))
    output.sort(key=lambda item: (item["start"], item["type"], item["name"].casefold(), item["id"]))
    return output


def _event(gigs, kind):
    first = gigs[0]
    start = min(g["date"] for g in gigs)
    end = max(g["date"] for g in gigs)
    name = (first["festival"] if kind == "festival" and first["festival"] else
            first["venue"] or first["artist"])
    return {
        "id": min(g["id"] for g in gigs),
        "start": start, "end": end, "type": kind,
        "name": name, "venue": first["venue"],
        "performances": len(gigs),
        "artists": list(dict.fromkeys(g["artist"] for g in gigs)),
        "gigs": [
            {"id": g["id"], "date": g["date"], "artist": g["artist"],
             "venue": g["venue"], "url": g["setlist_url"]}
            for g in gigs
        ],
    }


def all_listening_timeline(store, scale="year", year=None):
    """All-time or single-year scrobbles and dated gig markers.

    Uses the configured local timezone, not SQLite UTC calendar buckets.
    A single indexed timestamp scan aggregates all plays without an
    expensive one-query-per-day plan.
    """
    if scale not in ("year", "month", "week", "day"):
        raise ValueError("Choose a valid timeline scale")
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Choose a valid calendar year") from None
        if not 1970 <= year <= datetime.now(timezone.utc).year:
            raise ValueError("Choose a valid calendar year")
    if scale == "day" and year is None:
        raise ValueError("Choose a year for the daily timeline")
    tz = ZoneInfo(store.config["timezone"])
    with store.db.connect() as conn:
        earliest, latest, total = conn.execute(
            "SELECT MIN(ts),MAX(ts),COUNT(*) FROM scrobbles WHERE active=1"
        ).fetchone()
        attended = [dict(row) for row in conn.execute(
            """SELECT id,artist,date,venue,festival,setlist_url,show_type
               FROM gigs ORDER BY date,id""")]
    dates = ([datetime.fromtimestamp(earliest, tz).date(),
              datetime.fromtimestamp(latest, tz).date()] if total else [])
    dates += [date.fromisoformat(gig["date"]) for gig in attended]
    if not dates:
        return {"scale": scale, "year": year, "years": [], "bins": [], "events": [],
                "total": 0, "performances": 0, "event_count": 0}
    now = datetime.now(tz).date()
    start = date(year, 1, 1) if year else min(dates)
    # All-time includes the current calendar period, not empty future years.
    finish = min(date(year + 1, 1, 1) - timedelta(days=1), now) if year else max(min(max(dates), now), min(dates))
    if finish < start:
        finish = start
    boundaries = []
    day = period_start(start, scale)
    while day <= finish:
        boundaries.append(day)
        day = period_end(day, scale)
        if len(boundaries) > 2000:
            raise ValueError("Too many timeline intervals; select a calendar year")
    boundaries.append(day)
    starts = [int(datetime.combine(d, datetime.min.time(), tzinfo=tz).timestamp())
              for d in boundaries]
    # The current UTC time may exceed the end of a selected historical period
    # but every event and scrobble is filtered to these same bucket boundaries.
    counters = [0] * (len(boundaries) - 1)
    if counters and earliest is not None:
        with store.db.connect() as conn:
            for row in conn.execute(
                "SELECT ts FROM scrobbles WHERE active=1 AND ts>=? AND ts<? ORDER BY ts",
                (starts[0], starts[-1])):
                index = bisect_right(starts, row[0]) - 1
                if 0 <= index < len(counters):
                    counters[index] += 1
    bins = [
        {"start": start_date.isoformat(),
         "end": (boundaries[i + 1] - timedelta(days=1)).isoformat(),
         "plays": counters[i]}
        for i, start_date in enumerate(boundaries[:-1])
    ]
    selected = [gig for gig in attended if start <= date.fromisoformat(gig["date"]) <= finish]
    events = group_events(selected)
    return {
        "scale": scale, "year": year,
        "years": list(range(min(dates).year, min(now.year, max(dates).year) + 1)),
        "bins": bins, "events": events,
        "total": sum(counters), "performances": len(selected),
        "event_count": len(events),
        "start": start.isoformat(), "end": finish.isoformat(),
        "timezone": str(tz),
    }
