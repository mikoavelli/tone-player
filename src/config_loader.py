"""Загрузка и проверка конфигурационного файла."""

import math
import os
import re
import tempfile
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import cast

from calibration import MAX_GAIN_DB, MIN_GAIN_DB
from error import ConfigNotFoundError, ConfigSaveError, ConfigValidationError
from models import ToneConfig

# Допустимый диапазон частоты дискретизации, Гц.
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 768000


def load_config(config_path: Path) -> ToneConfig:
    """Прочитать конфигурацию из TOML-файла."""
    if not config_path.exists():
        raise ConfigNotFoundError(str(config_path))

    try:
        with config_path.open("rb") as config_file:
            raw_data: object = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as err:
        raise ConfigValidationError(
            f"Синтаксическая ошибка в конфигурационном файле: {err}"
        ) from err
    except OSError as err:
        raise ConfigValidationError(f"Не удалось прочитать конфигурационный файл: {err}") from err

    raw_dict = _require_dict(raw_data, "root")

    playback = _require_dict(raw_dict.get("playback"), "playback")
    calibration = _require_dict(raw_dict.get("calibration"), "calibration")
    output = _require_dict(raw_dict.get("output"), "output")

    number_of_constants = _require_int(
        playback.get("number_of_constants"), "playback.number_of_constants"
    )
    if number_of_constants <= 0:
        raise ConfigValidationError("Параметр 'number_of_constants' должен быть больше нуля.")

    frequencies = _require_float_list(playback.get("f_i"), "playback.f_i")
    gain_db = _require_float_list(calibration.get("gain_db_i"), "calibration.gain_db_i")

    if len(frequencies) != number_of_constants:
        raise ConfigValidationError(
            f"Список 'f_i' должен содержать ровно {number_of_constants} значений."
        )
    if len(gain_db) != number_of_constants:
        raise ConfigValidationError(
            f"Список 'gain_db_i' должен содержать ровно {number_of_constants} значений."
        )

    for index, frequency in enumerate(frequencies):
        if frequency <= 0.0:
            raise ConfigValidationError(
                f"Частота f_i[{index}] должна быть больше нуля, получено {frequency}."
            )

    for index, gain in enumerate(gain_db):
        if not MIN_GAIN_DB <= gain <= MAX_GAIN_DB:
            raise ConfigValidationError(
                f"Значение gain_db_i[{index}]={gain} дБ должно быть в диапазоне "
                f"[{MIN_GAIN_DB}, {MAX_GAIN_DB}] дБ."
            )

    seconds_per_tone = _require_float(playback.get("seconds_per_tone"), "playback.seconds_per_tone")
    if seconds_per_tone <= 0.0:
        raise ConfigValidationError("Параметр 'seconds_per_tone' должен быть больше нуля.")

    target_spl_db = _require_float(playback.get("target_spl_db"), "playback.target_spl_db")

    sample_rate = _require_int(output.get("sample_rate"), "output.sample_rate")
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise ConfigValidationError(
            f"Параметр 'sample_rate' должен быть в диапазоне "
            f"[{MIN_SAMPLE_RATE}, {MAX_SAMPLE_RATE}] Гц."
        )

    # Частота выше половины частоты дискретизации отображается в более низкую
    # (алиасинг), то есть прозвучит не та частота, что указана в конфигурации.
    nyquist = sample_rate / 2.0
    for index, frequency in enumerate(frequencies):
        if frequency >= nyquist:
            raise ConfigValidationError(
                f"Частота f_i[{index}]={frequency:g} Гц должна быть ниже половины частоты "
                f"дискретизации ({nyquist:g} Гц); увеличьте 'sample_rate'."
            )

    fade_ms = _require_float(output.get("fade_ms"), "output.fade_ms")
    if fade_ms <= 0.0:
        raise ConfigValidationError("Параметр 'fade_ms' должен быть больше нуля.")

    device = _optional_device(output.get("device"), "output.device")
    default_offset_db = _require_float(output.get("default_offset_db"), "output.default_offset_db")

    return ToneConfig(
        frequencies=frequencies,
        gain_db=gain_db,
        seconds_per_tone=seconds_per_tone,
        target_spl_db=target_spl_db,
        sample_rate=sample_rate,
        fade_ms=fade_ms,
        device=device,
        default_offset_db=default_offset_db,
    )


def _require_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigValidationError(f"Секция '{name}' должна быть таблицей.")
    raw_items = cast("dict[object, object]", value)
    result: dict[str, object] = {}
    for key, item in raw_items.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _require_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"Параметр '{name}' должен быть числом.")
    return float(value)


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"Параметр '{name}' должен быть целым числом.")
    return value


def _optional_device(value: object, name: str) -> int | str | None:
    """Устройство вывода: индекс, часть названия или устройство по умолчанию."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigValidationError(f"Параметр '{name}' должен быть числом или строкой.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        return None if text.lower() in ("", "default") else text
    raise ConfigValidationError(f"Параметр '{name}' должен быть числом или строкой.")


def _require_float_list(value: object, name: str) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigValidationError(f"Параметр '{name}' должен быть непустым списком чисел.")
    items = cast("list[object]", value)
    return tuple(_require_float(item, f"{name}[{index}]") for index, item in enumerate(items))


# -----------------------------------------------------------------------------
# Сохранение калибровки
# -----------------------------------------------------------------------------

_SECTION_HEADER = re.compile(
    r"^[ \t]*\[(?P<name>[^\[\]\r\n]+)\][ \t]*(?:#.*)?\r?$", re.MULTILINE
)
_GAIN_BLOCK = re.compile(r"^(?P<head>[ \t]*gain_db_i[ \t]*=[ \t]*)\[[^\]]*\]", re.MULTILINE)

# Значений в строке блока gain_db_i и отступ строк: как в поставляемом config.toml.
_GAINS_PER_ROW = 5
_ROW_INDENT = "    "


def _format_gain_block(gains: tuple[float, ...], newline: str) -> str:
    """Список gain_db_i в стиле поставляемого config.toml."""
    values = [repr(float(gain)) for gain in gains]
    if len(values) <= _GAINS_PER_ROW:
        return "[" + ", ".join(values) + "]"
    rows = [
        _ROW_INDENT + ", ".join(values[start : start + _GAINS_PER_ROW])
        for start in range(0, len(values), _GAINS_PER_ROW)
    ]
    return "[" + newline + ("," + newline).join(rows) + newline + "]"


def _replace_gain_block(text: str, gains: tuple[float, ...]) -> str:
    """Заменить в тексте конфигурации только значение gain_db_i в секции [calibration]."""
    section_start = -1
    section_end = len(text)
    for header in _SECTION_HEADER.finditer(text):
        if section_start >= 0:
            section_end = header.start()
            break
        if header.group("name").strip() == "calibration":
            section_start = header.end()
    if section_start < 0:
        raise ConfigSaveError("В конфигурационном файле нет секции [calibration].")

    block = _GAIN_BLOCK.search(text, section_start, section_end)
    if block is None:
        raise ConfigSaveError("В секции [calibration] не найден список gain_db_i.")

    newline = "\r\n" if "\r\n" in text else "\n"
    replacement = block.group("head") + _format_gain_block(gains, newline)
    return text[: block.start()] + replacement + text[block.end() :]


def save_gain_db(config_path: Path, gain_db: tuple[float, ...]) -> None:
    """Записать калибровку в config.toml, не трогая остальной текст и комментарии.

    Заменяется только список gain_db_i в секции [calibration]. Результат
    перед записью разбирается заново и сверяется с переданными значениями,
    файл записывается атомарно (временный файл рядом и замена).
    """
    for index, gain in enumerate(gain_db):
        if not math.isfinite(gain) or not MIN_GAIN_DB <= gain <= MAX_GAIN_DB:
            raise ConfigSaveError(
                f"Значение gain_db_i[{index}]={gain} дБ вне диапазона "
                f"[{MIN_GAIN_DB}, {MAX_GAIN_DB}] дБ, файл не изменён."
            )

    try:
        original = config_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigSaveError(f"Не удалось прочитать конфигурационный файл: {err}") from err

    updated = _replace_gain_block(original, gain_db)

    try:
        parsed: object = tomllib.loads(updated)
        root = _require_dict(parsed, "root")
        calibration = _require_dict(root.get("calibration"), "calibration")
        saved = _require_float_list(calibration.get("gain_db_i"), "calibration.gain_db_i")
    except (tomllib.TOMLDecodeError, ConfigValidationError) as err:
        raise ConfigSaveError(
            f"Калибровка не записана: результат не прошёл проверку ({err})."
        ) from err
    if saved != tuple(float(gain) for gain in gain_db):
        raise ConfigSaveError(
            "Калибровка не записана: результат не совпал с введёнными значениями."
        )

    if updated == original:
        return

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=config_path.parent, prefix=config_path.name + ".", suffix=".tmp", delete=False
        ) as temp_file:
            temp_name = temp_file.name
            _ = temp_file.write(updated.encode("utf-8"))
        os.replace(temp_name, config_path)
    except OSError as err:
        if temp_name:
            with suppress(OSError):
                os.unlink(temp_name)
        raise ConfigSaveError(f"Не удалось записать конфигурационный файл: {err}") from err
