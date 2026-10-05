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
