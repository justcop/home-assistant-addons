"""Deterministic, fictional listening history. Always in a separate database."""

import random
from datetime import datetime, timedelta, timezone

CATALOGUE = [
    (
        "Radiohead",
        "In Rainbows",
        ["15 Step", "Bodysnatchers", "Nude", "Weird Fishes/Arpeggi", "All I Need"],
    ),
    (
        "The Beatles",
        "Abbey Road",
        [
            "Come Together",
            "Something",
            "Here Comes the Sun",
            "Because",
            "Golden Slumbers",
        ],
    ),
    (
        "Tame Impala",
        "Currents",
        [
            "Let It Happen",
            "Eventually",
            "The Less I Know the Better",
            "New Person, Same Old Mistakes",
        ],
    ),
    (
        "Fleetwood Mac",
        "Rumours",
        ["Dreams", "Go Your Own Way", "The Chain", "You Make Loving Fun"],
    ),
    (
        "Massive Attack",
        "Mezzanine",
        ["Angel", "Risingson", "Teardrop", "Inertia Creeps"],
    ),
    (
        "Arctic Monkeys",
        "Favourite Worst Nightmare",
        ["Brianstorm", "Teddy Picker", "Fluorescent Adolescent", "505"],
    ),
    (
        "Nirvana",
        "Nevermind",
        ["Come as You Are", "Lithium", "In Bloom", "Something in the Way"],
    ),
    (
        "Oasis",
        "(What’s the Story) Morning Glory?",
        [
            "Wonderwall",
            "Don't Look Back in Anger",
            "Some Might Say",
            "Champagne Supernova",
        ],
    ),
    ("Biffy Clyro", "Only Revolutions", ["Mountains", "Many of Horror", "Bubbles"]),
    ("Portishead", "Dummy", ["Mysterons", "Sour Times", "Glory Box"]),
    (
        "Khruangbin",
        "Con Todo El Mundo",
        ["Como Me Quieres", "Maria También", "Friday Morning"],
    ),
    (
        "Little Simz",
        "Sometimes I Might Be Introvert",
        ["Introvert", "Woman", "I Love You, I Hate You"],
    ),
]


def seed(database, now=None):
    if database.meta("demo_seeded"):
        return
    rng = random.Random(71)
    now = now or datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for ago in range(729, -1, -1):
        day = midnight - timedelta(days=ago)
        for n in range(rng.randint(4, 24)):
            index = rng.choices(
                range(10 if ago > 20 else 12),
                weights=[22, 18, 15, 10, 8, 7, 6, 5, 4, 3]
                + ([] if ago > 20 else [12, 9]),
            )[0]
            if index == 9 and 12 < ago < 180:
                index = 0
            artist, album, titles = CATALOGUE[index]
            title = rng.choice(titles)
            if index == 1 and rng.random() < 0.4:
                title += " (2009 Remaster)"
                album += " (Remastered)"
            if index == 6 and rng.random() < 0.15:
                title += " - Live"
            ts = int((day + timedelta(hours=17, minutes=n * 6)).timestamp())
            if ts < now.timestamp():
                rows.append(
                    {
                        "ts": ts,
                        "artist": artist,
                        "title": title,
                        "album": album,
                        "raw": {"demo": True},
                    }
                )
    database.apply_window(0, int(now.timestamp()) + 1, rows)
    database.set_meta(
        "import",
        {
            "lower": min(r["ts"] for r in rows),
            "upper": int(now.timestamp()),
            "cursor": min(r["ts"] for r in rows),
            "complete": True,
            "windows": 25,
        },
    )
    database.set_meta("last_sync", int(now.timestamp()))
    database.set_meta("demo_seeded", True)
