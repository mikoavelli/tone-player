"""Логика эквалайзера без Qt: набор полос и выравнивание по измерениям."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from error import BandError, SettingsFormatError
from models import (
    DEFAULT_LEVEL_SCALE,
    MAX_BANDS,
    Band,
    LevelScale,
)
from settings_store import validate_bands

# Частоты считаются совпадающими, если различаются меньше чем на эту величину, Гц.
_SAME_FREQUENCY_HZ = 1e-6


# -----------------------------------------------------------------------------
# Набор полос
# -----------------------------------------------------------------------------


def _checked(
    bands: Sequence[Band],
    max_frequency_hz: float,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> tuple[Band, ...]:
    try:
        validate_bands(bands, max_frequency_hz=max_frequency_hz, what="Полосы", scale=scale)
    except SettingsFormatError as err:
        raise BandError(str(err)) from err
    return tuple(bands)


def generate_grid(
    start_hz: float,
    step_hz: float,
    count: int,
    *,
    max_frequency_hz: float,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> tuple[Band, ...]:
    """Равномерная сетка частот; все поправки уровня нулевые."""
    if not math.isfinite(start_hz) or not math.isfinite(step_hz):
        raise BandError("Начало и шаг сетки должны быть конечными числами.")
    if step_hz <= 0.0:
        raise BandError("Шаг сетки должен быть больше нуля.")
    if not 1 <= count <= MAX_BANDS:
        raise BandError(f"Число полос должно быть от 1 до {MAX_BANDS}.")
    bands = [Band(round(start_hz + step_hz * index, 3), 0.0) for index in range(count)]
    return _checked(bands, max_frequency_hz, scale)


def insert_band(
    bands: Sequence[Band],
    frequency_hz: float,
    *,
    max_frequency_hz: float,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> tuple[Band, ...]:
    """Добавить полосу с нулевой поправкой на своё место по частоте."""
    if any(abs(band.frequency_hz - frequency_hz) < _SAME_FREQUENCY_HZ for band in bands):
        raise BandError(f"Полоса {frequency_hz:g} Гц уже есть.")
    merged = sorted([*bands, Band(frequency_hz, 0.0)], key=lambda band: band.frequency_hz)
    return _checked(merged, max_frequency_hz, scale)


def remove_band(bands: Sequence[Band], index: int) -> tuple[Band, ...]:
    """Убрать полосу; последняя полоса остаётся всегда."""
    if not 0 <= index < len(bands):
        raise BandError(f"Нет полосы с номером {index + 1}.")
    if len(bands) == 1:
        raise BandError("Нельзя удалить последнюю полосу.")
    return tuple(band for position, band in enumerate(bands) if position != index)


def set_frequency(
    bands: Sequence[Band],
    index: int,
    frequency_hz: float,
    *,
    max_frequency_hz: float,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> tuple[Band, ...]:
    """Изменить частоту полосы, не меняя порядка полос; поправка остаётся при ней."""
    if not 0 <= index < len(bands):
        raise BandError(f"Нет полосы с номером {index + 1}.")
    changed = list(bands)
    changed[index] = Band(frequency_hz, bands[index].level_db)
    return _checked(changed, max_frequency_hz, scale)


def with_levels(bands: Sequence[Band], levels: Sequence[float]) -> tuple[Band, ...]:
    """Те же частоты с новыми поправками уровня."""
    if len(levels) != len(bands):
        raise BandError("Число уровней должно совпадать с числом полос.")
    return tuple(Band(band.frequency_hz, level) for band, level in zip(bands, levels, strict=True))


def reset_levels(bands: Sequence[Band]) -> tuple[Band, ...]:
    """Те же частоты с нулевыми поправками."""
    return tuple(Band(band.frequency_hz, 0.0) for band in bands)


# -----------------------------------------------------------------------------
# Выравнивание по измерениям
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """Показание звукомера для одной полосы.

    level_dbfs — уровень сигнала, при котором оно снято: общий уровень плюс
    поправка полосы, но не выше 0 дБ (выше полной шкалы сигнал обрезается).
    Благодаря этому показания остаются верными, даже если после измерения
    двигали ползунки.
    """

    measured_db: float
    level_dbfs: float


@dataclass(frozen=True)
class FlattenResult:
    """Итог выравнивания."""

    levels: tuple[float, ...]
    reference_index: int
    spread_db: float
    changed: tuple[int, ...]
    clamped: tuple[int, ...]


def flatten(
    levels: Sequence[float],
    readings: Mapping[int, Reading],
    master_db: float,
    scale: LevelScale = DEFAULT_LEVEL_SCALE,
) -> FlattenResult:
    """Подобрать поправки так, чтобы на всех измеренных частотах давление совпало.

    Опора — самая «тихая» полоса: чувствительность (показание минус уровень
    сигнала) у неё наименьшая, а поднять сигнал выше полной шкалы нельзя.
    Она сохраняет свой уровень, остальные опускаются до её давления.
    Поправки округляются до шага, поэтому остаётся неточность до половины шага;
    выходящие за диапазон поправки ограничиваются и попадают в `clamped`.
    Полосы без показаний не меняются.
    """
    if len(readings) < 2:
        raise BandError("Для выравнивания нужны показания минимум для двух полос.")
    for index, reading in readings.items():
        if not 0 <= index < len(levels):
            raise BandError(f"Нет полосы с номером {index + 1}.")
        if not (math.isfinite(reading.measured_db) and math.isfinite(reading.level_dbfs)):
            raise BandError(f"Показание полосы {index + 1} не является конечным числом.")

    sensitivity = {index: item.measured_db - item.level_dbfs for index, item in readings.items()}
    reference = min(sensitivity, key=lambda index: (sensitivity[index], index))
    anchor = min(master_db + levels[reference], 0.0)

    new_levels = list(levels)
    clamped: list[int] = []
    for index in sorted(readings):
        wanted_total = anchor + sensitivity[reference] - sensitivity[index]
        snapped = scale.snap(wanted_total - master_db)
        level = min(max(snapped, scale.min_db), scale.max_db)
        if level != snapped:
            clamped.append(index)
        new_levels[index] = level

    changed = tuple(index for index in sorted(readings) if new_levels[index] != levels[index])
    return FlattenResult(
        levels=tuple(new_levels),
        reference_index=reference,
        spread_db=max(sensitivity.values()) - sensitivity[reference],
        changed=changed,
        clamped=tuple(clamped),
    )
