"""Read-only live frontend source for Development Manager-managed AudioShelf.

The manager exclusively controls /local_apps/devmgr_audioshelf.  A new Git
commit is visible to Flask without restarting the running application.
Backend code remains imported from the built container.
"""
import json
import re
from pathlib import Path


class LiveAssets:
    def __init__(self, root: Path, fallback_version: str):
        self.root = Path(root)
        self.fallback_version = fallback_version

    def commit(self):
        marker = self.root / ".dev-manager.json"
        if marker.is_symlink() or not marker.is_file():
            return None
        try:
            identity = json.loads(marker.read_text())
        except (OSError, ValueError):
            return None
        sha = identity.get("sha")
        if (identity.get("id") != "audioshelf"
                or identity.get("slug") != "local_audioshelf"
                or not isinstance(sha, str)
                or not re.fullmatch(r"[0-9a-f]{40}", sha)):
            return None
        return sha

    def static_root(self):
        if not self.commit():
            return None
        path = self.root / "app" / "static"
        return path if path.is_dir() and not path.is_symlink() else None

    def version(self):
        sha = self.commit()
        return self.fallback_version + ("-git." + sha[:16] if sha else "")
