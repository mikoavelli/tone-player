import tempfile
import unittest
from pathlib import Path

from tests.bootstrap import ConfigNotFoundError, ConfigValidationError, load_config

VALID_CONFIG = """
[playback]
number_of_constants = 2
f_i = [500, 1000]
seconds_per_tone = 10.0
target_spl_db = 90.0

[calibration]
gain_db_i = [-12.0, -10.5]

[output]
sample_rate = 48000
fade_ms = 20.0
device = 0
default_offset_db = 0.0
"""


class TestConfigLoader(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "config.toml"

    def _write(self, text: str) -> None:
        self._path.write_text(text, encoding="utf-8")

    def test_loads_valid_config(self) -> None:
        self._write(VALID_CONFIG)
        config = load_config(self._path)

        self.assertEqual(config.frequencies, (500.0, 1000.0))
        self.assertEqual(config.gain_db, (-12.0, -10.5))
        self.assertEqual(config.seconds_per_tone, 10.0)
        self.assertEqual(config.target_spl_db, 90.0)
        self.assertEqual(config.sample_rate, 48000)
        self.assertEqual(config.device, 0)
        self.assertEqual(config.total_seconds, 20.0)
        self.assertEqual(config.fade_seconds, 0.02)

    def test_missing_file(self) -> None:
        with self.assertRaises(ConfigNotFoundError):
            _ = load_config(self._path)

    def test_rejects_syntax_error(self) -> None:
        self._write("[playback")
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_frequency_count_mismatch(self) -> None:
        self._write(VALID_CONFIG.replace("f_i = [500, 1000]", "f_i = [500, 1000, 1500]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_gain_count_mismatch(self) -> None:
        self._write(VALID_CONFIG.replace("gain_db_i = [-12.0, -10.5]", "gain_db_i = [-12.0]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_negative_frequency(self) -> None:
        self._write(VALID_CONFIG.replace("f_i = [500, 1000]", "f_i = [500, -1000]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_positive_gain(self) -> None:
        self._write(VALID_CONFIG.replace("gain_db_i = [-12.0, -10.5]", "gain_db_i = [3.0, -10.5]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_non_positive_duration(self) -> None:
        self._write(VALID_CONFIG.replace("seconds_per_tone = 10.0", "seconds_per_tone = 0.0"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_missing_section(self) -> None:
        self._write(VALID_CONFIG.replace("[output]", "[unused]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_fade_limited_by_tone_length(self) -> None:
        self._write(VALID_CONFIG.replace("seconds_per_tone = 10.0", "seconds_per_tone = 0.01"))
        config = load_config(self._path)
        self.assertEqual(config.fade_seconds, 0.005)

    def test_device_may_be_omitted(self) -> None:
        self._write(
            VALID_CONFIG.replace(
                "device = 0",
                "",
            )
        )
        config = load_config(self._path)
        self.assertIsNone(config.device)


class TestProjectConfig(unittest.TestCase):
    """Конфигурация, поставляемая вместе с приложением."""

    def test_project_config_is_valid(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.toml"
        config = load_config(config_path)

        self.assertEqual(config.tone_count, 20)
        self.assertEqual(config.seconds_per_tone, 10.0)
        self.assertEqual(config.total_seconds, 200.0)
        self.assertEqual(config.target_spl_db, 90.0)
        self.assertEqual(config.frequencies[0], 500.0)
        self.assertEqual(config.frequencies[-1], 31500.0)


if __name__ == "__main__":
    unittest.main()
