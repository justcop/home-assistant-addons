"""On-demand gig analytics. No long-lived copies of setlist.fm song data.

Live songs are read only from the expiring setlist cache. Listening counts are
calculated from the local scrobble database; attendance remains user owned.
"""
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .grouping import canonical_title, normalise


def song_key(title):
    return normalise(canonical_title(title, "song"))


def artist_identity(conn, artist):
    """Find the canonical scrobble identity for a supplied attendance artist."""
    key = normalise(artist)
    row = conn.execute(
        "SELECT canonical_key FROM artist_aliases WHERE artist_key=?",
        (key,),
    ).fetchone()
    return row[0] if row else key


def listening_summary(store, artist):
    """Return live-song overlap without treating missing setlists as negatives."""
    gigs = store.list(artist)
    if not gigs:
        return {"artist": artist, "performances": 0, "cached_setlists": 0,
                "live_songs": [], "not_in_cached_setlists": [],
                "top_listening": [], "coverage_note": "No attended performances recorded."}
    cached = [g for g in gigs if g["sets"] is not None]
    live = {}
    for gig in cached:
        seen = set()
        for part in gig["sets"] or []:
            if not isinstance(part, dict):
                continue
            for song in part.get("song", []) or []:
                if not isinstance(song, dict) or not song.get("name"):
                    continue
                name = str(song["name"])
                key = song_key(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                item = live.setdefault(key, {"title": name, "performances": 0,
                                              "cover": False})
                item["performances"] += 1
                item["cover"] = bool(item["cover"] or song.get("cover"))
    with store.db.connect() as conn:
        key = artist_identity(conn, artist)
        # Retain aliases and raw spelling variants without double-counting.
        tracks = conn.execute("""SELECT s.title AS title, COUNT(*) AS plays
            FROM scrobbles s WHERE s.active=1
            AND (s.artist_group_key=? OR (s.artist_group_key IS NULL
                 AND source_key(s.artist)=?))
            GROUP BY s.title ORDER BY plays DESC""", (key, normalise(artist))).fetchall()
    listens = {}
    for track in tracks:
        key = song_key(track["title"])
        if not key:
            continue
        item = listens.setdefault(key, {"title": track["title"], "plays": 0})
        item["plays"] += track["plays"]
        if track["plays"] > item.get("largest_variant", 0):
            item["title"] = track["title"]
            item["largest_variant"] = track["plays"]
    live_rows = [
        {**song, "listens": listens.get(k, {}).get("plays", 0)}
        for k, song in live.items()
    ]
    live_rows.sort(key=lambda s: (-s["listens"], -s["performances"], s["title"].casefold()))
    top = sorted(listens.items(), key=lambda t: (-t[1]["plays"], t[1]["title"].casefold()))
    return {
        "artist": artist,
        "performances": len(gigs),
        "cached_setlists": len(cached),
        "cached_songs": len(live),
        "live_songs": live_rows[:250],
        "not_in_cached_setlists": [
            {"title": item["title"], "listens": item["plays"]}
            for k, item in top if k not in live
        ][:25],
        "top_listening": [
            {"title": item["title"], "listens": item["plays"],
             "in_cached_setlist": k in live}
            for k, item in top[:25]
        ],
        "coverage_note": (
            f"{len(cached)} of {len(gigs)} attended setlists are currently cached. "
            "All setlists are unverified; songs not listed may still have been performed. "
            "Cache expiry can reduce coverage until the next scheduled refresh."
        ),
    }


def impact_for_artist(store, artist):
    """Equal 30-day pre/post windows, normalised for overall listening volume.

    Never present this as a causal effect. Dates without fully observable
    history or with an unfinished post-window are explicitly unavailable.
    """
    gigs = store.list(artist)
    if not gigs:
        return []
    zone = ZoneInfo(store.config["timezone"])
    with store.db.connect() as conn:
        key = artist_identity(conn, artist)
        limits = conn.execute(
            "SELECT MIN(ts) AS first, MAX(ts) AS last FROM scrobbles WHERE active=1"
        ).fetchone()
        if limits["first"] is None:
            return [{"id": g["id"], "date": g["date"], "available": False,
                     "reason": "No listening data"} for g in gigs]
        results = []
        now = datetime.now(zone).date()
        for gig in gigs:
            day = date.fromisoformat(gig["date"])
            # Pre: days -30 to -1; Post: days +1 to +30 (exclude gig day).
            start = int(datetime.combine(day - timedelta(days=30),
                     datetime.min.time(), tzinfo=zone).timestamp())
            mid = int(datetime.combine(day, datetime.min.time(), tzinfo=zone).timestamp())
            post_start = int(datetime.combine(day + timedelta(days=1),
                     datetime.min.time(), tzinfo=zone).timestamp())
            finish = int(datetime.combine(day + timedelta(days=31),
                     datetime.min.time(), tzinfo=zone).timestamp())
            base = {"id": gig["id"], "date": gig["date"], "available": False}
            if day + timedelta(days=31) > now:
                results.append({**base, "reason": "30-day post-gig period not complete"})
                continue
            if limits["first"] > start or limits["last"] < finish - 86400:
                results.append({**base, "reason": "Listening history does not span both periods"})
                continue
            row = conn.execute("""SELECT
                SUM(CASE WHEN ts>=? AND ts<? THEN 1 ELSE 0 END) AS total_before,
                SUM(CASE WHEN ts>=? AND ts<? THEN 1 ELSE 0 END) AS total_after,
                SUM(CASE WHEN ts>=? AND ts<? AND
                    (artist_group_key=? OR (artist_group_key IS NULL AND source_key(artist)=?))
                    THEN 1 ELSE 0 END) AS artist_before,
                SUM(CASE WHEN ts>=? AND ts<? AND
                    (artist_group_key=? OR (artist_group_key IS NULL AND source_key(artist)=?))
                    THEN 1 ELSE 0 END) AS artist_after
                FROM scrobbles WHERE active=1 AND ts>=? AND ts<?""",
                (start, mid, post_start, finish, start, mid, key,
                 normalise(artist), post_start, finish, key,
                 normalise(artist), start, finish)).fetchone()
            before = row["artist_before"] or 0
            after = row["artist_after"] or 0
            total_before = row["total_before"] or 0
            total_after = row["total_after"] or 0
            share_before = round(before / total_before * 100, 2) if total_before else None
            share_after = round(after / total_after * 100, 2) if total_after else None
            results.append({
                **base, "available": bool(total_before and total_after),
                "artist_before": before, "artist_after": after,
                "all_before": total_before, "all_after": total_after,
                "share_before": share_before, "share_after": share_after,
                "share_change_pp": round(share_after - share_before, 2)
                    if share_before is not None and share_after is not None else None,
                "reason": None if total_before and total_after else "No listening in one comparison window",
            })
    return results
