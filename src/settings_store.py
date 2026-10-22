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
    LEVEL_MAX_DB,
    LEVEL_MIN_DB,
    LEVEL_STEP_DB,
    MASTER_MAX_DB,
    MASTER_MIN_DB,
    MAX_BANDS,
    MAX_FADE_MS,
    MAX_PRESET_NAME_LENGTH,
    MAX_SAMPLE_RATE,
    MAX_SECONDS_PER_TONE,
    MIN_FADE_MS,
    MIN_FREQUENCY_HZ,
    MIN_SAMPLE_RATE,
    MIN_SECONDS_PER_TONE,
    Band,
    Preset,
    Settings,
    nyquist_hz,
    on_level_step,
)

SETTINGS_NAME = "settings.json"
SCHEMA_VERSION = 1

_ROOT_KEYS = frozenset(
    {"schema_version", "playback", "output", "bands", "presets", "active_preset"}
)
_PLAYBACK_KEYS = frozenset({"seconds_per_tone", "sample_rate", "fade_ms"})
_OUTPUT_KEYS = frozenset({"device", "master_level_db"})
_BAND_KEYS = frozenset({"frequency_hz", "level_db"})
_PRESET_KEYS = frozenset({"name", "device", "bands"})

# Частоты пресета не привязаны к текущей частоте дискретизации, поэтому для них
# проверяется самая широкая допустимая; Найквист проверяется при применении.
_PRESET_MAX_FREQUENCY_HZ = nyquist_hz(MAX_SAMPLE_RATE)


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
    return None


def validate_bands(bands: Sequence[Band], *, max_frequency_hz: float, what: str) -> None:
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

        level = band.level_db
        if not math.isfinite(level) or not LEVEL_MIN_DB <= level <= LEVEL_MAX_DB:
            raise SettingsFormatError(
                f"{what}: уровень полосы {number} ({level:g} дБ) должен быть "
                f"от {LEVEL_MIN_DB:g} до {LEVEL_MAX_DB:g} дБ."
            )
        if not on_level_step(level):
            raise SettingsFormatError(
                f"{what}: уровень полосы {number} ({level:g} дБ) должен быть кратен "
                f"{LEVEL_STEP_DB:g} дБ."
            )


def _validate_device(device: str | None, what: str) -> None:
    if device is not None and not device.strip():
        raise SettingsFormatError(f"{what}: название устройства не может быть пустым.")


def validate_settings(settings: Settings) -> None:
    """Проверить настройки целиком; при нарушении бросает SettingsFormatError."""
    if not MIN_SECONDS_PER_TONE <= settings.seconds_per_tone <= MAX_SECONDS_PER_TONE:
        raise SettingsFormatError(
            f"Время на тон должно быть от {MIN_SECONDS_PER_TONE:g} до "
            f"{MAX_SECONDS_PER_TONE:g} с."
        )
    if not MIN_SAMPLE_RATE <= settings.sample_rate <= MAX_SAMPLE_RATE:
        raise SettingsFormatError(
            f"Частота дискретизации должна быть от {MIN_SAMPLE_RATE} до {MAX_SAMPLE_RATE} Гц."
        )
    if not MIN_FADE_MS <= settings.fade_ms <= MAX_FADE_MS:
        raise SettingsFormatError(
            f"Длительность нарастания должна быть от {MIN_FADE_MS:g} до {MAX_FADE_MS:g} мс."
        )
    if not MASTER_MIN_DB <= settings.master_level_db <= MASTER_MAX_DB:
        raise SettingsFormatError(
            f"Общий уровень должен быть от {MASTER_MIN_DB:g} до {MASTER_MAX_DB:g} дБ."
        )
    if not on_level_step(settings.master_level_db):
        raise SettingsFormatError(f"Общий уровень должен быть кратен {LEVEL_STEP_DB:g} дБ.")
    _validate_device(settings.device, "Устройство вывода")

    validate_bands(
        settings.bands, max_frequency_hz=nyquist_hz(settings.sample_rate), what="Полосы"
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
            max_frequency_hz=_PRESET_MAX_FREQUENCY_HZ,
            what=f"Пресет '{preset.name}'",
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


def _as_optional_text(value: object, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsFormatError(f"{what}: ожидалась строка или null.")
    return value


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
    if isinstance(version, int) and not isinstance(version, bool) and version != SCHEMA_VERSION:
        raise SettingsVersionError(
            f"Файл настроек имеет версию схемы {version}, программа поддерживает "
            f"версию {SCHEMA_VERSION}."
        )
    _exact_keys(root, _ROOT_KEYS, "Файл")
    _ = _as_int(version, "schema_version")

    playback = _as_object(root["playback"], "playback")
    _exact_keys(playback, _PLAYBACK_KEYS, "playback")
    output = _as_object(root["output"], "output")
    _exact_keys(output, _OUTPUT_KEYS, "output")

    presets: list[Preset] = []
    for number, item in enumerate(_as_list(root["presets"], "presets"), start=1):
        label = f"presets[{number}]"
        data = _as_object(item, label)
        _exact_keys(data, _PRESET_KEYS, label)
        presets.append(
            Preset(
                name=_as_text(data["name"], f"{label}.name"),
                device=_as_optional_text(data["device"], f"{label}.device"),
                bands=_parse_bands(data["bands"], f"{label}.bands"),
            )
        )

    settings = Settings(
        seconds_per_tone=_as_number(playback["seconds_per_tone"], "playback.seconds_per_tone"),
        sample_rate=_as_int(playback["sample_rate"], "playback.sample_rate"),
        fade_ms=_as_number(playback["fade_ms"], "playback.fade_ms"),
        master_level_db=_as_number(output["master_level_db"], "output.master_level_db"),
        device=_as_optional_text(output["device"], "output.device"),
        bands=_parse_bands(root["bands"], "bands"),
        presets=tuple(presets),
        active_preset=_as_optional_text(root["active_preset"], "active_preset"),
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
        raw: object = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as err:
        raise SettingsFormatError(f"Синтаксическая ошибка JSON: {err}") from err

    return parse_settings(raw)


# -----------------------------------------------------------------------------
# Запись файла
# -----------------------------------------------------------------------------


def settings_to_data(settings: Settings) -> dict[str, object]:
    """Представление настроек для JSON; порядок ключей фиксирован."""

    def bands_data(bands: Sequence[Band]) -> list[object]:
        return [{"frequency_hz": band.frequency_hz, "level_db": band.level_db} for band in bands]

    return {
        "schema_version": SCHEMA_VERSION,
        "playback": {
            "seconds_per_tone": settings.seconds_per_tone,
            "sample_rate": settings.sample_rate,
            "fade_ms": settings.fade_ms,
        },
        "output": {
            "device": settings.device,
            "master_level_db": settings.master_level_db,
        },
        "bands": bands_data(settings.bands),
        "presets": [
            {"name": preset.name, "device": preset.device, "bands": bands_data(preset.bands)}
            for preset in settings.presets
        ],
        "active_preset": settings.active_preset,
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
    """Записать settings.json атомарно: временный файл рядом и замена.

    Данные проверяются до записи и сверяются после сериализации, поэтому
    некорректное состояние не попадает в файл.
    """
    try:
        validate_settings(settings)
    except SettingsFormatError as err:
        raise SettingsSaveError(f"Настройки не записаны: {err}") from err

    text = settings_to_text(settings)
    try:
        if parse_settings(json.loads(text)) != settings:
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
    """Переименовать повреждённый файл в settings.corrupted-<дата-время>.json.

    Существующие файлы не затираются: при совпадении имени добавляется номер.
    """
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
    """Загрузка при запуске программы.

    Файл без синтаксиса или с недопустимыми данными переименовывается в
    settings.corrupted-<дата-время>.json, чтобы его не затёрли и можно было
    исправить; значений по умолчанию нет, поэтому после этого запуск невозможен.
    Файл, которого нет, нельзя прочитать или который создан другой версией
    программы, остаётся на месте.
    """
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
