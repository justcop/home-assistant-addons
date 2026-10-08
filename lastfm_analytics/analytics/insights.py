"""Exact play-count analytics; no fabricated duration or session estimates."""

import json
from .artwork import image_url

from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


JOINS = """FROM scrobbles s
 JOIN resolved_variants sv ON sv.id=s.song_id
 JOIN groups sg ON sg.id=sv.group_id
 LEFT JOIN resolved_variants av ON av.id=s.album_id
 LEFT JOIN groups ag ON ag.id=av.group_id
"""


def period(args, tz, now=None, earliest=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    end = int(now.timestamp()) + 1
    name = args.get("period", "all")
    if name == "custom":
        try:
            start_dt = datetime.strptime(args["start"], "%Y-%m-%d").replace(tzinfo=tz)
            end_dt = datetime.strptime(args["end"], "%Y-%m-%d").replace(
                tzinfo=tz
            ) + timedelta(days=1)
            start = int(start_dt.timestamp())
            end = min(end, int(end_dt.timestamp()))
        except (KeyError, ValueError):
            raise ValueError("Choose valid start and end dates") from None
    elif name.startswith("year:"):
        try:
            year = int(name[5:])
            if not 1970 <= year <= local.year:
                raise ValueError
            start = int(datetime(year, 1, 1, tzinfo=tz).timestamp())
            end = min(end, int(datetime(year + 1, 1, 1, tzinfo=tz).timestamp()))
        except ValueError:
            raise ValueError("Choose a valid calendar year") from None
    elif name == "all":
        start = (
            earliest
            if earliest is not None
            else int(
                (local - timedelta(days=29))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
        )
    elif name in ("7d", "30d", "90d", "365d"):
        days = int(name[:-1])
        start = int(
            (local - timedelta(days=days - 1))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
    else:
        raise ValueError("Unknown date period")
    if start < 0 or end <= start:
        raise ValueError(
            "Date range must start before it ends and cannot be in the future"
        )
    return {
        "start": start,
        "end": end,
        "previous_start": (
            max(0, int(datetime(year - 1, 1, 1, tzinfo=tz).timestamp()))
            if name.startswith("year:") and year > 1970
            else max(0, start - (end - start))
        ),
        # An incomplete calendar year should not be compared with a full year.
        "compare": name != "all"
        and not (name.startswith("year:") and year == local.year),
        "name": name,
        "timezone": str(tz),
        "start_label": datetime.fromtimestamp(start, tz).strftime("%d %b %Y"),
        "end_label": datetime.fromtimestamp(end - 1, tz).strftime("%d %b %Y"),
    }


def source_scope(args):
    source = args.get("source", "all")
    if source == "all":
        return "", []
    if source not in ("vinyl", "unknown"):
        raise ValueError("Unknown listening source")
    exists = """EXISTS (SELECT 1 FROM source_reports sr
        WHERE sr.ts=s.ts AND sr.artist_key=s.artist_key
        AND sr.title_key=source_key(s.title) AND sr.source='vinyl')"""
    return " AND " + ("NOT " if source == "unknown" else "") + exists, []


def scope(args):
    extra, params = source_scope(args)
    entity_extra, entity_params = entity_scope(args)
    return extra + entity_extra, params + entity_params


def entity_scope(args):
    kind, value = args.get("entity"), args.get("id")
    raw = args.get("mode") == "raw"
    if not kind:
        return "", []
    if kind == "artist":
        return " AND s.artist_group_key=?", [value]
    if kind in ("song", "album"):
        col = ("sv" if kind == "song" else "av") + (".id" if raw else ".group_id")
        try:
            return f" AND {col}=?", [int(value)]
        except (ValueError, TypeError):
            pass
    raise ValueError("Invalid history filter")


def summaries(conn, p, extra, params, raw):
    song = "sv.id" if raw else "sv.group_id"
    album = "av.id" if raw else "av.group_id"
    sql = f"""SELECT COUNT(*) AS plays, COUNT(DISTINCT s.artist_group_key) AS artists,
       COUNT(DISTINCT {song}) AS songs, COUNT(DISTINCT {album}) AS albums
       {JOINS} WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}"""
    current = dict(conn.execute(sql, [p["start"], p["end"], *params]).fetchone())
    previous = (
        dict(conn.execute(sql, [p["previous_start"], p["start"], *params]).fetchone())
        if p["compare"]
        else None
    )
    return current, previous


def rankings(
    conn, p, kind, raw=False, search="", limit=50, offset=0, extra="", params=()
):
    if kind == "artist":
        key, name, artist = "s.artist_group_key", "COALESCE((SELECT display_name FROM artist_aliases WHERE artist_key=s.artist_group_key),MIN(s.artist))", "''"
        condition, variants = "", "1"
    elif kind == "song":
        key, name, artist = (
            ("sv.id", "sv.name", "sv.artist")
            if raw
            else ("sv.group_id", "sg.name", "sg.artist")
        )
        condition, variants = "", "COUNT(DISTINCT sv.id)"
    elif kind == "album":
        key, name, artist = (
            ("av.id", "av.name", "av.artist")
            if raw
            else ("av.group_id", "ag.name", "ag.artist")
        )
        condition, variants = " AND s.album_id IS NOT NULL", "COUNT(DISTINCT av.id)"
    else:
        raise ValueError("Unknown ranking")
    joins = "FROM scrobbles s" if kind == "artist" else JOINS
    query = f"""WITH ranked AS (
      SELECT {key} AS id, {name} AS name, {artist} AS artist,
       SUM(CASE WHEN s.ts>=? THEN 1 ELSE 0 END) AS plays,
       SUM(CASE WHEN s.ts<? THEN 1 ELSE 0 END) AS previous,
       {variants} AS versions, MAX(s.ts) AS last_play
       {joins} WHERE s.active=1 AND s.ts>=? AND s.ts<? {condition} {extra}
       GROUP BY {key}) SELECT *, COUNT(*) OVER() AS total_rows FROM ranked
       WHERE plays>0 AND (instr(lower(name),lower(?))>0 OR instr(lower(artist),lower(?))>0)
       ORDER BY plays DESC, name COLLATE NOCASE, id LIMIT ? OFFSET ?"""
    rows = [
        dict(r)
        for r in conn.execute(
            query,
            [
                p["start"],
                p["start"],
                p["previous_start"] if p["compare"] else p["start"],
                p["end"],
                *params,
                search,
                search,
                limit,
                offset,
            ],
        )
    ]
    for r in rows:
        if not p["compare"]:
            r["previous"] = None
    return rows


def timeline(conn, p, tz, extra="", params=()):
    monthly = p["end"] - p["start"] > 370 * 86400
    fmt = "%Y-%m" if monthly else "%Y-%m-%d"
    counts, hours = Counter(), [[0] * 24 for _ in range(7)]
    joins = JOINS if "sv." in extra or "av." in extra else "FROM scrobbles s"
    # UK transitions occur at UTC whole-hour boundaries. Grouping by UTC
    # hour in SQLite is dramatically cheaper than materialising every play,
    # while mapping each bucket back to local time preserves both DST hours.
    # Other zones retain the exact per-play implementation.
    if str(tz) in ("Europe/London", "UTC", "Etc/UTC"):
        stream = conn.execute(
            f"""SELECT (s.ts / 3600) * 3600 AS bucket, COUNT(*) AS plays
            {joins} WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}
            GROUP BY bucket""", [p["start"], p["end"], *params],
        )
    else:
        stream = ((r[0], 1) for r in conn.execute(
            f"SELECT s.ts {joins} WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}",
            [p["start"], p["end"], *params],
        ))
    for ts, plays in stream:
        local = datetime.fromtimestamp(ts, tz)
        counts[local.strftime(fmt)] += plays
        hours[local.weekday()][local.hour] += plays
    point = datetime.fromtimestamp(p["start"], tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if monthly:
        point = point.replace(day=1)
    bins = []
    while point.timestamp() < p["end"]:
        if monthly:
            following = (point.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            following = point + timedelta(days=1)
        bins.append(
            {
                "label": point.strftime(fmt),
                "plays": counts[point.strftime(fmt)],
                "start": point.strftime("%Y-%m-%d"),
                "end": (following - timedelta(days=1)).strftime("%Y-%m-%d"),
            }
        )
        point = following
    return bins, hours


def overview(database, args, tz_name, now=None):
    tz = ZoneInfo(tz_name)
    with database.connect() as conn:
        conn.execute("BEGIN")
        earliest = conn.execute(
            "SELECT MIN(ts) FROM scrobbles WHERE active=1"
        ).fetchone()[0]
        p = period(args, tz, now, earliest)
        complete = bool(database.get(conn, "import", {}).get("complete"))
        p["compare"] = p["compare"] and complete
        extra, params = scope(args)
        raw = args.get("mode") == "raw"
        current, previous = summaries(conn, p, extra, params, raw)
        bins, hours = timeline(conn, p, tz, extra, params)
        artists = rankings(conn, p, "artist", limit=5, extra=extra, params=params)
        top_share = (
            round(100 * sum(a["plays"] for a in artists) / current["plays"], 1)
            if current["plays"]
            else 0
        )
        source_extra, _ = source_scope(args)
        # First heard is evaluated against the selected source across all history.
        discovery = conn.execute(
            f"""WITH firsts AS (
          SELECT s.artist_group_key, MIN(s.ts) first_ts FROM scrobbles s WHERE s.active=1 {source_extra} GROUP BY s.artist_group_key)
          SELECT COUNT(DISTINCT s.artist_group_key), COUNT(*) FROM scrobbles s
          JOIN firsts f ON f.artist_group_key=s.artist_group_key
          WHERE s.active=1 AND s.ts>=? AND s.ts<? AND f.first_ts>=? {extra}""",
            [p["start"], p["end"], p["start"], *params],
        ).fetchone()
        returns = [
            dict(r)
            for r in conn.execute(
                f"""WITH prior AS (
          SELECT s.artist_group_key, MAX(s.ts) last_ts FROM scrobbles s WHERE s.active=1 AND s.ts<? {source_extra} GROUP BY s.artist_group_key)
          SELECT s.artist_group_key AS id,
          COALESCE((SELECT display_name FROM artist_aliases WHERE artist_key=s.artist_group_key),MIN(s.artist)) AS name,
          COUNT(*) AS plays,
          (MIN(s.ts)-prior.last_ts)/86400 AS gap_days FROM scrobbles s
          JOIN prior ON prior.artist_group_key=s.artist_group_key
          WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}
          GROUP BY s.artist_group_key HAVING MIN(s.ts)-prior.last_ts>=7776000
          ORDER BY gap_days DESC LIMIT 5""",
                [p["start"], p["start"], p["end"], *params],
            )
        ]
        impact = conn.execute(
            """SELECT COUNT(DISTINCT sv.id), COUNT(DISTINCT sv.group_id),
          COUNT(DISTINCT av.id),COUNT(DISTINCT av.group_id) """
            + JOINS
            + f""" WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}""",
            [p["start"], p["end"], *params],
        ).fetchone()
        return {
            "period": p,
            "current": current,
            "previous": previous,
            "timeline": bins,
            "hours": hours,
            "top_artists": artists,
            "top_albums": rankings(
                conn, p, "album", raw, limit=5, extra=extra, params=params
            ),
            "top_songs": rankings(
                conn, p, "song", raw, limit=5, extra=extra, params=params
            ),
            "discovery": {
                "complete": complete,
                "artists": discovery[0] if complete else None,
                "plays": discovery[1] if complete else None,
            },
            "returning": returns,
            "top_five_share": top_share,
            "grouping": {
                "raw_songs": impact[0],
                "songs": impact[1],
                "raw_albums": impact[2],
                "albums": impact[3],
            },
        }


def history(conn, p, args, tz):
    extra, params = scope(args)
    search = args.get("q", "").strip()[:200]
    offset = max(0, int(args.get("offset", 0)))
    where = f"""WHERE s.active=1 AND s.ts>=? AND s.ts<? {extra}
      AND (instr(lower(s.artist),lower(?))>0 OR instr(lower(s.title),lower(?))>0 OR instr(lower(s.album),lower(?))>0)"""
    values = [p["start"], p["end"], *params, search, search, search]
    total = conn.execute("SELECT COUNT(*) " + JOINS + where, values).fetchone()[0]
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT s.id,s.ts,s.artist,s.title,s.album,
       CASE WHEN EXISTS (SELECT 1 FROM source_reports sr WHERE sr.ts=s.ts
         AND sr.artist_key=s.artist_key AND sr.title_key=source_key(s.title)
         AND sr.source='vinyl') THEN 'vinyl' ELSE 'unknown' END AS source,
       sv.group_id AS song_group,av.group_id AS album_group,s.song_id,s.album_id,
       sg.name AS song_name,ag.name AS album_name,s.artist_group_key AS artist_key """
            + JOINS
            + where
            + " ORDER BY s.ts DESC,s.id DESC LIMIT 50 OFFSET ?",
            [*values, offset],
        )
    ]
    for r in rows:
        r["local_time"] = datetime.fromtimestamp(r["ts"], tz).isoformat()
    return {"rows": rows, "total": total, "offset": offset}


def artwork_albums(conn, kind, value, raw, args):
    extra, params = source_scope(args or {})
    if kind == "artist":
        scope, values = "s.artist_group_key=?", (value,)
    elif kind in ("song", "album"):
        variant = "sv" if kind == "song" else "av"
        column = "id" if raw else "group_id"
        scope, values = f"{variant}.{column}=?", (int(value),)
    else:
        raise ValueError("Unknown detail type")
    albums = conn.execute(
        f"SELECT s.artist,s.album,COUNT(*) AS plays {JOINS} "
        f"WHERE s.active=1 AND {scope} {extra} AND s.album<>'' "
        "GROUP BY s.artist,s.album ORDER BY plays DESC,s.album LIMIT 10",
        (*values, *params),
    ).fetchall()
    return albums, scope, values, extra, params


def cover_art(conn, kind, value, raw, args):
    """Search distinct stored image metadata throughout the selected history."""
    albums, scope, values, extra, params = artwork_albums(conn, kind, value, raw, args)
    for album in albums:
        rows = conn.execute(
            f"SELECT DISTINCT json_extract(CASE WHEN json_valid(s.raw_json) "
            f"THEN s.raw_json ELSE '{{}}' END,'$.image') AS images {JOINS} "
            f"WHERE s.active=1 AND {scope} {extra} AND s.artist=? AND s.album=? "
            "AND images IS NOT NULL",
            (*values, *params, album["artist"], album["album"]),
        )
        for row in rows:
            try:
                url = image_url(json.loads(row[0]))
                if url:
                    return dict(url=url, album=album["album"],
                                artist=album["artist"], source="Last.fm")
            except (ValueError, TypeError):
                continue
    return None


def details(conn, kind, value, raw, args=None):
    source_extra, source_params = source_scope(args or {})
    if kind == "artist":
        name = conn.execute(
            """SELECT COALESCE(
                (SELECT display_name FROM artist_aliases WHERE artist_key=?),
                (SELECT artist FROM scrobbles WHERE artist_group_key=? LIMIT 1))""",
                (value, value)
        ).fetchone()
        if not name:
            raise ValueError("Artist not found")
        return {
            "name": name[0],
            "artist": "",
            "versions": [],
            "artwork": cover_art(conn, kind, value, raw, args),
        }
    if kind not in ("song", "album"):
        raise ValueError("Unknown detail type")
    value = int(value)
    column = "v.id" if raw else "v.group_id"
    fk = "song_id" if kind == "song" else "album_id"
    versions = [
        dict(r)
        for r in conn.execute(
            f"""SELECT v.id,v.name,v.artist,v.group_id,
      v.override_group IS NOT NULL AS manual,COUNT(s.id) AS plays,MIN(s.ts) AS first_play,MAX(s.ts) AS last_play
      FROM resolved_variants v LEFT JOIN scrobbles s ON s.{fk}=v.id AND s.active=1 {source_extra}
      WHERE v.kind=? AND {column}=? GROUP BY v.id ORDER BY plays DESC,v.name""",
            (*source_params, kind, value),
        )
    ]
    if not versions:
        raise ValueError("Entry not found")
    group = conn.execute(
        "SELECT * FROM groups WHERE id=?", (versions[0]["group_id"],)
    ).fetchone()
    return {
        "name": versions[0]["name"] if raw else group["name"],
        "artist": group["artist"],
        "versions": versions,
        "artwork": cover_art(conn, kind, value, raw, args),
    }
