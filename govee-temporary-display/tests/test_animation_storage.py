import base64
import io
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import artwork_store as store


class AnimationStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        images = root / "images"
        images.mkdir()
        self.patches = patch.multiple(store, ROOT=root, IMAGES=images,
                                      MANIFEST=root / "gallery.json", SEEDS=(),
                                      _mounted=lambda: True, _ensure_storage=lambda: root)
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def payload(self, raw):
        return {"name":"Test animation", "frames_zlib_base64":base64.b64encode(zlib.compress(raw)).decode()}

    def test_animation_roundtrip(self):
        red = bytes([255,0,0])*1664
        blue = bytes([0,0,255])*1664
        item = store.save(self.payload(red+blue))
        self.assertTrue(item["animation"])
        self.assertEqual(store.get_frames(item["id"]), (red,blue))
        self.assertEqual(store.get_rgb(item["id"]), red)
        self.assertEqual(store.save({"id":item["id"],"name":"Renamed"})["file"],item["file"])
        store.delete(item["id"])
        self.assertFalse((store.IMAGES/item["file"]).exists())

    def test_static_replacement(self):
        item = store.save(self.payload(bytes(4992)))
        out = io.BytesIO()
        Image.new("RGB",(52,32),(12,34,56)).save(out,format="PNG")
        updated = store.save({"id":item["id"],"name":"Still","image_png_base64":base64.b64encode(out.getvalue()).decode()})
        self.assertFalse(updated["animation"])
        self.assertEqual(store.get_frames(item["id"]), (bytes([12,34,56])*1664,))
        self.assertFalse((store.IMAGES/item["file"]).exists())

    def test_invalid_frames(self):
        for raw in (b"", b"x", bytes(4992*721)):
            with self.assertRaises(ValueError):
                store.save(self.payload(raw))
        self.assertEqual(store.gallery()["items"],[])

    def test_unmounted_no_save(self):
        with patch.object(store,"_ensure_storage",side_effect=OSError("not mounted")):
            with self.assertRaises(OSError):
                store.save(self.payload(bytes(4992)))
