"""Чтение, проверка и запись файла настроек settings.json.

Значений по умолчанию нет: файл обязан существовать и содержать все параметры.
Любое отклонение (синтаксис, тип, диапазон, неизвестный ключ) считается
повреждением файла.
"""

import json
import math
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

from error import (
    SettingsFormatError,
    SettingsNotFoundError,
    SettingsReadError,
    SettingsSaveError,
    SettingsVersionError,
)
from models import (
    DEFAULT_LEVEL_SCALE,
    MASTER_GAIN_SCALE,
    MAX_BANDS,
    MAX_FADE_MS,
    MAX_LEVEL_SCALE_DB,
    MAX_LEVEL_STEP_DB,
    MAX_PRESET_NAME_LENGTH,
    MAX_SAMPLE_RATE,
    MAX_SECONDS_PER_TONE,
    MIN_FADE_MS,
    MIN_FREQUENCY_HZ,
    MIN_LEVEL_SCALE_DB,
    MIN_LEVEL_STEP_DB,
    MIN_SAMPLE_RATE,
    MIN_SECONDS_PER_TONE,
    Band,
    LevelScale,
    Preset,
    Settings,
    nyquist_hz,
)

SETTINGS_NAME = "settings.json"
SCHEMA_VERSION = 1
DEFAULT_DEVICE_TOKEN = "default"  # noqa: S105

_ROOT_KEYS = frozenset(
    {"schema_version", "playback", "output", "levels", "bands", "presets", "active_preset"}
)
_PLAYBACK_KEYS = frozenset({"seconds_per_tone", "sample_rate", "fade_ms", "autoplay"})
_OUTPUT_KEYS = frozenset({"device", "master_gain_db"})
_LEVELS_KEYS = frozenset({"min_db", "max_db", "step_db"})
_BAND_KEYS = frozenset({"frequency_hz", "level_db"})
_PRESET_KEYS = frozenset({"name", "device", "bands"})


# -----------------------------------------------------------------------------
# Проверка значений
# -----------------------------------------------------------------------------


def preset_name_problem(name: str) -> str | None:
    """Почему имя пресета недопустимо, либо None, если оно подходит."""
    if not name.strip():
        return "Имя пресета не может быть пустым."
    if name != name.strip():
        return "Имя пресета не должно начинаться или заканчиваться пробелом."
    if len(name) > MAX_PRESET_NAME_LENGTH:
        return f"Имя пресета длиннее {MAX_PRESET_NAME_LENGTH} символов."
    if any(not char.isprintable() for char in name):
        return "Имя пресета содержит непечатаемые символы."
    if name.casefold() in ("default", "по умолчанию"):
        return "Имя «По умолчанию» зарезервировано."
    return None


def validate_level_scale(scale: LevelScale) -> None:
    """Проверить диапазон и шаг ползунков уровня."""
    values = (scale.min_db, scale.max_db, scale.step_db)
    if not all(math.isfinite(value) for value in values):
        raise SettingsFormatError("Диапазон и шаг ползунков должны быть конечными числами.")
    if not MIN_LEVEL_SCALE_DB <= scale.min_db < 0.0:
        raise SettingsFormatError(
            f"Нижняя граница ползунков должна быть от {MIN_LEVEL_SCALE_DB:g} до 0 дБ."
        )
    if not 0.0 < scale.max_db <= MAX_LEVEL_SCALE_DB:
        raise SettingsFormatError(
            f"Верхняя граница ползунков должна быть от 0 до {MAX_LEVEL_SCALE_DB:g} дБ."
        )
    if not MIN_LEVEL_STEP_DB <= scale.step_db <= MAX_LEVEL_STEP_DB:
        raise SettingsFormatError(
            f"Шаг ползунков должен быть от {MIN_LEVEL_STEP_DB:g} до {MAX_LEVEL_STEP_DB:g} дБ."
        )
    steps = scale.span_db / scale.step_db
    if abs(steps - round(steps)) > 1e-9:
        raise SettingsFormatError(
            f"Диапазон ползунков {scale.span_db:g} дБ должен делиться на шаг "
            f"{scale.step_db:g} дБ без остатка."
        )
    if not scale.on_step(scale.min_db):
        raise SettingsFormatError(
            f"Нижняя граница ползунков должна быть кратна шагу {scale.step_db:g} дБ."
        )


def validate_bands(
    bands: Sequence[Band],
    *,
    max_frequency_hz: float,
    what: str,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> None:
    """Проверить набор полос: число, частоты (по возрастанию) и уровни."""
    if not 1 <= len(bands) <= MAX_BANDS:
        raise SettingsFormatError(f"{what}: число полос должно быть от 1 до {MAX_BANDS}.")

    previous = 0.0
    for number, band in enumerate(bands, start=1):
        frequency = band.frequency_hz
        if not math.isfinite(frequency) or not MIN_FREQUENCY_HZ <= frequency < max_frequency_hz:
            raise SettingsFormatError(
                f"{what}: частота полосы {number} ({frequency:g} Гц) должна быть не ниже "
                f"{MIN_FREQUENCY_HZ:g} Гц и ниже {max_frequency_hz:g} Гц."
            )
        if number > 1 and frequency <= previous:
            raise SettingsFormatError(
                f"{what}: частоты должны идти строго по возрастанию "
                f"(полоса {number}: {frequency:g} Гц после {previous:g} Гц)."
            )
        previous = frequency

        _validate_level(band.level_db, scale, f"{what}: уровень полосы {number}")


def _validate_level(level_db: float, scale: LevelScale, what: str) -> None:
    if not math.isfinite(level_db) or not scale.min_db <= level_db <= scale.max_db:
        raise SettingsFormatError(
            f"{what} ({level_db:g} дБ) должен быть от {scale.min_db:g} до {scale.max_db:g} дБ."
        )
    if not scale.on_step(level_db):
        raise SettingsFormatError(
            f"{what} ({level_db:g} дБ) должен быть кратен {scale.step_db:g} дБ."
        )


def _validate_device(device: str | None, what: str) -> None:
    if device is not None and not device.strip():
        raise SettingsFormatError(f"{what}: название устройства не может быть пустым.")


def _validate_bool(value: object, what: str) -> None:
    if not isinstance(value, bool):
        raise SettingsFormatError(f"{what}: ожидалось true или false.")


def validate_settings(settings: Settings) -> None:
    """Проверить настройки целиком; при нарушении бросает SettingsFormatError."""
    scale = settings.level_scale
    validate_level_scale(scale)

    if not MIN_SECONDS_PER_TONE <= settings.seconds_per_tone <= MAX_SECONDS_PER_TONE:
        raise SettingsFormatError(
            f"Время на тон должно быть от {MIN_SECONDS_PER_TONE:g} до {MAX_SECONDS_PER_TONE:g} с."
        )
    if not MIN_SAMPLE_RATE <= settings.sample_rate <= MAX_SAMPLE_RATE:
        raise SettingsFormatError(
            f"Частота дискретизации должна быть от {MIN_SAMPLE_RATE} до {MAX_SAMPLE_RATE} Гц."
        )
    if not MIN_FADE_MS <= settings.fade_ms <= MAX_FADE_MS:
        raise SettingsFormatError(
            f"Длительность нарастания должна быть от {MIN_FADE_MS:g} до {MAX_FADE_MS:g} мс."
        )
    if not MASTER_GAIN_SCALE.min_db <= settings.master_gain_db <= MASTER_GAIN_SCALE.max_db:
        raise SettingsFormatError(
            f"Уровень мастера должен быть от {MASTER_GAIN_SCALE.min_db:g} до "
            f"{MASTER_GAIN_SCALE.max_db:g} дБ."
        )
    if not MASTER_GAIN_SCALE.on_step(settings.master_gain_db):
        raise SettingsFormatError(
            f"Уровень мастера должен быть кратен {MASTER_GAIN_SCALE.step_db:g} дБ."
        )
    autoplay: object = settings.autoplay
    _validate_bool(autoplay, "Автовоспроизведение")
    _validate_device(settings.device, "Устройство вывода")

    validate_bands(
        settings.bands,
        max_frequency_hz=nyquist_hz(settings.sample_rate),
        what="Полосы",
        scale=scale,
    )

    if settings.default_bands:
        validate_bands(
            settings.default_bands,
            max_frequency_hz=nyquist_hz(settings.sample_rate),
            what="Полосы по умолчанию",
            scale=scale,
        )

    names: set[str] = set()
    for preset in settings.presets:
        problem = preset_name_problem(preset.name)
        if problem is not None:
            raise SettingsFormatError(f"Пресет '{preset.name}': {problem}")
        key = preset.name.casefold()
        if key in names:
            raise SettingsFormatError(f"Пресет '{preset.name}' встречается дважды.")
        names.add(key)
        _validate_device(preset.device, f"Пресет '{preset.name}'")
        validate_bands(
            preset.bands,
            max_frequency_hz=nyquist_hz(settings.sample_rate),
            what=f"Пресет '{preset.name}'",
            scale=scale,
        )

    if settings.active_preset is not None and settings.active_preset.casefold() not in names:
        raise SettingsFormatError(
            f"Активный пресет '{settings.active_preset}' отсутствует в списке пресетов."
        )


# -----------------------------------------------------------------------------
# Разбор файла
# -----------------------------------------------------------------------------


def _as_object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SettingsFormatError(f"{what}: ожидался объект.")
    result: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            raise SettingsFormatError(f"{what}: ключи должны быть строками.")
        result[key] = item
    return result


def _exact_keys(data: dict[str, object], keys: frozenset[str], what: str) -> None:
    missing = sorted(keys - data.keys())
    if missing:
        raise SettingsFormatError(f"{what}: нет параметров {', '.join(missing)}.")
    extra = sorted(data.keys() - keys)
    if extra:
        raise SettingsFormatError(f"{what}: неизвестные параметры {', '.join(extra)}.")


def _as_number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsFormatError(f"{what}: ожидалось число.")
    number = float(value)
    if not math.isfinite(number):
        raise SettingsFormatError(f"{what}: число должно быть конечным.")
    return number


def _as_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsFormatError(f"{what}: ожидалось целое число.")
    return value


def _as_bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsFormatError(f"{what}: ожидалось true или false.")
    return value


def _as_device(value: object, what: str) -> str | None:
    """Устройство: строка названия или default для устройства по умолчанию."""
    if value == DEFAULT_DEVICE_TOKEN:
        return None
    if not isinstance(value, str):
        raise SettingsFormatError(
            f'{what}: ожидалась строка с названием устройства или "{DEFAULT_DEVICE_TOKEN}".'
        )
    return value


def _as_active_preset(value: object, what: str) -> str | None:
    """Активный пресет: имя пресета или default для отсутствующего."""
    if value == DEFAULT_DEVICE_TOKEN:
        return None
    if not isinstance(value, str) or not value:
        raise SettingsFormatError(
            f'{what}: ожидалась строка с именем пресета или "{DEFAULT_DEVICE_TOKEN}".'
        )
    return value


def _active_preset_data(active_preset: str | None) -> str:
    """Представление отсутствующего активного пресета в файле настроек."""
    return DEFAULT_DEVICE_TOKEN if active_preset is None else active_preset


def _device_data(device: str | None) -> str:
    """Представление устройства по умолчанию в файле настроек."""
    return DEFAULT_DEVICE_TOKEN if device is None else device


def _as_text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise SettingsFormatError(f"{what}: ожидалась строка.")
    return value


def _as_list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise SettingsFormatError(f"{what}: ожидался список.")
    return list(cast("list[object]", value))


def _parse_bands(value: object, what: str) -> tuple[Band, ...]:
    bands: list[Band] = []
    for number, item in enumerate(_as_list(value, what), start=1):
        label = f"{what}[{number}]"
        data = _as_object(item, label)
        _exact_keys(data, _BAND_KEYS, label)
        bands.append(
            Band(
                frequency_hz=_as_number(data["frequency_hz"], f"{label}.frequency_hz"),
                level_db=_as_number(data["level_db"], f"{label}.level_db"),
            )
        )
    return tuple(bands)


def parse_settings(raw: object) -> Settings:
    """Построить и проверить настройки по разобранному JSON."""
    root = _as_object(raw, "Файл")
    version = root.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version != SCHEMA_VERSION:
            raise SettingsVersionError(
                f"Файл настроек имеет версию схемы {version}, программа поддерживает "
                f"версию {SCHEMA_VERSION}."
            )
    _exact_keys(root, _ROOT_KEYS, "Файл")
    _ = _as_int(root["schema_version"], "schema_version")

    playback = _as_object(root["playback"], "playback")
    _exact_keys(playback, _PLAYBACK_KEYS, "playback")
    output = _as_object(root["output"], "output")
    _exact_keys(output, _OUTPUT_KEYS, "output")
    levels = _as_object(root["levels"], "levels")
    _exact_keys(levels, _LEVELS_KEYS, "levels")

    presets: list[Preset] = []
    for number, item in enumerate(_as_list(root["presets"], "presets"), start=1):
        label = f"presets[{number}]"
        data = _as_object(item, label)
        _exact_keys(data, _PRESET_KEYS, label)
        presets.append(
            Preset(
                name=_as_text(data["name"], f"{label}.name"),
                device=_as_device(data["device"], f"{label}.device"),
                bands=_parse_bands(data["bands"], f"{label}.bands"),
            )
        )

    active = _as_active_preset(root["active_preset"], "active_preset")
    root_bands = _parse_bands(root["bands"], "bands")
    default_bands = tuple(Band(b.frequency_hz, 0.0) for b in root_bands)

    active_bands = default_bands
    if active is not None:
        for p in presets:
            if p.name.casefold() == active.casefold():
                active_bands = p.bands
                break

    settings = Settings(
        seconds_per_tone=_as_number(playback["seconds_per_tone"], "playback.seconds_per_tone"),
        sample_rate=_as_int(playback["sample_rate"], "playback.sample_rate"),
        fade_ms=_as_number(playback["fade_ms"], "playback.fade_ms"),
        master_gain_db=_as_number(output["master_gain_db"], "output.master_gain_db"),
        device=_as_device(output["device"], "output.device"),
        bands=active_bands,
        presets=tuple(presets),
        active_preset=active,
        level_scale=LevelScale(
            min_db=_as_number(levels["min_db"], "levels.min_db"),
            max_db=_as_number(levels["max_db"], "levels.max_db"),
            step_db=_as_number(levels["step_db"], "levels.step_db"),
        ),
        autoplay=_as_bool(playback["autoplay"], "playback.autoplay"),
        default_bands=default_bands,
    )
    validate_settings(settings)
    return settings


def load_settings(path: Path) -> Settings:
    """Прочитать settings.json. Без файла программа работать не может."""
    if not path.is_file():
        raise SettingsNotFoundError(str(path))

    try:
        content = path.read_bytes()
    except OSError as err:
        raise SettingsReadError(f"Не удалось прочитать файл настроек: {err}") from err

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise SettingsFormatError(f"Файл настроек не в кодировке UTF-8: {err}") from err

    try:
        raw = cast("object", json.loads(text))
    except (json.JSONDecodeError, RecursionError) as err:
        raise SettingsFormatError(f"Синтаксическая ошибка JSON: {err}") from err

    return parse_settings(raw)


# -----------------------------------------------------------------------------
# Запись файла
# -----------------------------------------------------------------------------


def settings_to_data(settings: Settings) -> dict[str, object]:
    """Представление настроек для JSON; порядок ключей фиксирован."""

    def bands_data(bands: Sequence[Band]) -> list[object]:
        return [{"frequency_hz": band.frequency_hz, "level_db": 0.0} for band in bands]

    return {
        "schema_version": SCHEMA_VERSION,
        "playback": {
            "seconds_per_tone": settings.seconds_per_tone,
            "sample_rate": settings.sample_rate,
            "fade_ms": settings.fade_ms,
            "autoplay": settings.autoplay,
        },
        "output": {
            "device": _device_data(settings.device),
            "master_gain_db": settings.master_gain_db,
        },
        "levels": {
            "min_db": settings.level_scale.min_db,
            "max_db": settings.level_scale.max_db,
            "step_db": settings.level_scale.step_db,
        },
        "bands": bands_data(settings.default_bands if settings.default_bands else settings.bands),
        "presets": [
            {
                "name": preset.name,
                "device": _device_data(preset.device),
                "bands": [
                    {"frequency_hz": band.frequency_hz, "level_db": band.level_db}
                    for band in preset.bands
                ],
            }
            for preset in settings.presets
        ],
        "active_preset": _active_preset_data(settings.active_preset),
    }


def _scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _is_scalar(value: object) -> bool:
    return not isinstance(value, (dict, list))


def _format(value: object, indent: int, *, inline_objects: bool = False) -> str:
    """JSON с отступами; простые объекты внутри списков (полосы) идут в одну строку."""
    pad = " " * (indent + 2)
    end = " " * indent
    if isinstance(value, dict):
        items = cast("dict[str, object]", value)
        if not items:
            return "{}"
        if inline_objects and all(_is_scalar(item) for item in items.values()):
            pairs = (f"{_scalar(key)}: {_scalar(item)}" for key, item in items.items())
            return "{" + ", ".join(pairs) + "}"
        lines = [f"{pad}{_scalar(key)}: {_format(item, indent + 2)}" for key, item in items.items()]
        return "{\n" + ",\n".join(lines) + "\n" + end + "}"
    if isinstance(value, list):
        elements = cast("list[object]", value)
        if not elements:
            return "[]"
        lines = [pad + _format(item, indent + 2, inline_objects=True) for item in elements]
        return "[\n" + ",\n".join(lines) + "\n" + end + "]"
    return _scalar(value)


def settings_to_text(settings: Settings) -> str:
    """Текст settings.json для записи на диск."""
    return _format(settings_to_data(settings), 0) + "\n"


def save_settings(path: Path, settings: Settings) -> None:
    """Записать settings.json атомарно: временный файл рядом и замена."""
    default_bands_zero = tuple(
        Band(b.frequency_hz, 0.0)
        for b in (settings.default_bands if settings.default_bands else settings.bands)
    )
    settings_to_save = replace(settings, default_bands=default_bands_zero)

    try:
        validate_settings(settings_to_save)
    except SettingsFormatError as err:
        raise SettingsSaveError(f"Настройки не записаны: {err}") from err

    text = settings_to_text(settings_to_save)
    try:
        if parse_settings(cast("object", json.loads(text))) != settings_to_save:
            raise SettingsSaveError("Настройки не записаны: результат не совпал с исходными.")
    except SettingsFormatError as err:
        raise SettingsSaveError(f"Настройки не записаны: {err}") from err

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as handle:
            temp_name = handle.name
            _ = handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as err:
        if temp_name:
            with suppress(OSError):
                os.unlink(temp_name)
        raise SettingsSaveError(f"Не удалось записать файл настроек: {err}") from err


def quarantine_corrupted(path: Path, now: datetime | None = None) -> Path:
    """Переименовать повреждённый файл в settings.corrupted-<дата-время>.json."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.corrupted-{stamp}{path.suffix}")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.stem}.corrupted-{stamp}-{counter}{path.suffix}")
    try:
        _ = path.rename(target)
    except OSError as err:
        raise SettingsSaveError(f"Не удалось переименовать повреждённый файл: {err}") from err
    return target


def load_settings_at_startup(path: Path) -> Settings:
    """Загрузка при запуске программы."""
    try:
        return load_settings(path)
    except SettingsFormatError as err:
        try:
            target = quarantine_corrupted(path)
        except SettingsSaveError as rename_err:
            raise SettingsFormatError(f"{err}\n\n{rename_err}") from err
        raise SettingsFormatError(
            f"{err}\n\nПовреждённый файл сохранён как «{target.name}». Исправьте его и верните "
            f"имя {path.name}, чтобы программа запустилась."
        ) from err
