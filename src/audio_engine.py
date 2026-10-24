"""Потоковый генератор контрольных тонов.

Звук формируется без numpy: синус берётся из таблицы, буфер вывода
заполняется через RawOutputStream, поэтому в сборку не попадают ни numpy,
ни связанные с ним библиотеки OpenBLAS.
"""

import math
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import sounddevice as sd

from error import AudioError
from models import (
    DEFAULT_LEVEL_SCALE,
    MASTER_MAX_DB,
    MASTER_MIN_DB,
    LevelScale,
    PlaybackProgress,
    PlayerState,
    ToneConfig,
)

_CHANNELS = 1
_FALLBACK_CHANNELS = 2
DEFAULT_DEVICE_LABEL = "По умолчанию"

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

# Широта нужна: звуковая библиотека может бросить PortAudioError, OSError
# (нет PortAudio-библиотеки) или ValueError на неожиданном устройстве.
PORTAUDIO_ERRORS: tuple[type[Exception], ...] = (
    sd.PortAudioError,
    OSError,
    ValueError,
)


@dataclass(frozen=True)
class OutputDevice:
    """Устройство вывода PortAudio.

    Индекс меняется между запусками, поэтому в настройках хранится метка:
    название и звуковой интерфейс (одно устройство видно через MME, WASAPI и
    другие интерфейсы под одним названием).
    """

    index: int
    name: str
    host_api: str

    @property
    def label(self) -> str:
        """Устойчивое между запусками обозначение устройства."""
        return f"{self.name} [{self.host_api}]" if self.host_api else self.name


def list_output_devices() -> list[OutputDevice]:
    """Список устройств вывода PortAudio."""
    devices: list[OutputDevice] = []
    try:
        entries = sd.query_devices()
    except PORTAUDIO_ERRORS:
        return devices

    host_apis = _host_api_names()
    for index, entry in enumerate(entries):
        channels: object = entry.get("max_output_channels")
        if not isinstance(channels, int) or channels <= 0:
            continue
        name = entry.get("name")
        host_api: object = entry.get("hostapi")
        devices.append(
            OutputDevice(
                index=index,
                name=str(name) if name else f"Устройство {index}",
                host_api=host_apis.get(host_api, "") if isinstance(host_api, int) else "",
            )
        )
    return devices


def find_device(devices: Sequence[OutputDevice], label: str | None) -> OutputDevice | None:
    """Устройство с меткой label; None, если метка не задана или устройства нет."""
    if label is None:
        return None
    for device in devices:
        if device.label == label:
            return device
    return None


def _host_api_names() -> dict[int, str]:
    try:
        entries = sd.query_hostapis()
    except PORTAUDIO_ERRORS:
        return {}
    names: dict[int, str] = {}
    for index, entry in enumerate(entries):
        name: object = entry.get("name")
        if name:
            names[index] = str(name)
    return names


class ToneGenerator:
    """Формирование сигнала тона: частота, уровень полосы, плавные переходы.

    Уровень тона в дБ от полной шкалы — это общий уровень плюс поправка полосы.
    """

    def __init__(
        self,
        config: ToneConfig,
        levels: Sequence[float],
        master_db: float,
        *,
        autoplay_enabled: bool = True,
        level_scale: LevelScale = DEFAULT_LEVEL_SCALE,
    ) -> None:
        if len(levels) != config.tone_count:
            raise ValueError("Число уровней должно совпадать с числом частот.")
        self._config = config
        self._autoplay_enabled = autoplay_enabled
        self._master_db = _checked_master(master_db)
        self._level_scale = level_scale
        # Уровни правятся из интерфейса во время звучания, поэтому живут здесь,
        # а не в неизменяемом ToneConfig. Замена элемента списка атомарна, и
        # поток PortAudio читает значение один раз на блок.
        self._levels = [_checked_level(level, level_scale) for level in levels]
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
    def master_db(self) -> float:
        """Общий уровень, дБ от полной шкалы."""
        return self._master_db

    @property
    def tone_length_frames(self) -> int:
        """Длительность тона в кадрах."""
        return max(int(round(self._config.seconds_per_tone * self._config.sample_rate)), 1)

    @property
    def levels(self) -> tuple[float, ...]:
        """Поправки уровня всех полос, дБ."""
        return tuple(self._levels)

    @property
    def level_scale(self) -> LevelScale:
        """Диапазон и шаг поправок уровня."""
        return self._level_scale

    def level_at(self, index: int) -> float:
        """Поправка уровня полосы по индексу, дБ."""
        return self._levels[index]

    def set_level_at(self, index: int, level_db: float) -> None:
        """Изменить поправку уровня полосы, дБ; звучащий тон меняется сразу."""
        if not 0 <= index < len(self._levels):
            raise IndexError(f"Нет полосы с номером {index}.")
        self._levels[index] = _checked_level(level_db, self._level_scale)

    @property
    def current_gain_db(self) -> float:
        """Итоговый уровень текущего тона: общий уровень плюс поправка полосы, дБ."""
        return self._master_db + self.level_at(min(self.index, len(self._levels) - 1))

    @property
    def amplitude(self) -> float:
        """Амплитуда текущего тона, 0.0..1.0."""
        return min(math.pow(10.0, self.current_gain_db / 20.0), 1.0)

    def advance_allowed(self) -> bool:
        """Может ли автоматически начинаться следующая частота."""
        return self._autoplay_enabled

    def set_autoplay(self, enabled: bool) -> None:
        """Разрешить (или запретить) автоматическое воспроизведение следующей частоты."""
        self._autoplay_enabled = enabled

    def _advance_allowed(self) -> bool:
        """Может ли автоматически начинаться следующая частота."""
        return self._autoplay_enabled

    @property
    def fade_frames(self) -> int:
        """Длительность нарастания и затухания в кадрах."""
        fade = int(round(self._config.fade_seconds * self._config.sample_rate))
        return max(min(fade, self.tone_length_frames // 2), 1)

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def set_master_db(self, master_db: float) -> None:
        """Изменить общий уровень, дБ от полной шкалы."""
        self._master_db = _checked_master(master_db)

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
                if self.advance_allowed():
                    self._advance()
                else:
                    # Автовоспроизведение выключено: следующая частота не начинается.
                    self.finished = True
                continue
            count = min(frames - written, self.tone_length_frames - self.frames_in_tone)
            self._render_tone(block, written, count)
            written += count
            self.frames_in_tone += count

        if not self.finished and self.frames_in_tone >= self.tone_length_frames:
            if self._advance_allowed():
                self._advance()
            else:
                # Автовоспроизведение выключено: следующая частота не начинается.
                self.finished = True
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


def _checked_level(level_db: float, scale: LevelScale) -> float:
    if not scale.min_db <= level_db <= scale.max_db:  # NaN тоже не проходит
        raise ValueError(
            f"Поправка полосы {level_db} дБ вне диапазона [{scale.min_db}, {scale.max_db}] дБ."
        )
    return float(level_db)


def _checked_master(master_db: float) -> float:
    if not MASTER_MIN_DB <= master_db <= MASTER_MAX_DB:
        raise ValueError(
            f"Общий уровень {master_db} дБ вне диапазона [{MASTER_MIN_DB}, {MASTER_MAX_DB}] дБ."
        )
    return float(master_db)


class TonePlayer:
    """Воспроизведение последовательности тонов с заданными частотами.

    Позиция воспроизведения считается по числу выданных кадров, поэтому пауза
    не сдвигает таймер и не зависит от точности системных часов.
    """

    def __init__(
        self,
        config: ToneConfig,
        levels: Sequence[float],
        master_db: float,
        device: int | None = None,
        stream_factory: StreamFactory | None = None,
        level_scale: LevelScale = DEFAULT_LEVEL_SCALE,
    ) -> None:
        self._config = config
        self._stream_factory = sd.RawOutputStream if stream_factory is None else stream_factory
        self._level_scale = level_scale
        self._generator = ToneGenerator(config, levels, master_db, level_scale=level_scale)
        self._state = PlayerState.IDLE
        self._device: int | None = device
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
    def master_db(self) -> float:
        """Общий уровень, дБ от полной шкалы."""
        return self._generator.master_db

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
        """Итоговый уровень выбранного тона: общий уровень плюс поправка полосы, дБ."""
        return self._generator.current_gain_db

    @property
    def levels(self) -> tuple[float, ...]:
        """Поправки уровня всех полос, дБ."""
        return self._generator.levels

    def level_at(self, index: int) -> float:
        """Поправка уровня полосы по индексу, дБ."""
        return self._generator.level_at(index)

    def set_level_at(self, index: int, level_db: float) -> None:
        """Изменить поправку уровня полосы; звучащий тон меняется сразу."""
        self._generator.set_level_at(index, level_db)

    def set_master_db(self, master_db: float) -> None:
        """Изменить общий уровень, дБ от полной шкалы."""
        self._generator.set_master_db(master_db)

    def reconfigure(
        self,
        config: ToneConfig,
        levels: Sequence[float],
        *,
        level_scale: LevelScale | None = None,
    ) -> None:
        """Заменить набор частот, длительности и уровни; только когда звука нет.

        Выбранная полоса сохраняется, если она ещё есть в новом наборе, иначе
        выбирается последняя. Общий уровень и автовоспроизведение не меняются.
        """
        if self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            raise AudioError("Параметры нельзя менять во время воспроизведения.")
        if level_scale is not None:
            self._level_scale = level_scale
        generator = ToneGenerator(
            config,
            levels,
            self._generator.master_db,
            autoplay_enabled=self._generator.advance_allowed(),
            level_scale=self._level_scale,
        )
        self._close_stream()
        generator.seek(self.tone_index)
        self._config = config
        self._generator = generator
        self._state = PlayerState.IDLE

    @property
    def device_name(self) -> str:
        """Название устройства вывода."""
        return self._resolve_device_name()

    @property
    def device(self) -> int | None:
        """Индекс устройства вывода; None — устройство по умолчанию."""
        return self._device

    def set_device(self, device: int | None) -> None:
        """Выбрать устройство вывода для следующего запуска."""
        if self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            raise AudioError("Устройство вывода нельзя сменить во время воспроизведения.")
        self._device = device

    def set_autoplay(self, enabled: bool) -> None:
        """Разрешить (или запретить) автоматическое воспроизведение следующей частоты."""
        self._generator.set_autoplay(enabled)

    @property
    def last_status(self) -> str:
        """Последнее сообщение потока PortAudio, если оно было."""
        return self._last_status

    # -------------------------------------------------------------------------
    # Управление
    # -------------------------------------------------------------------------

    def start(self, index: int = 0) -> None:
        """Начать или перенастроить воспроизведение с тона index.

        Если поток вывода уже открыт (идёт воспроизведение или пауза), он не
        переоткрывается: генератор просто переводится на нужный тон. Поэтому при
        воспроизведении текущая частота обрывается и сразу звучит новая, а на
        паузе звук остаётся тихим — новая частота зазвучит после продолжения.
        """
        self._last_status = ""

        if self._state in (PlayerState.PLAYING, PlayerState.PAUSED) and self._stream is not None:
            self._generator.seek(index)
            return

        self._close_stream()
        self._generator.seek(index)

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
        выполняется кнопкой «Старт». На паузе воспроизведение не прерывается —
        меняется только выбранный тон.
        """
        if self._state is PlayerState.PAUSED:
            self._generator.seek(index)
            return
        if self._state is PlayerState.PLAYING:
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
        """Остановить воспроизведение."""
        self._close_stream()
        self._generator.reset()
        self._state = PlayerState.IDLE

    def finish(self) -> None:
        """Освободить устройство вывода, сохранив итоговое состояние сеанса."""
        self._close_stream()

    def progress(self) -> PlaybackProgress:
        """Текущее положение воспроизведения.

        Позиция всегда берётся из фактического состояния генератора: сеанс мог
        закончиться раньше конца списка (например, при выключенном
        автовоспроизведении), поэтому завершение не привязано к последнему тону.
        """
        index = self.tone_index
        tone_elapsed = self._generator.frames_in_tone / self._config.sample_rate
        if self._state is PlayerState.FINISHED:
            # Завершённый тон показывается пройденным целиком.
            tone_elapsed = self._config.seconds_per_tone
        total_elapsed = min(
            tone_elapsed + index * self._config.seconds_per_tone,
            self._config.total_seconds,
        )
        tone_remaining = max(self._config.seconds_per_tone - tone_elapsed, 0.0)
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
        """Открыть поток вывода, подбирая число каналов и устройство."""
        attempts: list[tuple[int | None, int]] = []
        if self._device is not None:
            attempts.append((self._device, _CHANNELS))
            attempts.append((self._device, _FALLBACK_CHANNELS))
        attempts.append((None, _CHANNELS))
        attempts.append((None, _FALLBACK_CHANNELS))

        for device, channels in attempts:
            try:
                stream = self._stream_factory(
                    samplerate=self._config.sample_rate,
                    channels=channels,
                    device=device,
                    dtype="float32",
                    callback=self._callback,
                )
            except PORTAUDIO_ERRORS:
                continue

            if self._device is not None and device is None:
                self._last_status = (
                    "Выбранное устройство недоступно — используется устройство по умолчанию."
                )
            return stream, channels

        raise AudioError(
            "Не удалось открыть аудиовыход. Проверьте, что устройство подключено "
            "и не занято другой программой."
        )

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except PORTAUDIO_ERRORS as err:
            self._last_status = f"Ошибка закрытия потока: {err}"

    def _resolve_device_name(self) -> str:
        try:
            info = sd.query_devices(self._device, "output")
        except PORTAUDIO_ERRORS:
            return DEFAULT_DEVICE_LABEL
        name: object = info.get("name")
        return str(name) if name else DEFAULT_DEVICE_LABEL
