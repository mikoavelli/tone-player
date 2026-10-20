import unittest

from tests.bootstrap import apply_measurement


class TestApplyMeasurement(unittest.TestCase):
    def test_too_quiet_raises_gain(self) -> None:
        result = apply_measurement(gain_db=-12.0, offset_db=0.0, measured_db=87.0, target_db=90.0)

        self.assertAlmostEqual(result.new_gain_db, -9.0)
        self.assertAlmostEqual(result.correction_db, 3.0)
        self.assertTrue(result.changed)
        self.assertEqual(result.shortfall_db, 0.0)
        self.assertFalse(result.clamped_low)

    def test_too_loud_lowers_gain(self) -> None:
        result = apply_measurement(gain_db=-12.0, offset_db=0.0, measured_db=93.5, target_db=90.0)

        self.assertAlmostEqual(result.new_gain_db, -15.5)

    def test_on_target_changes_nothing(self) -> None:
        result = apply_measurement(gain_db=-12.0, offset_db=0.0, measured_db=90.0, target_db=90.0)

        self.assertEqual(result.new_gain_db, -12.0)
        self.assertFalse(result.changed)

    def test_slider_is_kept_as_temporary_offset(self) -> None:
        # Измерено при -12 + 3 = -9 дБ; нужно на 2 дБ тише. Общий уровень должен
        # стать -11 дБ при том же положении ползунка, то есть gain = -14.
        result = apply_measurement(gain_db=-12.0, offset_db=3.0, measured_db=92.0, target_db=90.0)

        self.assertAlmostEqual(result.new_gain_db, -14.0)
        self.assertAlmostEqual(result.new_gain_db + 3.0, -11.0)

    def test_clipped_signal_is_measured_at_full_scale(self) -> None:
        # Звучало на полной шкале (0 дБ), хотя gain + ползунок = +6 дБ.
        result = apply_measurement(gain_db=-6.0, offset_db=12.0, measured_db=92.0, target_db=90.0)

        self.assertAlmostEqual(result.new_gain_db + 12.0, -2.0)

    def test_gain_never_exceeds_full_scale(self) -> None:
        result = apply_measurement(gain_db=-2.0, offset_db=0.0, measured_db=80.0, target_db=90.0)

        self.assertEqual(result.new_gain_db, 0.0)
        self.assertAlmostEqual(result.shortfall_db, 8.0)

    def test_gain_is_limited_below(self) -> None:
        result = apply_measurement(gain_db=-90.0, offset_db=0.0, measured_db=120.0, target_db=90.0)

        self.assertEqual(result.new_gain_db, -96.0)
        self.assertTrue(result.clamped_low)
        self.assertEqual(result.shortfall_db, 0.0)

    def test_result_is_rounded_to_tenth(self) -> None:
        result = apply_measurement(gain_db=-12.0, offset_db=0.0, measured_db=89.84, target_db=90.0)

        self.assertEqual(result.new_gain_db, -11.8)


if __name__ == "__main__":
    unittest.main()
