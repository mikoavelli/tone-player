import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from models import ToneConfig  # noqa: E402
from ui.main_window import MainWindow, format_duration, format_frequency  # noqa: E402


def make_config() -> ToneConfig:
    return ToneConfig(
        frequencies=(500.0, 31500.0),
        gain_db=(-12.0, -6.0),
        seconds_per_tone=10.0,
        target_spl_db=90.0,
        sample_rate=48000,
        fade_ms=20.0,
        device=None,
        default_offset_db=0.0,
    )


class TestFormatters(unittest.TestCase):
    def testformat_frequency(self) -> None:
        self.assertEqual(format_frequency(31500.0), "31 500 Гц")
        self.assertEqual(format_frequency(500.0), "500 Гц")

    def testformat_duration(self) -> None:
        self.assertEqual(format_duration(0.0), "0:00")
        self.assertEqual(format_duration(9.6), "0:10")
        self.assertEqual(format_duration(9.1), "0:10")
        self.assertEqual(format_duration(8.9), "0:09")
        self.assertEqual(format_duration(200.0), "3:20")
        self.assertEqual(format_duration(-5.0), "0:00")


class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._window = MainWindow(config=make_config(), base_directory=Path(__file__))
        self.addCleanup(self._window.close)

    def test_initial_state(self) -> None:
        self.assertEqual(self._window.frequency_label.text(), "—")
        self.assertEqual(self._window.tone_label.text(), "Тон 1 из 2")
        self.assertIn("0:20", self._window.remaining_label.text())
        self.assertTrue(self._window.start_button.isEnabled())
        self.assertFalse(self._window.pause_button.isEnabled())
        self.assertFalse(self._window.stop_button.isEnabled())

    def test_slider_updates_player_offset(self) -> None:
        self._window.level_slider.setValue(-6)

        self.assertEqual(self._window.player.offset_db, -6.0)
        self.assertIn("-6.0 дБ", self._window.level_label.text())
        self.assertIn("цель: 90 дБ", self._window.level_label.text())

    def test_level_label_shows_current_tone_gain(self) -> None:
        self.assertIn("тон: -12.0 дБ", self._window.level_label.text())


if __name__ == "__main__":
    unittest.main()
