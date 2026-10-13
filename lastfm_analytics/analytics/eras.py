"""Daily rolling 365-day artist listening shares and neglected favourites.

Windows contain exactly 365 local calendar days, including the selected day.
"""
from collections import Counter, defaultdict, deque
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .insights import source_scope

WINDOW_DAYS = 365
MIN_WINDOW_PLAYS = 300


def analyse(db, args, timezone, now=None):
    tz = ZoneInfo(timezone)
    now = now or datetime.now(tz)
    today = now.astimezone(tz).date()
    supplied = args.get("as_of") or today.isoformat()
    try:
        as_of = date.fromisoformat(supplied)
        if supplied != as_of.isoformat() or as_of > today:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Choose a valid date no later than today") from None
    artist_id = args.get("artist")
    if artist_id is not None and (not isinstance(artist_id, str) or len(artist_id) > 256):
        raise ValueError("Invalid artist")
    extra, params = source_scope(args)
    cutoff = int(datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=tz).timestamp())
    daily = defaultdict(Counter)
    yearly = defaultdict(Counter)
    names = {}
    lifetime = Counter()
    last_played = {}
    with db.connect() as conn:
        complete = bool(db.get(conn, "import", {}).get("complete"))
        for row in conn.execute(
            f"""SELECT s.ts,s.artist_group_key,s.artist FROM scrobbles s
                WHERE s.active=1 AND s.ts<? {extra} ORDER BY s.ts""",
            (cutoff, *params),
        ):
            day = datetime.fromtimestamp(row["ts"], tz).date()
            key = row["artist_group_key"]
            daily[day][key] += 1
            yearly[day.year][key] += 1
            lifetime[key] += 1
            names.setdefault(key, row["artist"])
            last_played[key] = day
        if names:
            names.update({
                row["artist_key"]: row["display_name"]
                for row in conn.execute("SELECT artist_key,display_name FROM artist_aliases")
                if row["artist_key"] in names
            })
    result = dict(as_of=as_of.isoformat(), first_complete=None,
                  complete_history=complete, sufficient=False,
                  points=[], artists=[], suggested=[], neglected=[],
                  artist=artist_id, window_days=WINDOW_DAYS, current_total=0)
    if not daily:
        return result
    first = min(daily)
    first_full = first + timedelta(days=WINDOW_DAYS - 1)
    result["first_complete"] = first_full.isoformat()
    if as_of < first_full:
        return result
    window = Counter()
    recent_days = deque()
    total = 0
    peaks = {}
    samples = []
    current = Counter()
    day = first
    while day <= as_of:
        bucket = daily.get(day, Counter())
        recent_days.append(bucket)
        window.update(bucket)
        total += sum(bucket.values())
        if len(recent_days) > WINDOW_DAYS:
            old = recent_days.popleft()
            window.subtract(old)
            total -= sum(old.values())
            for key in old:
                if window[key] <= 0:
                    window.pop(key, None)
        if day >= first_full:
            if total >= MIN_WINDOW_PLAYS:
                for key, plays in window.items():
                    share = 100.0 * plays / total
                    if share > peaks.get(key, (0, None))[0]:
                        peaks[key] = (share, day.isoformat())
            if (day + timedelta(days=1)).month != day.month or day == as_of:
                samples.append((day.isoformat(), total, dict(window)))
        if day == as_of:
            current = window.copy()
            current_total = total
        day += timedelta(days=1)

    # Require two complete, substantially listened-to calendar years in which
    # the artist independently ranked among the top 20, not one brief spike.
    top_years = defaultdict(list)
    if complete:
        for year, counts in yearly.items():
            if year >= as_of.year or (year == first.year and first > date(year, 1, 1)):
                continue
            whole_year = sum(counts.values())
            if whole_year < MIN_WINDOW_PLAYS:
                continue
            for key, plays in counts.most_common(20):
                if plays >= 20:
                    top_years[key].append((year, 100 * plays / whole_year))
    neglected = []
    if complete and current_total >= MIN_WINDOW_PLAYS:
        for key, years in top_years.items():
            if len(years) < 2 or lifetime[key] < 120:
                continue
            baseline = sum(share for _, share in years) / len(years)
            current_share = 100 * current[key] / current_total
            gap = (as_of - last_played[key]).days
            if baseline < 1.5 or current_share > baseline * .25 or gap < 60:
                continue
            neglected.append(dict(
                id=key, name=names[key], current_plays=current[key],
                current_share=round(current_share, 2), baseline_share=round(baseline, 2),
                favourite_years=len(years),
                last_played=last_played[key].isoformat(),
                historical_plays=lifetime[key],
                score=baseline * min(len(years), 5) * (1 - current_share / baseline),
            ))
        neglected.sort(key=lambda a: (-a["score"], -a["favourite_years"], a["name"].casefold()))
    current_top = [key for key, _ in current.most_common(8)]
    historic_top = [key for key, _ in lifetime.most_common(12)]
    declining = sorted(peaks, key=lambda key: peaks[key][0] -
                       (100 * current[key] / current_total if current_total else 0), reverse=True)
    selected = []
    for key in [artist_id, *current_top, *historic_top, *declining[:8],
                *(row["id"] for row in neglected[:8])]:
        if key in lifetime and key not in selected:
            selected.append(key)
    selected = ([artist_id] if artist_id in lifetime else []) if artist_id is not None else selected[:32]
    artists = []
    for key in selected:
        peak_share, peak_date = peaks.get(key, (0, None))
        artists.append(dict(
            id=key, name=names[key], plays=lifetime[key],
            current_plays=current[key],
            current_share=round(100 * current[key] / current_total, 2) if current_total else 0,
            peak_share=round(peak_share, 2), peak_date=peak_date,
        ))
    points = [
        dict(date=d, plays=plays, shares={
            key:round(100 * counts.get(key, 0) / plays, 2) if plays >= MIN_WINDOW_PLAYS else None
            for key in selected
        }) for d, plays, counts in samples
    ]
    result.update(sufficient=current_total >= MIN_WINDOW_PLAYS,
                  points=points, artists=artists, neglected=neglected[:8],
                  suggested=selected[:5], current_total=current_total)
    return result
