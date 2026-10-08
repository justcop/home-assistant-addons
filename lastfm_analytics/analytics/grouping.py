"""Conservative title rules. Never discard performance/mix qualifiers."""

import re
import unicodedata


def normalise(value):
    value = unicodedata.normalize("NFKC", value).translate(
        str.maketrans({"’": "'", "‘": "'"})
    )
    return " ".join(value.casefold().split())


REMASTER = (
    r"(?:(?:19|20)\d{2}\s+)?remaster(?:ed)?(?:\s+(?:(?:19|20)\d{2}|version|edition))?"
)
EDITION = (
    rf"(?:{REMASTER}|deluxe(?:\s+edition)?|expanded(?:\s+edition)?|special edition)"
)


def canonical_title(title, kind):
    """Strip only a whole, recognised suffix; 'Live, 2009 Remaster' stays live."""
    allowed = REMASTER if kind == "song" else EDITION
    pattern = rf"\s*(?:\(\s*{allowed}\s*\)|\[\s*{allowed}\s*\]|\s[-–—]\s*{allowed})\s*$"
    result = title.strip()
    while True:
        clean = re.sub(pattern, "", result, flags=re.IGNORECASE).strip()
        if not clean or clean == result:
            return result
        result = clean


def auto_key(artist, title, kind):
    import json

    return json.dumps(
        [normalise(artist), normalise(canonical_title(title, kind))], ensure_ascii=False
    )


PERFORMANCE = re.compile(
    r"\b(live|acoustic|remix|mix|demo|instrumental|karaoke|radio|edit|session|version)\b",
    re.I,
)


def review_title(title):
    """Identify a potential suffix without declaring two recordings equivalent."""
    match = re.match(
        r"^(.*?)\s*(?:\(([^()]+)\)|\[([^\[\]]+)\]|\s[-–—]\s(.+))$", title.strip()
    )
    if match and match.group(1).strip():
        base = match.group(1).strip()
        suffix = title.strip()[len(base) :]
        qualifier = next(x for x in match.groups()[1:] if x is not None)
        return base, suffix, bool(PERFORMANCE.search(qualifier))
    return title.strip(), "", False


def review_key(title):
    base, _, _ = review_title(title)
    return re.sub(r"[^\w]", "", normalise(base))

def artist_suggestion_key(name):
    """Loose candidate key, NEVER an automatic artist identity.

    Ignore typographic differences, accents, common punctuation and a leading
    English article solely to propose human-reviewed pairs.
    """
    name = unicodedata.normalize("NFKD", normalise(name))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^\\w\\s]", " ", name)
    name = " ".join(name.split())
    if name.startswith("the "):
        name = name[4:]
    return name
