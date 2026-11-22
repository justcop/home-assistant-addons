"""Game-aware Govee artwork regression tests.

Run with: PYTHONPATH=govee-temporary-display python3 -m unittest discover
       -s govee-temporary-display/tests
"""
import unittest

from artwork_modes import get_artwork_rgb, resolve_game_artwork
from ha_countdown import countdown_label, render, selected_artwork_mode, validate

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

    def test_other_titles_do_not_show_gta_art(self):
        for title in ("Grand Theft Auto V", "Astro Bot", "Final Fantasy VII Rebirth", "unknown", "unavailable", None):
            with self.subTest(title=title):
                self.assertEqual(resolve_game_artwork(title, "playing"), "ps5_default")

    def test_idle_or_paused_game_is_not_assumed(self):
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI", "idle"), "ps5_default")
        self.assertEqual(resolve_game_artwork("Grand Theft Auto VI", "none"), "ps5_default")

    def test_fixed_mode_overrides_game_selection(self):
        cfg = validate(dict(CONFIG, artwork_mode="gta_vi"))
        self.assertEqual(selected_artwork_mode(cfg, "Astro Bot", "playing"), "gta_vi")

    def test_artwork_and_overlay_are_distinct_and_complete(self):
        self.assertEqual(len(get_artwork_rgb("gta_vi")), 52 * 32 * 3)
        self.assertEqual(len(get_artwork_rgb("ps5_default")), 52 * 32 * 3)
        a = render(self.config, 2100, "Grand Theft Auto VI", "playing")
        b = render(self.config, 2100, "Astro Bot", "playing")
        self.assertEqual(len(a), 52 * 32 * 3)
        self.assertEqual(len(b), 52 * 32 * 3)
        self.assertNotEqual(a, b)
        self.assertEqual(countdown_label(2100), "35M")


if __name__ == "__main__":
    unittest.main()
