"""Explain possible matches; leave ambiguous recording decisions to the listener."""

from collections import defaultdict
from itertools import combinations
from .grouping import normalise, review_key, review_title
from .db import Database


def review(db, kind="song", tab="suggested", q="", offset=0):
    if kind not in ("song", "album") or tab not in ("suggested", "skipped", "merged"):
        raise ValueError("Unknown grouping view")
    column = "song_id" if kind == "song" else "album_id"
    with db.connect() as conn:
        variants = [
            dict(r)
            for r in conn.execute(
                f"""SELECT v.id, v.group_id, v.artist, v.name, v.override_group, a.group_id alias_group,
          g.name group_name, aa.canonical_key artist_identity, COUNT(s.id) plays FROM resolved_variants v JOIN groups g ON g.id=v.group_id JOIN aliases a ON a.kind=v.kind AND a.auto_key=v.auto_key JOIN scrobbles s ON s.{column}=v.id JOIN artist_aliases aa ON aa.artist_key=source_key(v.artist)
          WHERE s.active=1 AND v.kind=? GROUP BY v.id""",
                (kind,),
            )
        ]
        groups = {}
        buckets = defaultdict(dict)
        for v in variants:
            group = groups.setdefault(
                v["group_id"],
                {
                    "id": v["group_id"],
                    "artist": v["artist"],
                    "name": v["group_name"],
                    "versions": [],
                    "plays": 0,
                },
            )
            group["versions"].append(v)
            group["plays"] += v["plays"]
            buckets[(v["artist_identity"], review_key(v["name"]))][
                v["group_id"]
            ] = group
        dismissed = Database.get(conn, "dismissed_candidates", [])
        rules = Database.get(conn, "learned_rules", [])
        learned_ids = {vid for r in rules for vid in r["applied"]}
        rows = []
        if tab == "merged":
            rows = [g for g in groups.values() if len(g["versions"]) > 1]
        else:
            seen = set()
            for bucket in buckets.values():
                for left, right in combinations(bucket.values(), 2):
                    ids = sorted([left["id"], right["id"]])
                    key = f"{kind}:{ids[0]}:{ids[1]}"
                    if key in seen:
                        continue
                    seen.add(key)
                    names = [v["name"] for g in (left, right) for v in g["versions"]]
                    protected = any(review_title(n)[2] for n in names)
                    rejected = key in dismissed
                    separated = any(
                        v["override_group"] is not None
                        and v["override_group"] != v["alias_group"]
                        and v["id"] not in learned_ids
                        for g in (left, right)
                        for v in g["versions"]
                    )
                    skipped = protected or rejected or separated
                    if (tab == "skipped") != skipped:
                        continue
                    suffixes = [review_title(n)[1] for n in names if review_title(n)[1]]
                    learnable = (
                        not protected
                        and not separated
                        and len(set(map(normalise, suffixes))) == 1
                        and any(not review_title(n)[1] for n in names)
                        and len({normalise(review_title(n)[0]) for n in names}) == 1
                    )
                    rows.append(
                        {
                            "key": key,
                            "ids": ids,
                            "artist": left["artist"],
                            "names": names,
                            "versions": [
                                {k: v[k] for k in ("id", "name", "plays", "group_id")}
                                for g in (left, right) for v in g["versions"]
                            ],
                            "plays": left["plays"] + right["plays"],
                            "learnable": learnable,
                            "dismissed": rejected,
                            "suffix": suffixes[0] if learnable else None,
                            "reason": (
                                "You chose to keep these separate."
                                if rejected
                                else (
                                    "A version was manually separated; that choice is preserved."
                                    if separated
                                    else (
                                        "Performance or mix qualifier retained; these may be different recordings."
                                        if protected or separated
                                        else (
                                            "Same artist and base title; this suffix is not covered by an automatic rule."
                                            if suffixes
                                            else "Same artist; titles differ only in punctuation or formatting."
                                        )
                                    )
                                )
                            ),
                        }
                    )
        if q:
            needle = normalise(q)
            rows = [
                r
                for r in rows
                if needle
                in normalise(
                    r["artist"]
                    + " " + r.get("name", "") + " "
                    + " ".join(
                        r.get("names", [v["name"] for v in r.get("versions", [])])
                    )
                )
            ]
        rows.sort(key=lambda r: (-r["plays"], r["artist"]))
        return {
            "rows": rows[offset : offset + 50],
            "total": len(rows),
            "rules": [
                {k: v for k, v in r.items() if k not in ("applied", "artist_key")}
                for r in rules
            ],
            "offset": offset,
        }
