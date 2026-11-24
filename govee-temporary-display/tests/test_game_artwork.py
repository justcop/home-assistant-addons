"""Game-aware Govee artwork regression tests.

Run with: PYTHONPATH=govee-temporary-display python3 -m unittest discover
       -s govee-temporary-display/tests
"""
import unittest
from pathlib import Path
from hashlib import sha256
from artwork_loader import load_rgb_image

from artwork_modes import get_artwork_rgb, resolve_game_artwork
from ha_countdown import countdown_label, render, render_frames, selected_artwork_mode, validate

CONFIG = {
    "state_entity": "input_select.gaming_session_state",
    "deadline_entity": "input_datetime.gaming_session_deadline",
    "power_entity": "switch.ps5_044_power",
    "active_states": ["active"],
    "title_lines": ["GRAND", "THEFT", "AUTO"],
    "mark": "VI",
}

class GameArtworkTests(unittest.TestCase):
    def setUp(self):
        self.config = validate(CONFIG)

    def test_old_vi_binding_migrates_to_auto(self):
        self.assertEqual(self.config["artwork_mode"], "auto")
        self.assertEqual(self.config["game_entity"], "sensor.justcop_now_playing")
        self.assertEqual(self.config["activity_entity"], "sensor.ps5_044_activity")

    def test_gta_vi_exact_title(self):
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI", "playing"), "gta_vi")
        self.assertEqual(resolve_game_artwork("grand theft auto vi™", "playing"), "gta_vi")
        self.assertEqual(resolve_game_artwork("GTA 6", "playing"), "gta_vi")

    def test_ffvii_revelation_title_and_png(self):
        for title in ("Final Fantasy VII Revelation",
                      "Final Fantasy VII: Revelation",
                      "FFVII Revelation"):
            with self.subTest(title=title):
                self.assertEqual(resolve_game_artwork(title, "playing"),
                                 "ffvii_revelation")
        self.assertEqual(resolve_game_artwork("Final Fantasy VII Rebirth", "playing"),
                         "ps5_default")
        self.assertEqual(
            sha256(load_rgb_image("ffvii_revelation.png")).hexdigest(),
            "e046e9a83cb937081731307a6b5aa70c8689ed6a931a1c5c0be94f5be52d25b8"
        )

    def test_other_titles_do_not_show_gta_art(self):
        for title in ("Grand Theft Auto V", "Astro Bot", "Final Fantasy VII Rebirth", "unknown", "unavailable", None):
            with self.subTest(title=title):
                self.assertEqual(resolve_game_artwork(title, "playing"), "ps5_default")

    def test_idle_or_paused_game_is_not_assumed(self):
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI", "idle"), "ps5_default")
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI", "none"), "ps5_default")

    def test_missing_external_artwork_falls_back_without_disabling_countdown(self):
        from unittest.mock import patch
        from ha_countdown import selected_artwork_mode
        from artwork_store import get_png
        # A persisted fixed ID remains valid after the disk disconnects.
        persisted=dict(CONFIG, artwork_mode="art_1234567890abcdef")
        with patch("ha_countdown.is_artwork_mode",return_value=False):
            config=validate(persisted,allow_missing_mode=True)
            self.assertEqual(selected_artwork_mode(config,"anything","playing"),"ps5_default")

    def test_fixed_mode_overrides_game_selection(self):
        cfg = validate(dict(CONFIG, artwork_mode="gta_vi"))
        self.assertEqual(selected_artwork_mode(cfg, "Astro Bot", "playing"), "gta_vi")

    def test_artwork_stored_as_exact_png_files(self):
        root=Path(__file__).resolve().parents[1] / "artwork"
        expected={
            "gta_vi.png":"fc42bdbc34cb7c39c4526a96c3188da67e6ab29f60f92e7916e3e8a5eca4cbda",
            "ps5_default.png":"079ab8c98b29bbf8f21928e9b7893947c097c604909f8fd4c8c4495fa5230639",
        }
        for name,digest in expected.items():
            with self.subTest(name=name):
                self.assertTrue((root/name).is_file())
                self.assertEqual(sha256(load_rgb_image(name)).hexdigest(),digest)

    def test_artwork_and_overlay_are_distinct_and_complete(self):
        self.assertEqual(len(get_artwork_rgb("gta_vi")), 52 * 32 * 3)
        self.assertEqual(len(get_artwork_rgb("ps5_default")), 52 * 32 * 3)
        a = render(self.config, 2100, "Grand Theft Auto VI", "playing")
        b = render(self.config, 2100, "Astro Bot", "playing")
        self.assertEqual(len(a), 52 * 32 * 3)
        self.assertEqual(len(b), 52 * 32 * 3)
        self.assertNotEqual(a, b)
        self.assertEqual(countdown_label(2100), "35M")


    def test_animated_timer_background(self):
        from unittest.mock import patch
        red=bytes((255,0,0))*1664
        blue=bytes((0,0,255))*1664
        with patch("ha_countdown.get_frames",return_value=(red,blue,red)):
            frames=render_frames(self.config,2100,mode="gta_vi")
        self.assertEqual(len(frames),3)
        self.assertEqual(frames[0],frames[2])
        self.assertEqual(frames[0][:3],red[:3])
        self.assertEqual(frames[1][:3],blue[:3])
        self.assertEqual(len(frames[0]),4992)
        ink=bytes((255,249,228))
        positions=lambda f: [i for i in range(0,len(f),3) if f[i:i+3]==ink]
        self.assertTrue(positions(frames[0]))
        self.assertEqual(positions(frames[0]),positions(frames[1]))

    def test_animated_timer_fallback(self):
        from unittest.mock import patch
        with patch("ha_countdown.get_frames",side_effect=OSError("drive missing")):
            frames=render_frames(self.config,2100,mode="gta_vi")
        self.assertEqual(len(frames),1)
        self.assertEqual(len(frames[0]),4992)


if __name__ == "__main__":
    unittest.main()
