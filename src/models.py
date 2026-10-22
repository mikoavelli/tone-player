"""Модели настроек и состояния воспроизведения."""

import math
from dataclasses import dataclass
from enum import Enum

# -----------------------------------------------------------------------------
# Допустимые значения настроек. Нигде, кроме этого файла, числа не дублируются:
# границы используют и проверка файла, и движок, и окно.
# -----------------------------------------------------------------------------

# Поправка уровня полосы эквалайзера относительно общего уровня, дБ.
LEVEL_STEP_DB = 0.5
LEVEL_MIN_DB = -10.0
LEVEL_MAX_DB = 10.0

# Общий уровень относительно полной шкалы, дБ (0 дБ = амплитуда 1.0).
MASTER_MIN_DB = -60.0
MASTER_MAX_DB = 0.0

MIN_FREQUENCY_HZ = 20.0
MAX_BANDS = 64

MIN_SECONDS_PER_TONE = 1.0
MAX_SECONDS_PER_TONE = 3600.0

MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 768000

MIN_FADE_MS = 1.0
MAX_FADE_MS = 1000.0

MAX_PRESET_NAME_LENGTH = 64

_STEP_TOLERANCE = 1e-9


def on_level_step(value_db: float) -> bool:
    """Лежит ли значение на сетке шага 0,5 дБ."""
    steps = value_db / LEVEL_STEP_DB
    return abs(steps - round(steps)) <= _STEP_TOLERANCE


def snap_to_step(value_db: float) -> float:
    """Ближайшее значение на сетке шага 0,5 дБ."""
    return round(value_db / LEVEL_STEP_DB) * LEVEL_STEP_DB


def clamp_level(value_db: float) -> float:
    """Поправка полосы в пределах диапазона, на сетке шага."""
    return min(max(snap_to_step(value_db), LEVEL_MIN_DB), LEVEL_MAX_DB)


def nyquist_hz(sample_rate: int) -> float:
    """Частота Найквиста: тон на ней и выше исказится алиасингом."""
    return sample_rate / 2.0


class PlayerState(Enum):
    """Состояние проигрывателя."""

    IDLE = "Готов"
    PLAYING = "Воспроизведение"
    PAUSED = "Пауза"
    FINISHED = "Завершено"


@dataclass(frozen=True)
class Band:
    """Полоса эквалайзера: частота тона и поправка его уровня."""

    frequency_hz: float
    level_db: float


@dataclass(frozen=True)
class Preset:
    """Именованный набор полос и устройство вывода, на котором он подобран."""

    name: str
    device: str | None
    bands: tuple[Band, ...]


@dataclass(frozen=True)
class Settings:
    """Все настройки программы; хранятся в settings.json рядом с программой."""

    seconds_per_tone: float
    sample_rate: int
    fade_ms: float
    master_level_db: float
    device: str | None
    bands: tuple[Band, ...]
    presets: tuple[Preset, ...]
    active_preset: str | None

    @property
    def frequencies(self) -> tuple[float, ...]:
        """Частоты полос по возрастанию, Гц."""
        return tuple(band.frequency_hz for band in self.bands)

    @property
    def levels(self) -> tuple[float, ...]:
        """Поправки уровня полос, дБ, в порядке частот."""
        return tuple(band.level_db for band in self.bands)


@dataclass(frozen=True)
class ToneConfig:
    """Структура сеанса воспроизведения: что и как долго играть.

    Уровни сюда не входят: они меняются во время звучания и живут в генераторе.
    """

    frequencies: tuple[float, ...]
    seconds_per_tone: float
    sample_rate: int
    fade_ms: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "ToneConfig":
        """Структура сеанса по текущим настройкам."""
        return cls(
            frequencies=settings.frequencies,
            seconds_per_tone=settings.seconds_per_tone,
            sample_rate=settings.sample_rate,
            fade_ms=settings.fade_ms,
        )

    @property
    def tone_count(self) -> int:
        """Количество частот."""
        return len(self.frequencies)

    @property
    def total_seconds(self) -> float:
        """Общая длительность сеанса, секунды."""
        return self.seconds_per_tone * self.tone_count

    @property
    def fade_seconds(self) -> float:
        """Длительность нарастания/затухания тона, секунды."""
        fade = self.fade_ms / 1000.0
        return min(fade, self.seconds_per_tone / 2.0)

    def frequency_at(self, index: int) -> float:
        """Частота тона по индексу, Гц."""
        return self.frequencies[index]


@dataclass(frozen=True)
class PlaybackProgress:
    """Текущее положение воспроизведения."""

    tone_index: int
    tone_elapsed: float
    tone_remaining: float
    total_elapsed: float
    total_remaining: float

    @property
    def tone_fraction(self) -> float:
        """Доля пройденного времени текущего тона, 0.0..1.0."""
        total = self.tone_elapsed + self.tone_remaining
        if total <= 0.0 or not math.isfinite(total):
            return 0.0
        return min(max(self.tone_elapsed / total, 0.0), 1.0)

    @property
    def total_fraction(self) -> float:
        """Доля пройденного времени сеанса, 0.0..1.0."""
        total = self.total_elapsed + self.total_remaining
        if total <= 0.0 or not math.isfinite(total):
            return 0.0
        return min(max(self.total_elapsed / total, 0.0), 1.0)
