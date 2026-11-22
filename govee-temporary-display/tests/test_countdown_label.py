"""Regression checks for Govee PlayStation countdown label formatting."""
import unittest
from ha_countdown import countdown_label

class CountdownLabelTest(unittest.TestCase):
    def test_less_than_an_hour_omits_zero_hours(self):
        self.assertEqual(countdown_label(2100), "35M")
        self.assertEqual(countdown_label(60), "1M")
        self.assertEqual(countdown_label(1), "1M")
        self.assertEqual(countdown_label(0), "0M")

    def test_exact_one_hour_and_above(self):
        self.assertEqual(countdown_label(3600), "1H 00M")
        self.assertEqual(countdown_label(3601), "1H 01M")
        self.assertEqual(countdown_label(4920), "1H 22M")

    def test_rounded_minutes_do_not_add_an_early_hour(self):
        self.assertEqual(countdown_label(3599), "60M")
        self.assertEqual(countdown_label(3540), "59M")
        self.assertEqual(countdown_label(3541), "60M")

if __name__ == "__main__":
    unittest.main()
