import tempfile
import unittest
from pathlib import Path

from tests.bootstrap import (
    ConfigNotFoundError,
    ConfigSaveError,
    ConfigValidationError,
    load_config,
    save_gain_db,
)

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

    def test_rejects_frequency_above_nyquist(self) -> None:
        self._write(VALID_CONFIG.replace("f_i = [500, 1000]", "f_i = [500, 25000]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_rejects_frequency_at_nyquist(self) -> None:
        self._write(VALID_CONFIG.replace("f_i = [500, 1000]", "f_i = [500, 24000]"))
        with self.assertRaises(ConfigValidationError):
            _ = load_config(self._path)

    def test_accepts_high_frequency_with_higher_sample_rate(self) -> None:
        self._write(
            VALID_CONFIG.replace("f_i = [500, 1000]", "f_i = [500, 25000]").replace(
                "sample_rate = 48000", "sample_rate = 96000"
            )
        )
        config = load_config(self._path)
        self.assertEqual(config.frequencies, (500.0, 25000.0))

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


COMMENTED_CONFIG = """# Заголовок файла.
[playback]
number_of_constants = 7
f_i = [500, 1000, 1500, 2000, 2500, 3000, 3500]  # частоты
seconds_per_tone = 10.0
target_spl_db = 90.0

# Калибровка: комментарий с [скобками] и gain_db_i = [1] внутри.
[calibration]
# Ослабление, дБ.
gain_db_i = [
    -12.0, -12.0, -12.0, -12.0, -12.0,
    -12.0, -12.0
]
# Хвостовой комментарий секции.

[output]
sample_rate = 48000
fade_ms = 20.0
device = "default"
default_offset_db = 0.0
"""

NEW_GAINS = (-10.5, -12.0, -9.0, -12.0, -12.0, -3.1, -96.0)


class TestSaveGain(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "config.toml"

    def _write(self, text: str) -> None:
        self._path.write_bytes(text.encode("utf-8"))

    def test_saved_values_are_loaded_back(self) -> None:
        self._write(COMMENTED_CONFIG)

        save_gain_db(self._path, NEW_GAINS)

        self.assertEqual(load_config(self._path).gain_db, NEW_GAINS)

    def test_only_gain_block_changes(self) -> None:
        self._write(COMMENTED_CONFIG)

        save_gain_db(self._path, NEW_GAINS)

        saved = self._path.read_bytes().decode("utf-8")
        expected = COMMENTED_CONFIG.replace(
            "    -12.0, -12.0, -12.0, -12.0, -12.0,\n    -12.0, -12.0\n",
            "    -10.5, -12.0, -9.0, -12.0, -12.0,\n    -3.1, -96.0\n",
        )
        self.assertEqual(saved, expected)

    def test_unchanged_values_keep_file_bytes(self) -> None:
        self._write(COMMENTED_CONFIG)
        before = self._path.stat().st_mtime_ns

        save_gain_db(self._path, (-12.0,) * 7)

        self.assertEqual(self._path.read_bytes().decode("utf-8"), COMMENTED_CONFIG)
        self.assertEqual(self._path.stat().st_mtime_ns, before)

    def test_crlf_line_endings_are_kept(self) -> None:
        self._write(COMMENTED_CONFIG.replace("\n", "\r\n"))

        save_gain_db(self._path, NEW_GAINS)

        raw = self._path.read_bytes().decode("utf-8")
        self.assertNotIn("\n", raw.replace("\r\n", ""))
        self.assertEqual(load_config(self._path).gain_db, NEW_GAINS)

    def test_short_list_is_written_in_one_line(self) -> None:
        self._write(VALID_CONFIG)

        save_gain_db(self._path, (-3.0, -4.5))

        self.assertIn("gain_db_i = [-3.0, -4.5]", self._path.read_text(encoding="utf-8"))

    def test_same_key_in_other_section_is_untouched(self) -> None:
        self._write("[playback]\ngain_db_i = [1]\n\n[calibration]\ngain_db_i = [-12.0, -12.0]\n")

        save_gain_db(self._path, (-3.0, -4.5))

        saved = self._path.read_text(encoding="utf-8")
        self.assertIn("[playback]\ngain_db_i = [1]\n", saved)
        self.assertIn("[calibration]\ngain_db_i = [-3.0, -4.5]\n", saved)

    def test_rejects_value_out_of_range(self) -> None:
        self._write(COMMENTED_CONFIG)

        with self.assertRaises(ConfigSaveError):
            save_gain_db(self._path, (-12.0, -12.0, -12.0, -12.0, -12.0, -12.0, 1.0))

        self.assertEqual(self._path.read_bytes().decode("utf-8"), COMMENTED_CONFIG)

    def test_rejects_missing_calibration_section(self) -> None:
        self._write(VALID_CONFIG.replace("[calibration]", "[other]"))

        with self.assertRaises(ConfigSaveError):
            save_gain_db(self._path, (-3.0, -4.5))

    def test_missing_file(self) -> None:
        with self.assertRaises(ConfigSaveError):
            save_gain_db(self._path, (-3.0,))

    def test_no_temporary_files_are_left(self) -> None:
        self._write(COMMENTED_CONFIG)

        save_gain_db(self._path, NEW_GAINS)

        self.assertEqual([item.name for item in self._path.parent.iterdir()], ["config.toml"])

    def test_project_config_roundtrip_keeps_comments(self) -> None:
        project = Path(__file__).resolve().parent.parent / "config.toml"
        original = project.read_text(encoding="utf-8")
        self._write(original)
        gains = tuple(-12.0 + 0.5 * index for index in range(20))

        save_gain_db(self._path, gains)

        saved = self._path.read_text(encoding="utf-8")
        self.assertEqual(load_config(self._path).gain_db, gains)
        self.assertEqual(
            [line for line in saved.splitlines() if line.lstrip().startswith("#")],
            [line for line in original.splitlines() if line.lstrip().startswith("#")],
        )


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
        self.assertEqual(config.frequencies[-1], 10000.0)


if __name__ == "__main__":
    unittest.main()
