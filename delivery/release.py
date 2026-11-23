"""Prepare affected immutable images, then advertise only successful builds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import yaml

from contract import digest, files_under, manifest

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "delivery/releases.json"
LAYOUT = {
    "audioshelf": ("/app", ""), "lastfm_analytics": ("/app", ""),
    "money_locations": ("/app", "app/"), "music_video_companion": ("/app", "app/"),
    "work_audit": ("/app", ""), "vinyl_guardian": ("/usr/src/app", ""),
    "govee-temporary-display": ("/", ""), "vikunja_chatgpt_bridge": ("/app", ""),
    "whatsapp_mcp": ("/opt/whatsapp-addon", ""), "ocr_tool": ("/app", ""),
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def shared_bytes(root=ROOT):
    return b"".join((root / "delivery" / p).read_bytes() for p in ("contract.py", "launcher.py"))


def catalog(root=ROOT):
    specs = json.loads((root / "delivery/apps.json").read_text())
    result = {}
    for config_path in sorted(root.glob("*/config.yaml")):
        folder = config_path.parent
        config = yaml.safe_load(config_path.read_text())
        name = folder.name
        files = files_under(folder)
        spec = specs.get(name)
        if spec:
            item = manifest(files, spec, shared_bytes(root), config)
        else:
            # One-shot/administrative tools use the same automatic publisher,
            # but every executable change requires a container release.
            item = manifest(files, {"roots": [], "command": [], "protected": []},
                            shared_bytes(root) if name == "addon_updater" else b"", config)
        item.update(id=config["slug"], directory=name, config=config, live=bool(spec))
        result[name] = item
    return result


def next_version(current, previous):
    def parse(s):
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(s)):
            raise ValueError("Release version must be major.minor.patch: " + str(s))
        return tuple(map(int, str(s).split(".")))
    now = parse(current)
    old = parse(previous or current)
    if previous and now > old:
        return str(current)
    major, minor, patch = max(now, old)
    return f"{major}.{minor}.{patch + 1}"


def missing_whatsapp_image(version):
    """Repair an advertised GHCR tag that is not anonymously downloadable.

    Only WhatsApp is recovered automatically because it is the known affected
    package. Do not silently bump every installed app or rebuild an unrelated
    image when GHCR has a transient outage.
    """
    if not version:
        return False
    image = "ghcr.io/justcop/whatsapp-mcp-amd64:" + str(version)
    try:
        result = subprocess.run(["docker", "manifest", "inspect", image],
                                capture_output=True, text=True, timeout=40,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not verify public WhatsApp image: " + str(exc)) from exc
    if result.returncode == 0:
        return False
    detail = (result.stderr + result.stdout).lower()
    if any(x in detail for x in ("manifest unknown", "no such manifest",
                                 "not found", "404", "unauthorized", "denied")):
        print("WhatsApp image missing/inaccessible; rebuilding last advertised version:", image)
        return True
    raise RuntimeError("GHCR image verification inconclusive; refusing publication: " +
                       (result.stderr or "unknown error")[:260])


def prepare():
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    sha = git("rev-parse", "HEAD")
    plan = []
    for name, item in catalog().items():
        before = previous.get(name, {})
        same_content = item["compatibility"] == before.get("compatibility")
        repair = (name == "whatsapp_mcp" and
                  missing_whatsapp_image(before.get("version") or item["config"]["version"]))
        if same_content and not repair:
            continue
        config = item["config"]
        # A missing published image is repaired in place at the *same* tag,
        # rather than advertising another nonexistent version.
        version = str(before["version"]) if repair and same_content and before.get("version") else next_version(config["version"], before.get("version"))
        image = "ghcr.io/justcop/" + item["id"].replace("_", "-") + "-{arch}"
        item.update(sha=sha, version=version, image=image)
        folder = ROOT / name
        if item["live"] or name == "addon_updater":
            bundle = folder / ".delivery"
            bundle.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / "delivery/contract.py", bundle / "contract.py")
        if item["live"]:
            destination, strip = item["spec"]["destination"], item["spec"]["strip"]
            item.update(destination=destination, strip=strip, ancestors=git("rev-list", "HEAD").splitlines())
            bundle = folder / ".delivery"
            bundle.mkdir(exist_ok=True)
            for file in ("launcher.py", "contract.py"):
                shutil.copyfile(ROOT / "delivery" / file, bundle / file)
            baked = {k:v for k,v in item.items() if k != "config"}
            (bundle / "manifest.json").write_text(json.dumps(baked))
        dockerfile = (folder / "Dockerfile").read_text()
        if "ARG BUILD_FROM" in dockerfile:
            build_from = yaml.safe_load((folder / "build.yaml").read_text())["build_from"]
        else:
            build_from = {}
        command = ["python3", "/opt/delivery/launcher.py"]
        if item["spec"].get("with_contenv"):
            command.insert(0, "/command/with-contenv")
        (folder / ".delivery.Dockerfile").write_text(dockerfile + (
            "\nCOPY .delivery /opt/delivery\nCMD " + json.dumps(command) + "\n" if item["live"] else "\n"))
        for arch in config["arch"]:
            if arch not in {"amd64", "aarch64", "armv7", "armhf", "i386"}:
                raise ValueError("Unsupported architecture: " + arch)
            plan.append({"addon": name, "arch": arch, "version": version,
                         "image": image.replace("{arch}", arch),
                         "platform": {"aarch64":"linux/arm64", "amd64":"linux/amd64", "armv7":"linux/arm/v7", "armhf":"linux/arm/v6", "i386":"linux/386"}[arch],
                         "build_from": build_from.get(arch, ""), "sha": sha})
        previous[name] = {k:item[k] for k in ("sha", "compatibility", "version", "image")}
    Path("/tmp/addon-release-plan.json").write_text(json.dumps(plan))
    Path("/tmp/addon-release-state.json").write_text(json.dumps(previous, indent=2) + "\n")
    return plan


def build():
    plan = prepare()
    for row in plan:
        args = ["docker", "buildx", "build", row["addon"], "--file", row["addon"] + "/.delivery.Dockerfile",
                "--platform", row["platform"], "--build-arg", "BUILD_ARCH=" + row["arch"],
                "--build-arg", "BUILD_VERSION=" + row["version"],
                "--tag", row["image"] + ":" + row["version"], "--push",
                "--cache-from", f"type=gha,scope=delivery-{row['addon']}-{row['arch']}",
                "--cache-to", f"type=gha,scope=delivery-{row['addon']}-{row['arch']},mode=max,ignore-error=true"]
        if row["build_from"]:
            args += ["--build-arg", "BUILD_FROM=" + row["build_from"]]
        subprocess.run(args, check=True, cwd=ROOT)
    return plan


def publish():
    plan = json.loads(Path("/tmp/addon-release-plan.json").read_text())
    for row in plan:
        path = ROOT / row["addon"] / "config.yaml"
        config = yaml.safe_load(path.read_text())
        # Every architecture was built before this configuration is advertised.
        config["version"] = row["version"]
        config["image"] = row["image"].replace(row["arch"], "{arch}")
        path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    STATE.write_text(Path("/tmp/addon-release-state.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "build", "publish"])
    args = parser.parse_args()
    {"prepare": prepare, "build": build, "publish": publish}[args.action]()
