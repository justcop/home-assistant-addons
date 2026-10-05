import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_runtime_source_defines_recognition_target(self):
        source = (ROOT / "vinyl_guardian.py").read_text()
        self.assertIn(
            "target = math.ceil(RATE / CHUNK * recognition_session.final_stage)",
            source,
        )
        self.assertNotIn(
            "strand the 10s stage forever.\\n    target =",
            source,
        )

    def test_runtime_dependencies_are_exactly_pinned(self):
        requirements = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertGreater(len(requirements), 0)
        self.assertTrue(all("==" in line for line in requirements))
        names = {line.split("==", 1)[0].lower() for line in requirements}
        self.assertEqual(
            names,
            {"numpy", "paho-mqtt", "pyalsaaudio", "requests", "shazamio", "pylast", "audioop-lts"},
        )

    def test_dockerfile_uses_explicit_multiarch_base_and_removes_build_deps(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM ghcr.io/home-assistant/base-python:", dockerfile)
        self.assertNotIn("ARG BUILD_FROM", dockerfile)
        self.assertNotIn("amd64-base-python", dockerfile)
        self.assertIn("apk add --no-cache --virtual .build-deps", dockerfile)
        self.assertIn("apk del .build-deps", dockerfile)
        self.assertIn("-r /tmp/requirements.txt", dockerfile)

    def test_config_only_advertises_architectures_supported_by_multiarch_base(self):
        config = yaml.safe_load((ROOT / "config.yaml").read_text())
        self.assertEqual(set(config["arch"]), {"amd64"})

    def test_manual_detector_overrides_are_not_exposed(self):
        config_text = (ROOT / "config.yaml").read_text()
        translation_text = (ROOT / "translations" / "en.yaml").read_text()
        for key in (
            "manual_override_mic_volume",
            "manual_override_music_threshold",
            "manual_override_motor_threshold",
        ):
            self.assertNotIn(key, config_text)
            self.assertNotIn(key, translation_text)


if __name__ == "__main__":
    unittest.main()
