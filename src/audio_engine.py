"""Потоковый генератор контрольных тонов."""

import math
from collections.abc import Callable
from typing import Protocol

import numpy as np
import sounddevice as sd

from error import AudioError
from models import PlaybackProgress, PlayerState, ToneConfig

_CHANNELS = 1

# Буфер вывода PortAudio: (кадры, каналы), float32.
AudioBuffer = np.ndarray[tuple[int, int], np.dtype[np.float32]]
# Блок сигнала: (кадры,), float64.
ToneBlock = np.ndarray[tuple[int], np.dtype[np.float64]]
# Готовый блок звука: (кадры,), float32.
AudioBlock = np.ndarray[tuple[int], np.dtype[np.float32]]


class AudioStream(Protocol):
    """Минимальный интерфейс аудиопотока."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


StreamFactory = Callable[..., AudioStream]


class ToneGenerator:
    """Формирование сигнала тона: частота, калибровка уровня, плавные переходы."""

    def __init__(self, config: ToneConfig) -> None:
        self._config = config
        self._offset_db = config.default_offset_db
        self.index = 0
        self.frames_in_tone = 0
        self.finished = False
        self._phase = 0.0

    # -------------------------------------------------------------------------
    # Свойства
    # -------------------------------------------------------------------------

    @property
    def config(self) -> ToneConfig:
        """Конфигурация воспроизведения."""
        return self._config

    @property
    def offset_db(self) -> float:
        """Положение ползунка уровня, дБ."""
        return self._offset_db

    @property
    def tone_length_frames(self) -> int:
        """Длительность тона в кадрах."""
        return max(int(round(self._config.seconds_per_tone * self._config.sample_rate)), 1)

    @property
    def current_gain_db(self) -> float:
        """Калибровочное ослабление текущего тона, дБ."""
        return self._config.gain_at(self.index) + self._offset_db

    @property
    def amplitude(self) -> float:
        """Амплитуда текущего тона, 0.0..1.0."""
        return min(math.pow(10.0, self.current_gain_db / 20.0), 1.0)

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def set_offset_db(self, offset_db: float) -> None:
        """Изменить уровень сигнала относительно калибровки, дБ."""
        self._offset_db = offset_db

    def reset(self) -> None:
        """Вернуться к началу последовательности."""
        self.index = 0
        self.frames_in_tone = 0
        self.finished = False
        self._phase = 0.0

    def render(self, frames: int) -> AudioBlock:
        """Сформировать кадры сигнала и сдвинуть позицию воспроизведения."""
        buffer = np.zeros(frames, dtype=np.float32)
        written = 0
        while written < frames and not self.finished:
            if self.frames_in_tone >= self.tone_length_frames:
                self._advance()
                continue
            count = min(frames - written, self.tone_length_frames - self.frames_in_tone)
            buffer[written : written + count] = self._render_tone(count)
            written += count
            self.frames_in_tone += count
        if not self.finished and self.frames_in_tone >= self.tone_length_frames:
            self._advance()
        return buffer

    # -------------------------------------------------------------------------
    # Внутреннее
    # -------------------------------------------------------------------------

    def _render_tone(self, count: int) -> ToneBlock:
        rate = float(self._config.sample_rate)
        frequency = self._config.frequency_at(self.index)
        offset = np.arange(count, dtype=np.float64)
        phase_step = 2.0 * np.pi * frequency / rate
        time = (offset + self.frames_in_tone) / rate
        samples = np.sin(self._phase + phase_step * offset)
        self._phase = float((self._phase + phase_step * count) % (2.0 * np.pi))
        return samples * self._envelope(time) * self.amplitude

    def _envelope(self, time: ToneBlock) -> ToneBlock:
        """Плавное нарастание и затухание тона на его границах."""
        fade = self._config.fade_seconds
        total = self._config.seconds_per_tone
        rise = np.clip(time / fade, 0.0, 1.0)
        fall = np.clip((total - time) / fade, 0.0, 1.0)
        return 0.5 * (1.0 - np.cos(np.pi * rise)) * 0.5 * (1.0 - np.cos(np.pi * fall))

    def _advance(self) -> None:
        self.index += 1
        self.frames_in_tone = 0
        self._phase = 0.0
        if self.index >= self._config.tone_count:
            self.finished = True


class TonePlayer:
    """Воспроизведение последовательности тонов с заданными частотами.

    Позиция воспроизведения считается по числу выданных кадров, поэтому пауза
    не сдвигает таймер и не зависит от точности системных часов.
    """

    def __init__(self, config: ToneConfig, stream_factory: StreamFactory = sd.OutputStream) -> None:
        self._config = config
        self._stream_factory = stream_factory
        self._generator = ToneGenerator(config)
        self._state = PlayerState.IDLE
        self._stream: AudioStream | None = None
        self._last_status = ""

    # -------------------------------------------------------------------------
    # Свойства
    # -------------------------------------------------------------------------

    @property
    def config(self) -> ToneConfig:
        """Конфигурация воспроизведения."""
        return self._config

    @property
    def generator(self) -> ToneGenerator:
        """Генератор сигнала."""
        return self._generator

    @property
    def state(self) -> PlayerState:
        """Текущее состояние проигрывателя."""
        return self._state

    @property
    def offset_db(self) -> float:
        """Положение ползунка уровня, дБ."""
        return self._generator.offset_db

    @property
    def tone_index(self) -> int:
        """Индекс текущего тона."""
        return min(self._generator.index, self._config.tone_count - 1)

    @property
    def current_frequency(self) -> float:
        """Частота текущего тона, Гц."""
        if self._state is PlayerState.IDLE:
            return 0.0
        return self._config.frequency_at(self.tone_index)

    @property
    def current_gain_db(self) -> float:
        """Итоговое ослабление текущего тона с учётом ползунка, дБ."""
        return self._generator.current_gain_db

    @property
    def device_name(self) -> str:
        """Название устройства вывода."""
        return self._resolve_device_name()

    @property
    def last_status(self) -> str:
        """Последнее сообщение потока PortAudio, если оно было."""
        return self._last_status

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Начать воспроизведение с первой частоты."""
        self._close_stream()
        self._generator.reset()
        self._last_status = ""

        try:
            stream = self._stream_factory(
                samplerate=self._config.sample_rate,
                channels=_CHANNELS,
                device=self._config.device,
                dtype="float32",
                callback=self._callback,
            )
        except Exception as err:
            raise AudioError(f"Не удалось открыть устройство вывода: {err}") from err

        self._stream = stream
        self._state = PlayerState.PLAYING
        try:
            stream.start()
        except Exception as err:
            self._state = PlayerState.IDLE
            self._close_stream()
            raise AudioError(f"Не удалось запустить воспроизведение: {err}") from err

    def pause(self) -> None:
        """Приостановить воспроизведение."""
        if self._state is PlayerState.PLAYING:
            self._state = PlayerState.PAUSED

    def resume(self) -> None:
        """Продолжить воспроизведение после паузы."""
        if self._state is PlayerState.PAUSED:
            self._state = PlayerState.PLAYING

    def stop(self) -> None:
        """Остановить воспроизведение."""
        self._close_stream()
        self._generator.reset()
        self._state = PlayerState.IDLE

    def finish(self) -> None:
        """Освободить устройство вывода, сохранив итоговое состояние сеанса."""
        self._close_stream()

    def set_offset_db(self, offset_db: float) -> None:
        """Изменить уровень сигнала относительно калибровки, дБ."""
        self._generator.set_offset_db(offset_db)

    def progress(self) -> PlaybackProgress:
        """Текущее положение воспроизведения."""
        if self._state is PlayerState.FINISHED:
            index = self._config.tone_count - 1
            tone_elapsed = self._config.seconds_per_tone
        else:
            index = self.tone_index
            tone_elapsed = self._generator.frames_in_tone / self._config.sample_rate
        tone_remaining = max(self._config.seconds_per_tone - tone_elapsed, 0.0)
        total_elapsed = min(
            tone_elapsed + index * self._config.seconds_per_tone,
            self._config.total_seconds,
        )
        total_remaining = max(self._config.total_seconds - total_elapsed, 0.0)
        return PlaybackProgress(
            tone_index=index,
            tone_elapsed=tone_elapsed,
            tone_remaining=tone_remaining,
            total_elapsed=total_elapsed,
            total_remaining=total_remaining,
        )

    # -------------------------------------------------------------------------
    # Внутреннее
    # -------------------------------------------------------------------------

    def _callback(
        self,
        outdata: AudioBuffer,
        frames: int,
        _time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Обратный вызов PortAudio: заполнить очередной блок звука."""
        if status:
            self._last_status = str(status)
        self.fill_buffer(outdata, frames)

    def fill_buffer(self, outdata: AudioBuffer, frames: int = -1) -> None:
        """Заполнить буфер вывода согласно текущему состоянию.

        Вызывается из потока PortAudio; в тестах метод используется напрямую.
        """
        count = outdata.shape[0] if frames < 0 else min(frames, outdata.shape[0])
        if self._state is not PlayerState.PLAYING:
            outdata[:count] = 0.0
            return

        outdata[:count, 0] = self._generator.render(count)
        if self._generator.finished:
            self._state = PlayerState.FINISHED

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as err:  # noqa: BLE001 - закрытие потока не должно ронять приложение
            self._last_status = f"Ошибка закрытия потока: {err}"

    def _resolve_device_name(self) -> str:
        try:
            info = sd.query_devices(self._config.device, "output")
        except Exception:  # noqa: BLE001 - имя устройства только для информации
            return "Устройство вывода"
        name: object = info.get("name")
        return str(name) if name else "Устройство вывода"
