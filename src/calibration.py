"""Расчёт калибровочной поправки по показанию звукомера."""

from dataclasses import dataclass

# Границы калибровочного ослабления, дБ (те же, что проверяет загрузчик).
MIN_GAIN_DB = -96.0
MAX_GAIN_DB = 0.0

# Калибровочные значения хранятся с шагом 0,1 дБ: так блок в config.toml
# остаётся читаемым, а точность заведомо выше погрешности звукомера.
GAIN_DECIMALS = 1


@dataclass(frozen=True)
class CalibrationResult:
    """Итог пересчёта калибровки одного тона."""

    old_gain_db: float
    new_gain_db: float
    correction_db: float
    # > 0: тон уже на полной шкале, а уровня всё равно не хватает. Сигнал
    # поднять нельзя — нужно усиление колонок (на столько дБ).
    shortfall_db: float
    # True: значение упёрлось в нижнюю границу (слишком громко для диапазона).
    clamped_low: bool

    @property
    def changed(self) -> bool:
        """Изменилось ли калибровочное значение."""
        return self.new_gain_db != self.old_gain_db


def apply_measurement(
    gain_db: float,
    offset_db: float,
    measured_db: float,
    target_db: float,
) -> CalibrationResult:
    """Пересчитать gain_db_i тона по измеренному звуковому давлению.

    Измерение сделано при фактическом уровне тона «калибровка + ползунок»
    (значения выше полной шкалы ограничиваются, поэтому реально звучало не
    больше 0 дБ). Уровень надо сдвинуть на разность «цель − измерено»; ползунок
    при этом остаётся временной поправкой поверх калибровки, и общая громкость
    после применения меняется ровно на эту разность.
    """
    correction = target_db - measured_db
    actual_total = min(gain_db + offset_db, MAX_GAIN_DB)
    raw = round(actual_total + correction - offset_db, GAIN_DECIMALS)

    shortfall = max(raw - MAX_GAIN_DB, 0.0)
    clamped_low = raw < MIN_GAIN_DB
    new_gain = min(max(raw, MIN_GAIN_DB), MAX_GAIN_DB)
    return CalibrationResult(
        old_gain_db=gain_db,
        new_gain_db=new_gain,
        correction_db=correction,
        shortfall_db=round(shortfall, GAIN_DECIMALS),
        clamped_low=clamped_low,
    )
