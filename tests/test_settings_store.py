import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import cast

from tests.bootstrap import (
    SETTINGS_NAME,
    Band,
    LevelScale,
    Preset,
    Settings,
    SettingsFormatError,
    SettingsNotFoundError,
    SettingsReadError,
    SettingsSaveError,
    SettingsVersionError,
    load_settings,
    load_settings_at_startup,
    parse_settings,
    quarantine_corrupted,
    save_settings,
    settings_to_data,
    settings_to_text,
    validate_settings,
)

PROJECT_SETTINGS = Path(__file__).resolve().parent.parent / SETTINGS_NAME


def make_bands(count: int = 3, level: float = 0.0) -> tuple[Band, ...]:
    return tuple(Band(500.0 * (index + 1), level) for index in range(count))


def make_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "seconds_per_tone": 10.0,
        "sample_rate": 48000,
        "fade_ms": 20.0,
        "master_gain_db": 0.0,
        "device": None,
        "bands": make_bands(),
        "presets": (),
        "active_preset": None,
    }
    values.update(changes)
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def with_preset(**changes: object) -> Settings:
    preset = Preset(name="Колонки", device="Speakers [MME]", levels=(0.0, 1.5))
    return make_settings(presets=(preset,), active_preset="Колонки", **changes)


class TestValidation(unittest.TestCase):
    def assertInvalid(self, settings: Settings) -> None:
        with self.assertRaises(SettingsFormatError):
            validate_settings(settings)

    def test_valid_settings_pass(self) -> None:
        validate_settings(make_settings())
        validate_settings(with_preset())

    def test_seconds_per_tone_range(self) -> None:
        self.assertInvalid(make_settings(seconds_per_tone=0.5))
        self.assertInvalid(make_settings(seconds_per_tone=3601.0))
        validate_settings(make_settings(seconds_per_tone=3600.0))

    def test_sample_rate_range(self) -> None:
        self.assertInvalid(make_settings(sample_rate=100))
        self.assertInvalid(make_settings(sample_rate=1_000_000))

    def test_fade_range(self) -> None:
        self.assertInvalid(make_settings(fade_ms=0.0))
        self.assertInvalid(make_settings(fade_ms=2000.0))

    def test_master_level_range_and_step(self) -> None:
        self.assertInvalid(make_settings(master_gain_db=10.5))
        self.assertInvalid(make_settings(master_gain_db=-10.5))
        self.assertInvalid(make_settings(master_gain_db=0.3))
        validate_settings(make_settings(master_gain_db=10.0))
        validate_settings(make_settings(master_gain_db=-10.0))

    def test_custom_scale_allows_wider_levels(self) -> None:
        scale = LevelScale(min_db=-12.0, max_db=12.0, step_db=1.0)

        validate_settings(
            make_settings(level_scale=scale, bands=(Band(500.0, 12.0), Band(1000.0, -12.0)))
        )
        self.assertInvalid(make_settings(level_scale=scale, bands=(Band(500.0, 12.5),)))

    def test_scale_with_coarse_step(self) -> None:
        scale = LevelScale(min_db=-10.0, max_db=10.0, step_db=2.5)

        validate_settings(
            make_settings(
                level_scale=scale,
                bands=(Band(500.0, -10.0), Band(1000.0, 7.5)),
            )
        )

    def test_scale_is_validated(self) -> None:
        cases = (
            LevelScale(min_db=0.0, max_db=10.0),
            LevelScale(min_db=-10.0, max_db=0.0),
            LevelScale(min_db=-40.0, max_db=10.0),
            LevelScale(min_db=-10.0, max_db=40.0),
            LevelScale(min_db=-10.0, max_db=10.0, step_db=0.0),
            LevelScale(min_db=-10.0, max_db=10.0, step_db=0.3),
            LevelScale(min_db=-10.0, max_db=10.0, step_db=0.8),
        )
        for scale in cases:
            with self.subTest(scale=scale):
                self.assertInvalid(make_settings(level_scale=scale))

    def test_autoplay_is_boolean(self) -> None:
        self.assertInvalid(make_settings(autoplay=1))

    def test_device_must_not_be_blank(self) -> None:
        self.assertInvalid(make_settings(device="  "))

    def test_band_count(self) -> None:
        self.assertInvalid(make_settings(bands=()))
        self.assertInvalid(make_settings(bands=make_bands(65)))

    def test_frequency_must_be_below_nyquist(self) -> None:
        bands = (Band(500.0, 0.0), Band(24000.0, 0.0))
        self.assertInvalid(make_settings(bands=bands))
        validate_settings(make_settings(bands=bands, sample_rate=96000))

    def test_frequency_lower_bound(self) -> None:
        self.assertInvalid(make_settings(bands=(Band(19.0, 0.0),)))
        validate_settings(make_settings(bands=(Band(20.0, 0.0),)))

    def test_frequencies_must_increase(self) -> None:
        self.assertInvalid(make_settings(bands=(Band(1000.0, 0.0), Band(500.0, 0.0))))
        self.assertInvalid(make_settings(bands=(Band(1000.0, 0.0), Band(1000.0, 0.0))))

    def test_band_level_range_and_step(self) -> None:
        self.assertInvalid(make_settings(bands=(Band(500.0, 10.5),)))
        self.assertInvalid(make_settings(bands=(Band(500.0, -10.5),)))
        self.assertInvalid(make_settings(bands=(Band(500.0, 1.2),)))
        validate_settings(make_settings(bands=(Band(500.0, 10.0), Band(1000.0, -10.0))))

    def test_non_finite_values_are_rejected(self) -> None:
        self.assertInvalid(make_settings(bands=(Band(float("nan"), 0.0),)))
        self.assertInvalid(make_settings(bands=(Band(500.0, float("inf")),)))

    def test_duplicate_preset_names_are_rejected_ignoring_case(self) -> None:
        first = Preset("Колонки", None, (0.0,))
        second = Preset("колонки", None, (0.0,))
        self.assertInvalid(make_settings(presets=(first, second)))

    def test_preset_name_rules(self) -> None:
        for name in ("", " x", "x ", "a" * 65, "a\tb"):
            with self.subTest(name=name):
                preset = Preset(name, None, (0.0,))
                self.assertInvalid(make_settings(presets=(preset,)))

    def test_preset_levels_are_checked(self) -> None:
        for level in (10.5, -10.5, 1.2):
            with self.subTest(level=level):
                preset = Preset("x", None, (level,))
                self.assertInvalid(make_settings(presets=(preset,)))

    def test_preset_level_count(self) -> None:
        self.assertInvalid(make_settings(presets=(Preset("x", None, ()),)))
        self.assertInvalid(make_settings(presets=(Preset("x", None, (0.0,) * 65),)))

    def test_preset_may_have_different_level_count_than_global_bands(self) -> None:
        shorter = Preset("Короче", None, (0.0, 1.0))
        longer = Preset("Длиннее", None, (0.0, 1.0, 2.0, 3.0, 4.0))

        validate_settings(make_settings(presets=(shorter, longer)))

    def test_active_preset_must_exist(self) -> None:
        self.assertInvalid(make_settings(active_preset="нет такого"))


class TestParsing(unittest.TestCase):
    def data(self) -> dict[str, object]:
        return copy.deepcopy(settings_to_data(with_preset()))

    def assertRejected(self, data: dict[str, object]) -> None:
        with self.assertRaises(SettingsFormatError):
            _ = parse_settings(data)

    def test_roundtrip_through_data(self) -> None:
        settings = with_preset()

        payload = cast("object", json.loads(json.dumps(settings_to_data(settings))))
        restored = parse_settings(payload)

        self.assertEqual(restored, settings)

    def test_missing_top_level_key(self) -> None:
        for key in ("playback", "output", "levels", "bands", "presets", "active_preset"):
            with self.subTest(key=key):
                data = self.data()
                del data[key]
                self.assertRejected(data)

    def test_unknown_key_is_rejected(self) -> None:
        data = self.data()
        data["extra"] = 1
        self.assertRejected(data)

    def test_unknown_key_in_section_is_rejected(self) -> None:
        data = self.data()
        playback = data["playback"]
        assert isinstance(playback, dict)
        playback["extra"] = 1  # pyright: ignore[reportUnknownVariableType]
        self.assertRejected(data)

    def test_missing_parameter_in_section(self) -> None:
        data = self.data()
        output = data["output"]
        assert isinstance(output, dict)
        del output["master_gain_db"]  # pyright: ignore[reportUnknownVariableType]
        self.assertRejected(data)

    def test_wrong_types(self) -> None:
        cases: dict[str, object] = {
            "playback": [],
            "output": "x",
            "bands": {},
            "presets": 3,
            "active_preset": 5,
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                data = self.data()
                data[key] = value
                self.assertRejected(data)

    def test_number_types_are_strict(self) -> None:
        for path, value in (
            (("playback", "sample_rate"), 48000.0),
            (("playback", "sample_rate"), True),
            (("playback", "seconds_per_tone"), "10"),
            (("output", "master_gain_db"), None),
            (("output", "device"), 5),
            (("output", "device"), None),
        ):
            with self.subTest(path=path, value=value):
                data = self.data()
                section = data[path[0]]
                assert isinstance(section, dict)
                section[path[1]] = value  # pyright: ignore[reportUnknownVariableType]
                self.assertRejected(data)

    def test_default_device_is_serialized_as_default(self) -> None:
        data = settings_to_data(make_settings())

        output = data["output"]
        assert isinstance(output, dict)
        self.assertEqual(output["device"], "default")

    def test_default_device_token_is_accepted(self) -> None:
        data = self.data()
        output = data["output"]
        assert isinstance(output, dict)
        output["device"] = "default"  # pyright: ignore[reportUnknownVariableType]

        self.assertIsNone(parse_settings(data).device)

    def test_default_preset_device_is_serialized_as_default(self) -> None:
        preset = Preset("Колонки", None, (0.0, 1.5))
        data = settings_to_data(make_settings(presets=(preset,), active_preset="Колонки"))

        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        self.assertEqual(item["device"], "default")
        self.assertIsNone(parse_settings(data).presets[0].device)

    def test_preset_is_serialized_with_levels_only(self) -> None:
        data = settings_to_data(with_preset())

        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        self.assertEqual(item["levels"], [0.0, 1.5])
        self.assertNotIn("bands", item)

    def test_positional_preset_levels_are_parsed(self) -> None:
        data = self.data()
        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        item["levels"] = [2.0, -3.0, 1.0]

        self.assertEqual(parse_settings(data).presets[0].levels, (2.0, -3.0, 1.0))

    def test_integers_are_accepted_as_numbers(self) -> None:
        data = self.data()
        playback = data["playback"]
        assert isinstance(playback, dict)
        playback["seconds_per_tone"] = 10  # pyright: ignore[reportUnknownVariableType]

        self.assertEqual(parse_settings(data).seconds_per_tone, 10.0)

    def test_band_must_have_exact_keys(self) -> None:
        data = self.data()
        bands = data["bands"]
        assert isinstance(bands, list)
        bands[0] = {"frequency_hz": 500.0}  # pyright: ignore[reportUnknownVariableType]
        self.assertRejected(data)

    def test_preset_must_have_exact_keys(self) -> None:
        data = self.data()
        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        item["extra"] = 1

        self.assertRejected(data)

    def test_old_preset_bands_are_rejected(self) -> None:
        data = self.data()
        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        del item["levels"]
        item["bands"] = [{"frequency_hz": 500.0, "level_db": 0.0}]

        self.assertRejected(data)

    def test_preset_levels_must_be_numbers(self) -> None:
        data = self.data()
        presets = data["presets"]
        assert isinstance(presets, list)
        item = cast("dict[str, object]", presets[0])
        item["levels"] = [0.0, "громко"]

        self.assertRejected(data)

    def test_unsupported_schema_version(self) -> None:
        for version in (2, 3, 99):
            with self.subTest(version=version):
                data = self.data()
                data["schema_version"] = version
                with self.assertRaises(SettingsVersionError):
                    _ = parse_settings(data)

    def test_schema_version_must_be_integer(self) -> None:
        data = self.data()
        data["schema_version"] = "1"
        self.assertRejected(data)

    def test_root_must_be_object(self) -> None:
        with self.assertRaises(SettingsFormatError):
            _ = parse_settings([])


class TestFile(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / SETTINGS_NAME

    def names(self) -> list[str]:
        return sorted(item.name for item in self.path.parent.iterdir())

    def test_save_and_load_roundtrip(self) -> None:
        settings = with_preset(device="Speakers [MME]")

        save_settings(self.path, settings)

        self.assertEqual(load_settings(self.path), settings)
        self.assertEqual(self.names(), [SETTINGS_NAME])

    def test_text_is_stable_and_bands_are_one_line(self) -> None:
        text = settings_to_text(with_preset())

        self.assertTrue(text.endswith("\n"))
        self.assertIn('    {"frequency_hz": 500.0, "level_db": 0.0},', text)
        self.assertIn('"name": "Колонки"', text)
        self.assertEqual(text, settings_to_text(parse_settings(cast("object", json.loads(text)))))
        self.assertEqual(json.loads(text), settings_to_data(with_preset()))

    def test_overwrite_replaces_content(self) -> None:
        save_settings(self.path, make_settings())

        save_settings(self.path, make_settings(master_gain_db=-6.0))

        self.assertEqual(load_settings(self.path).master_gain_db, -6.0)
        self.assertEqual(self.names(), [SETTINGS_NAME])

    def test_invalid_settings_are_not_written(self) -> None:
        save_settings(self.path, make_settings())
        before = self.path.read_bytes()

        with self.assertRaises(SettingsSaveError):
            save_settings(self.path, make_settings(master_gain_db=11.0))

        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.names(), [SETTINGS_NAME])

    def test_save_to_missing_directory_fails(self) -> None:
        missing = self.path.parent / "нет" / SETTINGS_NAME

        with self.assertRaises(SettingsSaveError):
            save_settings(missing, make_settings())

    def test_missing_file_is_an_error_without_defaults(self) -> None:
        with self.assertRaises(SettingsNotFoundError):
            _ = load_settings(self.path)
        self.assertEqual(self.names(), [])

    def test_directory_instead_of_file(self) -> None:
        self.path.mkdir()

        with self.assertRaises(SettingsNotFoundError):
            _ = load_settings(self.path)

    def test_broken_json(self) -> None:
        self.path.write_text('{"schema_version": 1,', encoding="utf-8")

        with self.assertRaises(SettingsFormatError):
            _ = load_settings(self.path)

    def test_empty_file(self) -> None:
        self.path.write_bytes(b"")

        with self.assertRaises(SettingsFormatError):
            _ = load_settings(self.path)

    def test_invalid_utf8(self) -> None:
        self.path.write_bytes(b"\xff\xfe\x00")

        with self.assertRaises(SettingsFormatError):
            _ = load_settings(self.path)

    def test_utf8_bom_is_accepted(self) -> None:
        text = settings_to_text(make_settings())
        self.path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))

        self.assertEqual(load_settings(self.path), make_settings())

    def test_nan_literal_is_rejected(self) -> None:
        data = settings_to_text(make_settings()).replace('"fade_ms": 20.0', '"fade_ms": NaN')
        self.path.write_text(data, encoding="utf-8")

        with self.assertRaises(SettingsFormatError):
            _ = load_settings(self.path)

    def test_unreadable_file_is_a_read_error(self) -> None:
        save_settings(self.path, make_settings())
        original = Path.read_bytes

        def broken(_self: Path) -> bytes:
            raise PermissionError("нет доступа")

        Path.read_bytes = broken  # type: ignore[method-assign]
        self.addCleanup(setattr, Path, "read_bytes", original)

        with self.assertRaises(SettingsReadError):
            _ = load_settings(self.path)


class TestQuarantine(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / SETTINGS_NAME
        self.now = datetime(2026, 10, 21, 9, 5, 7)

    def test_file_is_renamed_with_timestamp(self) -> None:
        self.path.write_text("мусор", encoding="utf-8")

        target = quarantine_corrupted(self.path, self.now)

        self.assertEqual(target.name, "settings.corrupted-20261021-090507.json")
        self.assertFalse(self.path.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "мусор")

    def test_existing_copies_are_not_overwritten(self) -> None:
        self.path.write_text("первый", encoding="utf-8")
        first = quarantine_corrupted(self.path, self.now)
        self.path.write_text("второй", encoding="utf-8")

        second = quarantine_corrupted(self.path, self.now)

        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "первый")
        self.assertEqual(second.read_text(encoding="utf-8"), "второй")

    def test_missing_file_is_an_error(self) -> None:
        with self.assertRaises(SettingsSaveError):
            _ = quarantine_corrupted(self.path, self.now)


class TestStartupLoad(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)
        self.path = self.directory / SETTINGS_NAME

    def names(self) -> list[str]:
        return sorted(item.name for item in self.directory.iterdir())

    def test_valid_file_is_loaded(self) -> None:
        save_settings(self.path, with_preset())

        self.assertEqual(load_settings_at_startup(self.path), with_preset())
        self.assertEqual(self.names(), [SETTINGS_NAME])

    def test_missing_file_is_not_created(self) -> None:
        with self.assertRaises(SettingsNotFoundError):
            _ = load_settings_at_startup(self.path)

        self.assertEqual(self.names(), [])

    def test_corrupted_file_is_kept_under_another_name(self) -> None:
        self.path.write_text('{"schema_version": 1', encoding="utf-8")

        with self.assertRaises(SettingsFormatError) as caught:
            _ = load_settings_at_startup(self.path)

        names = self.names()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("settings.corrupted-"))
        self.assertIn(names[0], str(caught.exception))
        self.assertEqual(
            (self.directory / names[0]).read_text(encoding="utf-8"), '{"schema_version": 1'
        )

    def test_invalid_values_count_as_corruption(self) -> None:
        data = settings_to_data(make_settings())
        data["output"] = {"device": "default", "master_gain_db": 11.0}
        self.path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(SettingsFormatError):
            _ = load_settings_at_startup(self.path)

        self.assertFalse(self.path.exists())

    def test_other_schema_version_is_left_in_place(self) -> None:
        data = settings_to_data(make_settings())
        data["schema_version"] = 99
        self.path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(SettingsVersionError):
            _ = load_settings_at_startup(self.path)

        self.assertEqual(self.names(), [SETTINGS_NAME])


class TestShippedSettings(unittest.TestCase):
    def test_shipped_file_is_valid(self) -> None:
        settings = load_settings(PROJECT_SETTINGS)

        self.assertEqual(settings.frequencies, tuple(float(f) for f in range(500, 10001, 500)))
        self.assertEqual(set(settings.levels), {0.0})
        self.assertEqual(settings.master_gain_db, 0.0)
        self.assertEqual(settings.presets, ())

    def test_shipped_file_is_in_canonical_form(self) -> None:
        text = PROJECT_SETTINGS.read_text(encoding="utf-8")

        self.assertEqual(text, settings_to_text(load_settings(PROJECT_SETTINGS)))


if __name__ == "__main__":
    unittest.main()
