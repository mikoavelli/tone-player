import unittest

from equalizer import (
    Reading,
    flatten,
    generate_grid,
    insert_band,
    remove_band,
    reset_levels,
    set_frequency,
    with_levels,
)
from tests.bootstrap import Band, BandError

NYQUIST = 24000.0


def bands(*frequencies: float, level: float = 0.0) -> tuple[Band, ...]:
    return tuple(Band(frequency, level) for frequency in frequencies)


class TestGrid(unittest.TestCase):
    def test_even_grid(self) -> None:
        grid = generate_grid(500.0, 500.0, 20, max_frequency_hz=NYQUIST)

        self.assertEqual([band.frequency_hz for band in grid], [500.0 * n for n in range(1, 21)])
        self.assertEqual({band.level_db for band in grid}, {0.0})

    def test_fractional_step_has_no_float_noise(self) -> None:
        grid = generate_grid(100.0, 0.1, 4, max_frequency_hz=NYQUIST)

        self.assertEqual([band.frequency_hz for band in grid], [100.0, 100.1, 100.2, 100.3])

    def test_invalid_parameters(self) -> None:
        cases = (
            (500.0, 0.0, 5),
            (500.0, -1.0, 5),
            (500.0, 500.0, 0),
            (500.0, 500.0, 65),
            (10.0, 500.0, 5),
            (500.0, 10000.0, 5),
            (float("nan"), 500.0, 5),
        )
        for start, step, count in cases:
            with self.subTest(start=start, step=step, count=count), self.assertRaises(BandError):
                _ = generate_grid(start, step, count, max_frequency_hz=NYQUIST)


class TestEditing(unittest.TestCase):
    def test_insert_keeps_order_and_levels(self) -> None:
        source = (Band(500.0, 1.0), Band(1500.0, -2.0))

        result = insert_band(source, 1000.0, max_frequency_hz=NYQUIST)

        self.assertEqual(result, (Band(500.0, 1.0), Band(1000.0, 0.0), Band(1500.0, -2.0)))

    def test_insert_duplicate_or_out_of_range(self) -> None:
        source = bands(500.0, 1000.0)
        for frequency in (500.0, 10.0, 30000.0):
            with self.subTest(frequency=frequency), self.assertRaises(BandError):
                _ = insert_band(source, frequency, max_frequency_hz=NYQUIST)

    def test_insert_over_limit(self) -> None:
        source = tuple(Band(100.0 * (n + 1), 0.0) for n in range(64))

        with self.assertRaises(BandError):
            _ = insert_band(source, 6500.0, max_frequency_hz=NYQUIST)

    def test_remove(self) -> None:
        result = remove_band((Band(500.0, 1.0), Band(1000.0, 2.0), Band(1500.0, 3.0)), 1)

        self.assertEqual(result, (Band(500.0, 1.0), Band(1500.0, 3.0)))

    def test_remove_last_band_is_refused(self) -> None:
        with self.assertRaises(BandError):
            _ = remove_band(bands(500.0), 0)

    def test_remove_bad_index(self) -> None:
        with self.assertRaises(BandError):
            _ = remove_band(bands(500.0, 1000.0), 2)

    def test_set_frequency_keeps_level(self) -> None:
        source = (Band(500.0, 1.0), Band(1000.0, 2.0), Band(1500.0, 3.0))

        result = set_frequency(source, 1, 1200.0, max_frequency_hz=NYQUIST)

        self.assertEqual(result[1], Band(1200.0, 2.0))

    def test_set_frequency_cannot_reorder_or_collide(self) -> None:
        source = bands(500.0, 1000.0, 1500.0)
        for frequency in (400.0, 500.0, 1500.0, 1800.0, 30000.0):
            with self.subTest(frequency=frequency), self.assertRaises(BandError):
                _ = set_frequency(source, 1, frequency, max_frequency_hz=NYQUIST)

    def test_with_levels_and_reset(self) -> None:
        source = bands(500.0, 1000.0)

        leveled = with_levels(source, (1.5, -2.0))

        self.assertEqual(leveled, (Band(500.0, 1.5), Band(1000.0, -2.0)))
        self.assertEqual(reset_levels(leveled), source)
        with self.assertRaises(BandError):
            _ = with_levels(source, (1.0,))


class TestFlatten(unittest.TestCase):
    def test_louder_bands_are_lowered_to_the_quietest(self) -> None:
        # Все показания сняты при одном уровне сигнала (−12 дБ).
        readings = {
            0: Reading(90.0, -12.0),
            1: Reading(86.0, -12.0),
            2: Reading(88.5, -12.0),
        }

        result = flatten((0.0, 0.0, 0.0), readings, master_db=-12.0)

        self.assertEqual(result.reference_index, 1)
        self.assertEqual(result.levels, (-4.0, 0.0, -2.5))
        self.assertEqual(result.changed, (0, 2))
        self.assertEqual(result.clamped, ())
        self.assertAlmostEqual(result.spread_db, 4.0)

    def test_result_is_flat_after_rounding(self) -> None:
        sensitivities = {0: 100.0, 1: 97.3, 2: 95.1, 3: 98.8}
        readings = {index: Reading(value - 12.0, -12.0) for index, value in sensitivities.items()}

        result = flatten((0.0,) * 4, readings, master_db=-12.0)

        # Опорная полоса 2 сохраняет давление 95,1 − 12 дБ; остальные попадают в него
        # с ошибкой округления до шага не больше половины шага (0,25 дБ).
        target = sensitivities[2] - 12.0
        for index in sensitivities:
            resulting = sensitivities[index] - 12.0 + result.levels[index]
            self.assertLessEqual(abs(resulting - target), 0.25 + 1e-9)

    def test_reference_keeps_its_level(self) -> None:
        readings = {0: Reading(80.0, -6.0), 1: Reading(83.0, -6.0)}

        result = flatten((2.0, 2.0), readings, master_db=-8.0)

        self.assertEqual(result.reference_index, 0)
        self.assertEqual(result.levels[0], 2.0)
        self.assertEqual(result.levels[1], -1.0)

    def test_readings_taken_at_different_levels_are_normalised(self) -> None:
        # Полоса 0 измерена при −12 дБ, полоса 1 — при −6 дБ (двигали ползунок).
        # Чувствительность: 78 и 80 → полоса 0 тише на 2 дБ.
        readings = {0: Reading(66.0, -12.0), 1: Reading(74.0, -6.0)}

        result = flatten((0.0, 0.0), readings, master_db=-12.0)

        self.assertEqual(result.reference_index, 0)
        self.assertEqual(result.levels, (0.0, -2.0))

    def test_unmeasured_bands_are_untouched(self) -> None:
        # Показания сняты при текущих уровнях: master −12 дБ плюс поправки 1 и 3 дБ.
        readings = {0: Reading(90.0, -11.0), 2: Reading(87.0, -9.0)}

        result = flatten((1.0, 2.0, 3.0), readings, master_db=-12.0)

        self.assertEqual(result.levels[1], 2.0)
        self.assertEqual(result.levels[0], -2.0)
        self.assertEqual(result.levels[2], 3.0)

    def test_needed_correction_beyond_range_is_clamped_and_reported(self) -> None:
        readings = {0: Reading(100.0, -12.0), 1: Reading(80.0, -12.0)}

        result = flatten((0.0, 0.0), readings, master_db=-12.0)

        self.assertEqual(result.levels, (-10.0, 0.0))
        self.assertEqual(result.clamped, (0,))

    def test_clipped_signal_is_measured_at_full_scale(self) -> None:
        # Мастер 0 дБ и поправка +10 дБ: звучал сигнал 0 дБ (обрезан).
        readings = {0: Reading(90.0, 0.0), 1: Reading(90.0, 0.0)}

        result = flatten((10.0, 10.0), readings, master_db=0.0)

        self.assertEqual(result.levels, (0.0, 0.0))

    def test_already_flat_response_changes_nothing(self) -> None:
        # Одинаковое давление при текущих уровнях −11 и −13 дБ: выравнивать нечего.
        readings = {0: Reading(90.0, -11.0), 1: Reading(90.0, -13.0)}

        result = flatten((1.0, -1.0), readings, master_db=-12.0)

        self.assertEqual(result.changed, ())
        self.assertEqual(result.levels, (1.0, -1.0))

    def test_equal_sensitivity_gives_equal_levels(self) -> None:
        # Одинаковая чувствительность при разных уровнях: полосы выравниваются
        # по уровню опорной (меньший индекс).
        readings = {0: Reading(89.0, -11.0), 1: Reading(87.0, -13.0)}

        result = flatten((1.0, -1.0), readings, master_db=-12.0)

        self.assertEqual(result.reference_index, 0)
        self.assertEqual(result.levels, (1.0, 1.0))
        self.assertEqual(result.changed, (1,))

    def test_tie_prefers_lower_index(self) -> None:
        readings = {3: Reading(90.0, -12.0), 1: Reading(90.0, -12.0)}

        self.assertEqual(flatten((0.0,) * 4, readings, -12.0).reference_index, 1)

    def test_levels_stay_on_the_step_grid(self) -> None:
        readings = {0: Reading(90.37, -12.0), 1: Reading(86.11, -12.0), 2: Reading(88.9, -12.0)}

        result = flatten((0.0, 0.0, 0.0), readings, master_db=-12.0)

        for level in result.levels:
            self.assertEqual(level, round(level * 2) / 2)

    def test_needs_two_readings(self) -> None:
        with self.assertRaises(BandError):
            _ = flatten((0.0, 0.0), {0: Reading(90.0, -12.0)}, -12.0)
        with self.assertRaises(BandError):
            _ = flatten((0.0, 0.0), {}, -12.0)

    def test_bad_index_and_non_finite(self) -> None:
        with self.assertRaises(BandError):
            _ = flatten((0.0, 0.0), {0: Reading(90.0, -12.0), 5: Reading(90.0, -12.0)}, -12.0)
        with self.assertRaises(BandError):
            _ = flatten(
                (0.0, 0.0),
                {0: Reading(90.0, -12.0), 1: Reading(float("nan"), -12.0)},
                -12.0,
            )


if __name__ == "__main__":
    unittest.main()
