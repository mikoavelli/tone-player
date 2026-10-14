"""Модели конфигурации и состояния воспроизведения."""

from dataclasses import dataclass
from enum import Enum


class PlayerState(Enum):
    """Состояние проигрывателя."""

    IDLE = "Готов"
    PLAYING = "Воспроизведение"
    PAUSED = "Пауза"
    FINISHED = "Завершено"


@dataclass(frozen=True)
class ToneConfig:
    """Параметры сеанса воспроизведения."""

    frequencies: tuple[float, ...]
    gain_db: tuple[float, ...]
    seconds_per_tone: float
    target_spl_db: float
    sample_rate: int
    fade_ms: float
    device: int | None
    default_offset_db: float

    @property
    def tone_count(self) -> int:
        """Количество контрольных частот."""
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

    def gain_at(self, index: int) -> float:
        """Калибровочное ослабление тона по индексу, дБ."""
        return self.gain_db[index]


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
        if total <= 0.0:
            return 0.0
        return min(max(self.tone_elapsed / total, 0.0), 1.0)

    @property
    def total_fraction(self) -> float:
        """Доля пройденного времени сеанса, 0.0..1.0."""
        total = self.total_elapsed + self.total_remaining
        if total <= 0.0:
            return 0.0
        return min(max(self.total_elapsed / total, 0.0), 1.0)
