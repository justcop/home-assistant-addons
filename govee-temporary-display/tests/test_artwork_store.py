"""External-drive gallery and game/song metadata regression tests."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import artwork_store as store
from artwork_modes import resolve_game_artwork, get_artwork_rgb


class ArtworkStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.patches=[
            patch.object(store,"VOLUME",root),
            patch.object(store,"ROOT",root/"govee-pixel-artwork"),
            patch.object(store,"IMAGES",root/"govee-pixel-artwork"/"images"),
            patch.object(store,"MANIFEST",root/"govee-pixel-artwork"/"gallery.json"),
            patch.object(store,"_mounted",return_value=True),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _png(self,w=52,h=32):
        img=Image.new("RGB",(w,h),(52,144,221))
        stream=io.BytesIO()
        img.save(stream,format="PNG")
        return base64.b64encode(stream.getvalue()).decode("ascii")

    def test_imports_existing_bundled_art_once(self):
        result=store.gallery()
        self.assertFalse(result["read_only"])
        self.assertGreaterEqual(len(result["items"]),3)
        for name in ("gta_vi","ffvii_revelation","ps5_default"):
            self.assertTrue((store.IMAGES/(name+".png")).exists())
        self.assertEqual(len(store.gallery()["items"]),len(result["items"]))
        self.assertTrue(store.MANIFEST.is_file())

    def test_upload_and_metadata_persist_on_external_disk(self):
        saved=store.save({
            "name":"Another Final Fantasy",
            "image_png_base64":self._png(),
            "mappings":[{"kind":"game","value":"Final Fantasy VII"},
                        {"kind":"song","value":"One-Winged Angel"}],
        })
        self.assertEqual(len(store.get_rgb(saved["id"])),52*32*3)
        self.assertTrue((store.IMAGES/(saved["id"]+".png")).is_file())
        self.assertEqual(store.resolve("game","final fantasy vii"),saved["id"])
        self.assertEqual(store.resolve("song","one-winged angel"),saved["id"])
        edited=store.save({"id":saved["id"],"name":"FFVII","mappings":[{"kind":"game","value":"Final Fantasy VII"}]})
        self.assertEqual(edited["name"],"FFVII")
        self.assertEqual(store.resolve("song","One-Winged Angel"),None)
        store.delete(saved["id"])
        self.assertFalse((store.IMAGES/(saved["id"]+".png")).exists())

    def test_unknown_game_never_displays_gta(self):
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI","playing"),"gta_vi")
        self.assertEqual(resolve_game_artwork("Final Fantasy VII Revelation","playing"),"ffvii_revelation")
        self.assertEqual(resolve_game_artwork("Astro Bot","playing"),"ps5_default")
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI","idle"),"ps5_default")

    def test_rejects_conflicting_and_wrong_sized_images(self):
        with self.assertRaisesRegex(ValueError,"52"):
            store.save({"name":"Wrong","image_png_base64":self._png(100,100),"mappings":[]})
        with self.assertRaisesRegex(ValueError,"already assigned"):
            store.save({"name":"Duplicate","image_png_base64":self._png(),
                        "mappings":[{"kind":"game","value":"GTA VI"}]})
        with self.assertRaises(ValueError):
            store.get_png("../../secrets")

    def test_unmounted_drive_blocks_upload_without_internal_fallback(self):
        with patch.object(store,"_mounted",return_value=False):
            info=store.gallery()
            self.assertTrue(info["read_only"])
            with self.assertRaisesRegex(OSError,"not mounted"):
                store.save({"name":"Not saved","image_png_base64":self._png(),"mappings":[]})
        self.assertFalse(store.MANIFEST.exists())

    def test_bundled_images_cannot_be_deleted(self):
        with self.assertRaisesRegex(ValueError,"Bundled"):
            store.delete("gta_vi")


if __name__=="__main__":
    unittest.main()
