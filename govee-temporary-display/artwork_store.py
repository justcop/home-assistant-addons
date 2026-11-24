"""Externally mounted, persistent 52x32 Govee artwork gallery.

The only writable asset store is /media/storage (the separate HA-mounted
storage disk). If it is absent, never create a replacement directory on HA's
internal disk. Existing bundled assets remain readable as a fallback.
"""
import base64
import binascii
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError

WIDTH, HEIGHT = 52, 32
VOLUME = Path("/media/storage")
ROOT = VOLUME / "govee-pixel-artwork"
IMAGES = ROOT / "images"
MANIFEST = ROOT / "gallery.json"
BUNDLED = Path(__file__).resolve().parent / "artwork"
LOCK = threading.RLock()
KINDS = {"game", "song", "artist", "album", "other"}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,50}$")
MAX_PNG_BYTES = 1_000_000
MAX_ASSETS = 1500

SEEDS = (
    dict(id="gta_vi", name="GTA VI Sunset", file="gta_vi.png",
         mappings=[{"kind":"game","value":"Grand Theft Auto VI"},
                   {"kind":"game","value":"Grand Theft Auto 6"},
                   {"kind":"game","value":"GTA VI"},
                   {"kind":"game","value":"GTA 6"}]),
    dict(id="ffvii_revelation", name="Final Fantasy VII Revelation", file="ffvii_revelation.png",
         mappings=[{"kind":"game","value":"Final Fantasy VII Revelation"},
                   {"kind":"game","value":"Final Fantasy VII: Revelation"},
                   {"kind":"game","value":"Final Fantasy 7 Revelation"},
                   {"kind":"game","value":"FFVII Revelation"}]),
    dict(id="ps5_default", name="PS5 Default", file="ps5_default.png", mappings=[]),
)
SEED_BY_ID = {s["id"]:s for s in SEEDS}


def _normalize(value):
    return re.sub(r"\s+", " ", str(value).replace("™", "").replace("®", "").strip()).casefold()


def _mounted():
    """Reject a shadow folder on the internal HA filesystem."""
    try:
        return (VOLUME.is_dir() and not VOLUME.is_symlink()
                and os.path.ismount(VOLUME)
                and VOLUME.stat().st_dev != Path("/media").stat().st_dev)
    except OSError:
        return False


def status():
    mounted = _mounted()
    accessible = mounted and ROOT.is_dir() and not ROOT.is_symlink()
    free = None
    if mounted:
        try:
            disk = os.statvfs(VOLUME)
            free = disk.f_bavail * disk.f_frsize
        except OSError:
            pass
    return {
        "path": str(ROOT),
        "volume": str(VOLUME),
        "external_mount": mounted,
        "ready": accessible and os.access(ROOT, os.W_OK | os.R_OK),
        "free_bytes": free,
        "error": None if mounted else "External /media/storage drive is not mounted; saving is disabled.",
    }


def _ensure_storage():
    if not _mounted():
        raise OSError("External storage drive /media/storage is not mounted; refusing internal-disk fallback")
    if ROOT.is_symlink() or IMAGES.is_symlink():
        raise OSError("Artwork directory cannot be a symlink")
    ROOT.mkdir(exist_ok=True)
    IMAGES.mkdir(exist_ok=True)
    if (not _mounted() or ROOT.stat().st_dev != VOLUME.stat().st_dev
            or IMAGES.stat().st_dev != VOLUME.stat().st_dev):
        raise OSError("Artwork folder is not on the external storage volume")
    return ROOT


def _atomic_write(path, payload):
    tmp=path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_manifest():
    if not MANIFEST.exists():
        return {"version": 1, "items": []}
    with MANIFEST.open("r", encoding="utf-8") as f:
        data=json.load(f)
    if (not isinstance(data,dict) or data.get("version")!=1
            or not isinstance(data.get("items"),list)):
        raise ValueError("Invalid gallery metadata; refusing to overwrite it")
    return data


def _write_manifest(data):
    _atomic_write(MANIFEST,json.dumps(data,ensure_ascii=False,indent=2).encode("utf-8"))


def _seed():
    """Import the three previously stored GitHub PNGs, once, non-destructively."""
    _ensure_storage()
    data=_read_manifest()
    ids={item["id"] for item in data["items"] if isinstance(item,dict) and "id" in item}
    dirty=False
    for item in SEEDS:
        key=item["id"]
        dst=IMAGES/(key+".png")
        src=BUNDLED/item["file"]
        if not dst.is_file():
            if not src.is_file():
                raise OSError("Bundled artwork is missing: "+item["file"])
            _atomic_write(dst,src.read_bytes())
        if key not in ids:
            data["items"].append({
                "id":key,"name":item["name"],"file":key+".png",
                "source":"bundled","mappings":item["mappings"],
                "created_at":int(time.time()),"updated_at":int(time.time())
            })
            dirty=True
    if dirty or not MANIFEST.exists():
        _write_manifest(data)
    return data


def _fallback_items():
    return [{
        "id":s["id"],"name":s["name"],"file":s["file"],
        "source":"bundled","mappings":s["mappings"],"created_at":0,"updated_at":0
    } for s in SEEDS]


def gallery():
    with LOCK:
        if not _mounted():
            return {"items":_fallback_items(),"storage":status(),"read_only":True}
        try:
            data=_seed()
            return {"items":data["items"],"storage":status(),"read_only":False}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            info=status()
            info["error"]=str(exc)[:250]
            info["ready"]=False
            return {"items":_fallback_items(),"storage":info,"read_only":True}


def _find(item_id, items):
    if not isinstance(item_id,str) or not ID_RE.fullmatch(item_id):
        raise ValueError("Invalid artwork ID")
    return next((it for it in items if it.get("id")==item_id),None)


def exists(item_id):
    return _find(item_id,gallery()["items"]) is not None


def get_png(item_id):
    with LOCK:
        data=gallery()
        item=_find(item_id,data["items"])
        if item is None:
            raise ValueError("Unknown artwork ID")
        if not data["read_only"]:
            path=IMAGES / (item["id"]+(".gif" if item.get("animation") else ".png"))
            if path.is_file() and not path.is_symlink():
                return path.read_bytes()
        seed=SEED_BY_ID.get(item_id)
        if seed:
            return (BUNDLED/seed["file"]).read_bytes()
        raise OSError("Saved artwork is unavailable because storage is disconnected")


def get_frames(item_id):
    with Image.open(io.BytesIO(get_png(item_id))) as image:
        if image.size != (WIDTH, HEIGHT):
            raise ValueError("Artwork must be 52×32")
        frames = []
        elapsed = 0
        for i in range(min(getattr(image, "n_frames", 1), 720)):
            image.seek(i)
            rgb = image.convert("RGB").tobytes()
            elapsed += max(10, image.info.get("duration", 83))
            target = min(720, round(elapsed * 12 / 1000))
            frames.extend([rgb] * max(0, target-len(frames)))
        return tuple(frames or [image.convert("RGB").tobytes()])


def get_rgb(item_id):
    with Image.open(io.BytesIO(get_png(item_id))) as image:
        if image.size!=(WIDTH,HEIGHT):
            raise ValueError("Artwork must be 52×32")
        return image.convert("RGB").tobytes()


def _mappings(value):
    if not isinstance(value,list) or len(value)>16:
        raise ValueError("Provide up to 16 game, song, artist or album matches")
    seen=set()
    result=[]
    for pair in value:
        if not isinstance(pair,dict):
            raise ValueError("Invalid artwork match")
        kind=pair.get("kind")
        title=pair.get("value")
        if kind not in KINDS or not isinstance(title,str):
            raise ValueError("Invalid artwork match type")
        title=title.strip()
        if not (1<=len(title)<=160):
            raise ValueError("Artwork match must contain 1–160 characters")
        key=(kind,_normalize(title))
        if key not in seen:
            result.append({"kind":kind,"value":title})
            seen.add(key)
    return result


def save(body):
    """Validate, atomically save PNG and metadata to the external disk only."""
    if not isinstance(body,dict):
        raise ValueError("Expected an artwork object")
    with LOCK:
        _ensure_storage()
        data=_seed()
        item_id=body.get("id")
        existing=_find(item_id,data["items"]) if item_id else None
        if item_id and not existing:
            raise ValueError("Artwork to edit was not found")
        if not existing and len(data["items"])>=MAX_ASSETS:
            raise ValueError("Artwork gallery is full")
        name=body.get("name")
        if not isinstance(name,str) or not (1<=len(name.strip())<=90):
            raise ValueError("Name must be 1–90 characters")
        name=name.strip()
        mappings=_mappings(body.get("mappings",[]))
        for candidate in data["items"]:
            if existing and candidate["id"]==existing["id"]:
                continue
            taken={(m["kind"],_normalize(m["value"])) for m in candidate.get("mappings",[])}
            for m in mappings:
                if (m["kind"],_normalize(m["value"])) in taken:
                    raise ValueError("That match is already assigned to "+candidate["name"])
        image64=body.get("image_png_base64")
        animation64=body.get("frames_zlib_base64")
        if image64 is not None and animation64 is not None:
            raise ValueError("Provide either PNG or animation")
        if image64 is None and animation64 is None and existing is None:
            raise ValueError("An image is required for new artwork")
        normalized_png=None
        if image64 is not None:
            if not isinstance(image64,str) or len(image64)>MAX_PNG_BYTES*2:
                raise ValueError("Image is too large")
            try:
                raw=base64.b64decode(image64,validate=True)
            except (binascii.Error,ValueError) as exc:
                raise ValueError("Image encoding is invalid") from exc
            if len(raw)>MAX_PNG_BYTES:
                raise ValueError("Image is too large")
            try:
                with Image.open(io.BytesIO(raw)) as img:
                    if img.format!="PNG" or img.size!=(WIDTH,HEIGHT) or getattr(img,"n_frames",1)!=1:
                        raise ValueError("Upload must be a single 52×32 PNG")
                    pixels=img.convert("RGB")
                    out=io.BytesIO()
                    pixels.save(out,format="PNG",optimize=True)
                    normalized_png=out.getvalue()
            except (UnidentifiedImageError,OSError) as exc:
                raise ValueError("Invalid PNG image") from exc
        normalized_gif=None
        if animation64 is not None:
            if not isinstance(animation64,str) or len(animation64)>4_000_000:
                raise ValueError("Animation too large")
            try:
                decoder=zlib.decompressobj()
                frame_bytes=WIDTH*HEIGHT*3
                decoded=decoder.decompress(base64.b64decode(animation64,validate=True),720*frame_bytes+1)
                if not decoder.eof or decoder.unused_data or len(decoded)>720*frame_bytes or not decoded or len(decoded)%frame_bytes:
                    raise ValueError("Invalid animation frames")
                frames=[Image.frombytes("RGB",(WIDTH,HEIGHT),decoded[i:i+frame_bytes]) for i in range(0,len(decoded),frame_bytes)]
                out=io.BytesIO()
                frames[0].save(out,format="GIF",save_all=True,append_images=frames[1:],duration=[80 if i%3!=2 else 90 for i in range(len(frames))],loop=0,disposal=2)
                normalized_gif=out.getvalue()
            except (zlib.error,binascii.Error,OSError) as exc:
                raise ValueError("Invalid animation") from exc
        now=int(time.time())
        if not existing:
            item_id="art_"+uuid.uuid4().hex[:16]
            existing={"id":item_id,"created_at":now,"source":"uploaded"}
            data["items"].append(existing)
        animation=normalized_gif is not None or (normalized_png is None and existing.get("animation",False))
        filename=item_id+(".gif" if animation else ".png")
        if normalized_gif is not None:
            _ensure_storage()
            _atomic_write(IMAGES/filename,normalized_gif)
        if normalized_png is not None:
            _ensure_storage()
            _atomic_write(IMAGES/filename,normalized_png)
        existing.update(name=name,file=filename,mappings=mappings,updated_at=now,animation=animation)
        _write_manifest(data)
        return existing.copy()


def delete(item_id):
    with LOCK:
        _ensure_storage()
        data=_seed()
        item=_find(item_id,data["items"])
        if not item:
            raise ValueError("Artwork not found")
        if item.get("source")=="bundled":
            raise ValueError("Bundled artwork cannot be deleted; edit its mappings instead")
        data["items"]=[it for it in data["items"] if it["id"]!=item_id]
        _write_manifest(data)
        # After metadata is safe, remove the now-unreferenced file.
        for suffix in (".png",".gif"):
            (IMAGES/(item_id+suffix)).unlink(missing_ok=True)


def resolve(kind,value):
    if kind not in KINDS or not isinstance(value,str):
        raise ValueError("Invalid lookup")
    key=_normalize(value)
    if not key or key in {"unknown","unavailable","none","null"}:
        return None
    for item in gallery()["items"]:
        if any(m["kind"]==kind and _normalize(m["value"])==key
               for m in item.get("mappings",[])):
            return item["id"]
    return None
