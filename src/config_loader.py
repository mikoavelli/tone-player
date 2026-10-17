"""Загрузка и проверка конфигурационного файла."""

import tomllib
from pathlib import Path
from typing import cast

from error import ConfigNotFoundError, ConfigValidationError
from models import ToneConfig

# Границы для калибровочного ослабления, дБ.
MIN_GAIN_DB = -96.0
MAX_GAIN_DB = 0.0

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
