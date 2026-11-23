"""Listening Analytics, a local and reversible view of Last.fm history."""

import json
from pathlib import Path

# Image releases are versioned by the shared publisher; local test runs have no
# baked manifest. The deployed code commit remains a separate identity.
try:
    __version__ = json.loads(Path('/opt/delivery/manifest.json').read_text())['version']
except (OSError, ValueError, KeyError):
    __version__ = "development"
