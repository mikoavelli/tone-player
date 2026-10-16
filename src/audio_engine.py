"""Потоковый генератор контрольных тонов.

Звук формируется без numpy: синус берётся из таблицы, буфер вывода
заполняется через RawOutputStream, поэтому в сборку не попадают ни numpy,
ни связанные с ним библиотеки OpenBLAS.
"""

import math
from array import array
from collections.abc import Callable
from typing import Protocol, cast

import sounddevice as sd

from error import AudioError
from models import PlaybackProgress, PlayerState, ToneConfig

_CHANNELS = 1
_FALLBACK_CHANNELS = 2
DEFAULT_DEVICE_LABEL = "Устройство вывода по умолчанию"

# Размер таблицы синуса — степень двойки, переход фазы выполняется по маске.
TABLE_SIZE = 8192
TABLE_MASK = TABLE_SIZE - 1
_SINE_TABLE = array("f", (math.sin(2.0 * math.pi * i / TABLE_SIZE) for i in range(TABLE_SIZE)))
_PI = math.pi
_FRAME_SIZE = 4  # float32


# Блок звука: (кадры,), float32.
AudioBlock = array[float]


class MutableBuffer(Protocol):
    """Буфер вывода, в который можно записать байты (cffi-буфер, bytearray)."""

    def __len__(self) -> int: ...

    def __setitem__(self, key: slice, value: bytes) -> None: ...


class AudioStream(Protocol):
    """Минимальный интерфейс аудиопотока."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


StreamFactory = Callable[..., AudioStream]


def list_output_devices() -> list[tuple[int, str]]:
    """Список устройств вывода PortAudio: индекс и название."""
    devices: list[tuple[int, str]] = []
    try:
        entries = sd.query_devices()
    except Exception:  # noqa: BLE001 - список устройств только для выбора в окне
        return devices

    for index, entry in enumerate(entries):
        channels: object = entry.get("max_output_channels")
        if not isinstance(channels, int) or channels <= 0:
            continue
        name = entry.get("name")
        devices.append((index, str(name) if name else f"Устройство {index}"))
    return devices


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

    @property
    def fade_frames(self) -> int:
        """Длительность нарастания и затухания в кадрах."""
        fade = int(round(self._config.fade_seconds * self._config.sample_rate))
        return max(min(fade, self.tone_length_frames // 2), 1)

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def set_offset_db(self, offset_db: float) -> None:
        """Изменить уровень сигнала относительно калибровки, дБ."""
        self._offset_db = offset_db

    def reset(self) -> None:
        """Вернуться к началу последовательности."""
        self.seek(0)

    def seek(self, index: int) -> None:
        """Перейти к тону по индексу, не воспроизводя его.

        Индекс ограничивается списком частот: значения вне диапазона дают
        первый или последний тон.
        """
        self.index = min(max(index, 0), self._config.tone_count - 1)
        self.frames_in_tone = 0
        self.finished = False
        self._phase = 0.0

    def render(self, frames: int) -> AudioBlock:
        """Сформировать кадры сигнала и сдвинуть позицию воспроизведения."""
        block = array("f", bytes(_FRAME_SIZE * frames))
        written = 0
        while written < frames and not self.finished:
            if self.frames_in_tone >= self.tone_length_frames:
                self._advance()
                continue
            count = min(frames - written, self.tone_length_frames - self.frames_in_tone)
            self._render_tone(block, written, count)
            written += count
            self.frames_in_tone += count

        if not self.finished and self.frames_in_tone >= self.tone_length_frames:
            self._advance()
        return block

    # -------------------------------------------------------------------------
    # Внутреннее
    # -------------------------------------------------------------------------

    def _render_tone(self, block: AudioBlock, start: int, count: int) -> None:
        """Записать count кадров текущего тона начиная с позиции start."""
        rate = self._config.sample_rate
        step = TABLE_SIZE * self._config.frequency_at(self.index) / rate
        amplitude = self.amplitude
        fade = self.fade_frames
        total = self.tone_length_frames
        first = self.frames_in_tone
        phase = self._phase
        table = _SINE_TABLE

        for offset in range(count):
            position = first + offset
            if position < fade:
                gain = amplitude * 0.5 * (1.0 - math.cos(_PI * position / fade))
            elif position > total - fade:
                gain = amplitude * 0.5 * (1.0 - math.cos(_PI * (total - position) / fade))
            else:
                gain = amplitude
            block[start + offset] = table[int(phase) & TABLE_MASK] * gain
            phase += step

        self._phase = phase % TABLE_SIZE

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

    def __init__(
        self,
        config: ToneConfig,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        self._config = config
        self._stream_factory = sd.RawOutputStream if stream_factory is None else stream_factory
        self._generator = ToneGenerator(config)
        self._state = PlayerState.IDLE
        self._device: int | str | None = config.device
        self._channels = _CHANNELS
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
        """Индекс выбранного тона: текущий при воспроизведении, иначе выбранный вручную."""
        return min(self._generator.index, self._config.tone_count - 1)

    @property
    def current_frequency(self) -> float:
        """Частота выбранного тона, Гц."""
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
    def device(self) -> int | str | None:
        """Устройство вывода: индекс или часть названия; None — по умолчанию."""
        return self._device

    def set_device(self, device: int | str | None) -> None:
        """Выбрать устройство вывода для следующего запуска."""
        if self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            raise AudioError("Устройство вывода нельзя сменить во время воспроизведения.")
        self._device = device

    @property
    def last_status(self) -> str:
        """Последнее сообщение потока PortAudio, если оно было."""
        return self._last_status

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def start(self, index: int = 0) -> None:
        """Начать воспроизведение с тона index (по умолчанию с первого)."""
        self._close_stream()
        self._generator.seek(index)
        self._last_status = ""

        stream, channels = self._open_stream()
        self._channels = channels

        self._stream = stream
        self._state = PlayerState.PLAYING
        try:
            stream.start()
        except Exception as err:
            self._state = PlayerState.IDLE
            self._close_stream()
            raise AudioError(f"Не удалось запустить воспроизведение: {err}") from err

    def select(self, index: int) -> None:
        """Перейти к тону по индексу без воспроизведения.

        Если звук шёл, он прекращается: тон остаётся выбранным, а запуск
        выполняется кнопкой «Старт».
        """
        if self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            self._close_stream()
        self._generator.seek(index)
        self._state = PlayerState.IDLE

    def pause(self) -> None:
        """Приостановить воспроизведение."""
        if self._state is PlayerState.PLAYING:
            self._state = PlayerState.PAUSED

    def resume(self) -> None:
        """Продолжить воспроизведение после паузы."""
        if self._state is PlayerState.PAUSED:
            self._state = PlayerState.PLAYING

    def stop(self) -> None:
        """Остановить воспроизождение."""
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
        outdata: object,
        frames: int,
        _time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Обратный вызов PortAudio: заполнить очередной блок звука."""
        if status:
            self._last_status = str(status)
        self.fill_buffer(cast("MutableBuffer", outdata), frames)

    def fill_buffer(self, buffer: MutableBuffer, frames: int = -1) -> None:
        """Заполнить буфер вывода согласно текущему состоянию.

        Вызывается из потока PortAudio; в тестах метод используется напрямую.
        """
        channels = self._channels
        capacity = len(buffer) // (_FRAME_SIZE * channels)
        count = capacity if frames < 0 else min(frames, capacity)

        if self._state is not PlayerState.PLAYING:
            buffer[: _FRAME_SIZE * count * channels] = bytes(_FRAME_SIZE * count * channels)
            return

        block = self._generator.render(count)
        if channels == 1:
            payload = block.tobytes()
        else:
            spread = array("f", bytes(_FRAME_SIZE * count * channels))
            for channel in range(channels):
                spread[channel::channels] = block
            payload = spread.tobytes()

        buffer[: len(payload)] = payload
        if self._generator.finished:
            self._state = PlayerState.FINISHED

    def _open_stream(self) -> tuple[AudioStream, int]:
        """Открыть поток вывода, подбирая число каналов и устройство по умолчанию."""
        attempts: list[tuple[int | str | None, int]] = [
            (self._device, _CHANNELS),
            (self._device, _FALLBACK_CHANNELS),
        ]
        if self._device is not None:
            attempts.append((None, _CHANNELS))
            attempts.append((None, _FALLBACK_CHANNELS))

        errors: list[str] = []
        for index, (device, channels) in enumerate(attempts):
            try:
                stream = self._stream_factory(
                    samplerate=self._config.sample_rate,
                    channels=channels,
                    device=device,
                    dtype="float32",
                    callback=self._callback,
                )
            except Exception as err:  # noqa: BLE001 - перебираем варианты открытия
                errors.append(f"устройство {device}, каналов {channels}: {err}")
                continue

            if index > 0:
                self._last_status = "Открыт запасной вариант вывода — " + errors[-1]
            return stream, channels

        raise AudioError("Не удалось открыть устройство вывода: " + "; ".join(errors))

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
            info = sd.query_devices(self._device, "output")
        except Exception:  # noqa: BLE001 - имя устройства только для информации
            return DEFAULT_DEVICE_LABEL
        name: object = info.get("name")
        return str(name) if name else DEFAULT_DEVICE_LABEL
