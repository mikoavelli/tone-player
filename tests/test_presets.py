import unittest
from dataclasses import replace

from models import Band, Preset, Settings
from presets import (
    apply_preset,
    delete_preset,
    find_preset,
    is_modified,
    normalize_name,
    rename_preset,
    save_preset,
)
from tests.bootstrap import PresetError, validate_settings


def bands(*levels: float) -> tuple[Band, ...]:
    return tuple(Band(500.0 * (index + 1), level) for index, level in enumerate(levels))


def make_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "seconds_per_tone": 10.0,
        "sample_rate": 48000,
        "fade_ms": 20.0,
        "master_gain_db": 0.0,
        "device": "Speakers [MME]",
        "bands": bands(0.0, 1.0, -2.5),
        "presets": (),
        "active_preset": None,
    }
    values.update(changes)
    return Settings(**values)  # pyright: ignore[reportArgumentType]


class TestSave(unittest.TestCase):
    def test_saves_current_levels_and_device_and_activates(self) -> None:
        settings = save_preset(make_settings(), "Дом")

        self.assertEqual(settings.presets, (Preset("Дом", "Speakers [MME]", (0.0, 1.0, -2.5)),))
        self.assertEqual(settings.active_preset, "Дом")
        validate_settings(settings)

    def test_preset_does_not_contain_frequency_list(self) -> None:
        preset = save_preset(make_settings(), "Дом").presets[0]

        self.assertFalse(hasattr(preset, "bands"))
        self.assertEqual(preset.levels, (0.0, 1.0, -2.5))

    def test_default_device_is_stored_as_none(self) -> None:
        settings = save_preset(make_settings(device=None), "Дом")

        self.assertIsNone(settings.presets[0].device)

    def test_master_level_is_not_part_of_a_preset(self) -> None:
        settings = save_preset(make_settings(master_gain_db=-6.0), "Дом")

        self.assertEqual(settings.master_gain_db, -6.0)
        self.assertFalse(hasattr(settings.presets[0], "master_gain_db"))

    def test_name_is_trimmed(self) -> None:
        self.assertEqual(save_preset(make_settings(), "  Дом  ").presets[0].name, "Дом")

    def test_existing_name_needs_overwrite_ignoring_case(self) -> None:
        settings = save_preset(make_settings(), "Дом")
        changed = replace(settings, bands=bands(3.0, 3.0, 3.0))

        with self.assertRaises(PresetError):
            _ = save_preset(changed, "дом")

        again = save_preset(changed, "дом", overwrite=True)
        self.assertEqual(len(again.presets), 1)
        # Имя сохраняется в том виде, в каком его ввели.
        self.assertEqual(again.presets[0].name, "дом")
        self.assertEqual(again.active_preset, "дом")
        self.assertEqual(again.presets[0].levels, (3.0, 3.0, 3.0))
        validate_settings(again)

    def test_overwrite_keeps_position(self) -> None:
        settings = save_preset(save_preset(make_settings(), "А"), "Б")

        again = save_preset(settings, "А", overwrite=True)

        self.assertEqual([item.name for item in again.presets], ["А", "Б"])
        self.assertEqual(again.active_preset, "А")

    def test_bad_names(self) -> None:
        for name in ("", "   ", "x" * 65, "a\nb"):
            with self.subTest(name=name), self.assertRaises(PresetError):
                _ = save_preset(make_settings(), name)

    def test_normalize_name(self) -> None:
        self.assertEqual(normalize_name(" Дом "), "Дом")
        with self.assertRaises(PresetError):
            _ = normalize_name(" ")


class TestApply(unittest.TestCase):
    def setUp(self) -> None:
        saved = save_preset(make_settings(device="USB [WASAPI]"), "USB")
        self.settings = replace(saved, bands=bands(0.0, 0.0, 0.0), device=None)

    def test_replaces_levels_and_device(self) -> None:
        result = apply_preset(self.settings, "usb", ["USB [WASAPI]"])

        self.assertEqual(result.settings.bands, bands(0.0, 1.0, -2.5))
        self.assertEqual(result.settings.device, "USB [WASAPI]")
        self.assertEqual(result.settings.active_preset, "USB")
        self.assertIsNone(result.missing_device)
        validate_settings(result.settings)

    def test_master_level_is_kept(self) -> None:
        settings = replace(self.settings, master_gain_db=-6.0)

        self.assertEqual(
            apply_preset(settings, "USB", ["USB [WASAPI]"]).settings.master_gain_db, -6.0
        )

    def test_missing_device_falls_back_to_default(self) -> None:
        result = apply_preset(self.settings, "USB", ["Other [MME]"])

        self.assertIsNone(result.settings.device)
        self.assertEqual(result.missing_device, "USB [WASAPI]")
        self.assertEqual(result.settings.bands, bands(0.0, 1.0, -2.5))

    def test_preset_without_device_needs_no_device(self) -> None:
        settings = save_preset(make_settings(device=None), "Любое")

        result = apply_preset(replace(settings, device="X [MME]"), "Любое", [])

        self.assertIsNone(result.settings.device)
        self.assertIsNone(result.missing_device)

    def test_unknown_preset(self) -> None:
        with self.assertRaises(PresetError):
            _ = apply_preset(self.settings, "нет", [])

    def test_apply_uses_current_global_frequencies(self) -> None:
        settings = replace(
            self.settings,
            bands=(Band(400.0, 0.0), Band(800.0, 0.0), Band(1200.0, 0.0)),
        )

        result = apply_preset(settings, "USB", ["USB [WASAPI]"])

        self.assertEqual(
            result.settings.bands,
            (Band(400.0, 0.0), Band(800.0, 1.0), Band(1200.0, -2.5)),
        )

    def test_new_global_bands_get_zero_level(self) -> None:
        settings = replace(self.settings, bands=bands(0.0, 0.0, 0.0, 0.0, 0.0))

        result = apply_preset(settings, "USB", ["USB [WASAPI]"])

        self.assertEqual(result.settings.levels, (0.0, 1.0, -2.5, 0.0, 0.0))

    def test_extra_saved_levels_are_ignored(self) -> None:
        settings = replace(self.settings, bands=bands(0.0, 0.0))

        result = apply_preset(settings, "USB", ["USB [WASAPI]"])

        self.assertEqual(result.settings.levels, (0.0, 1.0))


class TestRenameAndDelete(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = save_preset(save_preset(make_settings(), "А"), "Б")

    def test_rename_updates_active(self) -> None:
        result = rename_preset(self.settings, "Б", "В")

        self.assertEqual([item.name for item in result.presets], ["А", "В"])
        self.assertEqual(result.active_preset, "В")
        validate_settings(result)

    def test_rename_inactive_keeps_active(self) -> None:
        result = rename_preset(self.settings, "А", "Г")

        self.assertEqual(result.active_preset, "Б")

    def test_rename_to_own_name_in_other_case_is_allowed(self) -> None:
        result = rename_preset(self.settings, "А", "а")

        self.assertEqual(result.presets[0].name, "а")

    def test_rename_to_existing_name_is_refused(self) -> None:
        with self.assertRaises(PresetError):
            _ = rename_preset(self.settings, "А", "б")

    def test_rename_unknown(self) -> None:
        with self.assertRaises(PresetError):
            _ = rename_preset(self.settings, "нет", "Д")

    def test_delete_keeps_current_bands(self) -> None:
        result = delete_preset(self.settings, "Б")

        self.assertEqual([item.name for item in result.presets], ["А"])
        self.assertIsNone(result.active_preset)
        self.assertEqual(result.bands, self.settings.bands)
        validate_settings(result)

    def test_delete_inactive_keeps_active(self) -> None:
        self.assertEqual(delete_preset(self.settings, "А").active_preset, "Б")

    def test_delete_unknown(self) -> None:
        with self.assertRaises(PresetError):
            _ = delete_preset(self.settings, "нет")

    def test_find_ignores_case(self) -> None:
        self.assertIsNotNone(find_preset(self.settings, "а"))
        self.assertIsNone(find_preset(self.settings, "нет"))


class TestModified(unittest.TestCase):
    def test_no_active_preset_is_not_modified(self) -> None:
        self.assertFalse(is_modified(make_settings()))

    def test_fresh_preset_is_not_modified(self) -> None:
        self.assertFalse(is_modified(save_preset(make_settings(), "Дом")))

    def test_changed_level_or_device_marks_modified(self) -> None:
        saved = save_preset(make_settings(), "Дом")

        self.assertTrue(is_modified(replace(saved, bands=bands(0.0, 1.5, -2.5))))
        self.assertTrue(is_modified(replace(saved, device=None)))

    def test_master_level_does_not_mark_modified(self) -> None:
        saved = save_preset(make_settings(), "Дом")

        self.assertFalse(is_modified(replace(saved, master_gain_db=-6.0)))

    def test_changed_global_frequencies_do_not_mark_modified(self) -> None:
        saved = save_preset(make_settings(), "Дом")
        changed = replace(
            saved,
            bands=(Band(400.0, 0.0), Band(800.0, 1.0), Band(1200.0, -2.5)),
        )

        self.assertFalse(is_modified(changed))

    def test_extra_global_bands_with_zero_levels_are_not_modified(self) -> None:
        saved = save_preset(make_settings(), "Дом")

        self.assertFalse(is_modified(replace(saved, bands=bands(0.0, 1.0, -2.5, 0.0, 0.0))))


if __name__ == "__main__":
    unittest.main()
