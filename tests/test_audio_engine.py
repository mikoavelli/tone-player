import math
import unittest
from array import array
from collections.abc import Sequence
from unittest import mock

from tests.bootstrap import (
    AudioBlock,
    AudioError,
    OutputDevice,
    PlayerState,
    StreamFactory,
    ToneConfig,
    ToneGenerator,
    TonePlayer,
    find_device,
    list_output_devices,
)

RATE = 48000
MASTER = -12.0


class FakeStream:
    """Поток без реального устройства вывода."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class OutputBuffer:
    """Буфер вывода для тестов: пишет звук так же, как буфер PortAudio."""

    def __init__(self, size: int) -> None:
        self.data = bytearray(size)

    def __len__(self) -> int:
        return len(self.data)

    def __setitem__(self, key: slice, value: bytes) -> None:
        self.data[key] = value

    def samples(self, channels: int = 1) -> AudioBlock:
        block = array("f")
        block.frombytes(bytes(self.data))
        return block[::channels]


def make_config(
    frequencies: tuple[float, ...] = (500.0, 1000.0),
    seconds_per_tone: float = 1.0,
    fade_ms: float = 20.0,
) -> ToneConfig:
    return ToneConfig(
        frequencies=frequencies,
        seconds_per_tone=seconds_per_tone,
        sample_rate=RATE,
        fade_ms=fade_ms,
    )


def make_generator(
    config: ToneConfig | None = None,
    levels: Sequence[float] | None = None,
    master_db: float = MASTER,
) -> ToneGenerator:
    config = config or make_config()
    return ToneGenerator(config, levels or (0.0,) * config.tone_count, master_db)


def make_player(
    config: ToneConfig | None = None,
    levels: Sequence[float] | None = None,
    master_db: float = MASTER,
    device: int | None = None,
    stream_factory: StreamFactory | None = FakeStream,
) -> TonePlayer:
    config = config or make_config()
    return TonePlayer(
        config,
        levels or (0.0,) * config.tone_count,
        master_db,
        device=device,
        stream_factory=stream_factory,
    )


def fill(player: TonePlayer, frames: int, channels: int = 1) -> AudioBlock:
    """Прогнать блок через буфер вывода и вернуть полученный звук."""
    buffer = OutputBuffer(4 * frames * channels)
    player.fill_buffer(buffer)
    return buffer.samples(channels)


def peak(chunk: AudioBlock) -> float:
    """Максимальный модуль сигнала в блоке."""
    return max((abs(value) for value in chunk), default=0.0)


def edges(chunk: AudioBlock) -> float:
    """Модули первого и последнего отсчётов блока."""
    return max((abs(value) for value in (chunk[0], chunk[-1])), default=0.0)


def count_positive_crossings(chunk: AudioBlock) -> int:
    """Число переходов через ноль снизу вверх."""
    samples = chunk.tolist()
    return sum(1 for index in range(1, len(samples)) if samples[index - 1] <= 0.0 < samples[index])


class TestToneGenerator(unittest.TestCase):
    def test_level_is_master_plus_band_correction(self) -> None:
        generator = make_generator(levels=(6.0, -4.0))

        chunk = generator.render(24000)

        self.assertAlmostEqual(peak(chunk), math.pow(10.0, -0.3), delta=0.01)
        self.assertAlmostEqual(generator.current_gain_db, -6.0)
        generator.seek(1)
        self.assertAlmostEqual(generator.current_gain_db, -16.0)

    def test_master_change_changes_amplitude(self) -> None:
        generator = make_generator()
        _ = generator.render(256)
        generator.set_master_db(-18.0)

        chunk = generator.render(24000)

        self.assertAlmostEqual(peak(chunk), math.pow(10.0, -0.9), delta=0.01)
        self.assertAlmostEqual(generator.master_db, -18.0)

    def test_amplitude_is_clipped_to_full_scale(self) -> None:
        generator = make_generator(levels=(10.0, 10.0), master_db=0.0)

        chunk = generator.render(24000)

        self.assertLessEqual(peak(chunk), 1.0)
        self.assertAlmostEqual(peak(chunk), 1.0, delta=0.001)

    def test_generated_frequency(self) -> None:
        generator = make_generator(make_config(frequencies=(1000.0,)), master_db=0.0)

        chunk = generator.render(RATE)

        self.assertEqual(count_positive_crossings(chunk), 1000)

    def test_fade_in_and_out(self) -> None:
        generator = make_generator(make_config(frequencies=(1000.0,)), master_db=0.0)

        chunk = generator.render(RATE)

        fade = int(0.02 * RATE)
        self.assertLess(edges(chunk), 1e-3)
        head = peak(chunk[: fade // 2])
        middle = peak(chunk[RATE // 2 : RATE // 2 + 100])
        self.assertLess(head, 0.55)
        self.assertGreater(middle, 0.9)

    def test_tone_advances_after_duration(self) -> None:
        generator = make_generator(make_config(seconds_per_tone=0.1))

        _ = generator.render(int(RATE * 0.1) - 1)
        self.assertEqual(generator.index, 0)

        _ = generator.render(2)
        self.assertEqual(generator.index, 1)
        self.assertEqual(generator.config.frequency_at(generator.index), 1000.0)

    def test_finished_after_last_tone(self) -> None:
        generator = make_generator(make_config(seconds_per_tone=0.05))

        chunk = generator.render(int(RATE * 0.1))

        self.assertTrue(generator.finished)
        self.assertGreater(peak(chunk), 0.0)
        self.assertEqual(generator.index, 2)

    def test_autoplay_enabled_by_default(self) -> None:
        generator = make_generator(make_config(seconds_per_tone=0.05))

        generator.set_autoplay(True)
        _ = generator.render(int(RATE * 0.1))
        self.assertGreaterEqual(generator.index, 1)

        generator.set_autoplay(False)
        generator.seek(0)
        _ = generator.render(int(RATE * 0.05) + 10)
        self.assertTrue(generator.finished)
        self.assertEqual(generator.index, 0)

        generator.set_autoplay(True)
        generator.seek(0)
        _ = generator.render(int(RATE * 0.05))
        self.assertFalse(generator.finished)
        self.assertGreaterEqual(generator.index, 1)

    def test_set_level_changes_amplitude(self) -> None:
        generator = make_generator()
        generator.set_level_at(0, 6.0)

        chunk = generator.render(24000)

        self.assertAlmostEqual(peak(chunk), math.pow(10.0, -0.3), delta=0.01)
        self.assertAlmostEqual(generator.level_at(0), 6.0)
        self.assertAlmostEqual(generator.level_at(1), 0.0)
        self.assertEqual(generator.levels, (6.0, 0.0))

    def test_levels_are_copied(self) -> None:
        levels = [1.0, 2.0]
        generator = make_generator(levels=levels)

        levels[0] = -5.0
        generator.set_level_at(1, 3.0)

        self.assertEqual(generator.levels, (1.0, 3.0))
        self.assertEqual(levels, [-5.0, 2.0])

    def test_set_level_rejects_bad_index(self) -> None:
        generator = make_generator()

        with self.assertRaises(IndexError):
            generator.set_level_at(2, 1.0)
        with self.assertRaises(IndexError):
            generator.set_level_at(-1, 1.0)

    def test_set_level_rejects_out_of_range(self) -> None:
        generator = make_generator()

        for value in (10.5, -10.5, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                generator.set_level_at(0, value)
        self.assertEqual(generator.levels, (0.0, 0.0))

    def test_master_is_limited_to_full_scale(self) -> None:
        generator = make_generator()

        for value in (0.5, -60.5, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                generator.set_master_db(value)
        generator.set_master_db(0.0)
        generator.set_master_db(-60.0)

    def test_constructor_rejects_wrong_level_count(self) -> None:
        with self.assertRaises(ValueError):
            _ = ToneGenerator(make_config(), (0.0,), MASTER)

    def test_constructor_rejects_out_of_range_level(self) -> None:
        with self.assertRaises(ValueError):
            _ = ToneGenerator(make_config(), (0.0, 11.0), MASTER)

    def test_reset_returns_to_start(self) -> None:
        generator = make_generator(make_config(seconds_per_tone=0.05))
        _ = generator.render(int(RATE * 0.1))

        generator.reset()

        self.assertEqual(generator.index, 0)
        self.assertEqual(generator.frames_in_tone, 0)
        self.assertFalse(generator.finished)

    def test_seek_moves_to_tone(self) -> None:
        generator = make_generator(make_config())

        generator.seek(1)

        self.assertEqual(generator.index, 1)
        self.assertEqual(generator.frames_in_tone, 0)
        self.assertFalse(generator.finished)

    def test_seek_after_finish_clears_finished(self) -> None:
        generator = make_generator(make_config(seconds_per_tone=0.05))
        _ = generator.render(int(RATE * 0.1))
        self.assertTrue(generator.finished)

        generator.seek(1)

        self.assertFalse(generator.finished)
        self.assertEqual(generator.index, 1)


class TestTonePlayer(unittest.TestCase):
    def _player(self, config: ToneConfig | None = None) -> TonePlayer:
        return make_player(config)

    def test_idle_outputs_silence(self) -> None:
        player = self._player()

        buffer = fill(player, 128)

        self.assertEqual(peak(buffer), 0.0)
        self.assertEqual(player.state, PlayerState.IDLE)
        self.assertEqual(player.current_frequency, 500.0)
        self.assertEqual(player.tone_index, 0)

    def test_pause_outputs_silence(self) -> None:
        player = self._player()
        _ = player.start()
        _ = fill(player, 256)
        player.pause()

        buffer = fill(player, 256)

        self.assertEqual(peak(buffer), 0.0)
        self.assertEqual(player.state, PlayerState.PAUSED)

    def test_resume_returns_to_playing(self) -> None:
        player = self._player()
        _ = player.start()
        player.pause()

        player.resume()

        self.assertEqual(player.state, PlayerState.PLAYING)

    def test_start_passes_output_settings(self) -> None:
        streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        player = make_player(make_config(), stream_factory=factory)
        _ = player.start()

        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].started)
        self.assertEqual(streams[0].kwargs["samplerate"], RATE)
        self.assertEqual(streams[0].kwargs["channels"], 1)

    def test_start_failure_raises_audio_error(self) -> None:
        def factory(**kwargs: object) -> FakeStream:
            raise OSError("устройство занято")

        player = make_player(make_config(), stream_factory=factory)

        with self.assertRaises(AudioError):
            _ = player.start()
        self.assertEqual(player.state, PlayerState.IDLE)

    def test_master_change_applies_live(self) -> None:
        player = make_player(master_db=-6.0)
        _ = player.start()
        player.set_master_db(-12.0)

        buffer = fill(player, 24000)

        self.assertAlmostEqual(peak(buffer), math.pow(10.0, -0.6), delta=0.01)
        self.assertAlmostEqual(player.current_gain_db, -12.0)
        self.assertAlmostEqual(player.master_db, -12.0)

    def test_session_finishes_after_last_tone(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()

        _ = fill(player, int(RATE * 0.1))

        self.assertEqual(player.state, PlayerState.FINISHED)
        self.assertEqual(player.progress().total_remaining, 0.0)
        self.assertEqual(player.progress().tone_index, 1)

    def test_silence_after_finish(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()
        _ = fill(player, int(RATE * 0.1))

        buffer = fill(player, 256)

        self.assertEqual(peak(buffer), 0.0)

    def test_progress_accounts_pause(self) -> None:
        player = self._player(make_config(seconds_per_tone=1.0))
        _ = player.start()
        _ = fill(player, RATE // 2)
        player.pause()
        _ = fill(player, RATE)

        progress = player.progress()

        self.assertAlmostEqual(progress.tone_elapsed, 0.5, delta=1e-3)
        self.assertAlmostEqual(progress.total_remaining, 1.5, delta=1e-3)

    def test_stop_resets_position(self) -> None:
        player = self._player()
        _ = player.start()
        _ = fill(player, RATE)

        player.stop()

        self.assertEqual(player.state, PlayerState.IDLE)
        self.assertEqual(player.progress().total_elapsed, 0.0)
        self.assertEqual(player.current_frequency, 500.0)
        self.assertEqual(player.tone_index, 0)

    def test_start_from_index_plays_that_tone(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))

        _ = player.start(1)

        self.assertEqual(player.state, PlayerState.PLAYING)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)

        _ = fill(player, int(RATE * 0.05))

        self.assertEqual(player.state, PlayerState.FINISHED)

    def test_switch_tone_while_playing_retunes_same_stream(self) -> None:
        """Переключение тона во время игры меняет частоту, не переоткрывая поток."""
        streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        player = make_player(make_config(seconds_per_tone=1.0), stream_factory=factory)
        _ = player.start()
        _ = fill(player, 256)

        _ = player.start(1)

        self.assertEqual(len(streams), 1)
        self.assertFalse(streams[0].stopped)
        self.assertFalse(streams[0].closed)
        self.assertEqual(player.state, PlayerState.PLAYING)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)
        self.assertGreater(peak(fill(player, 4800)), 0.0)

    def test_switch_tone_while_paused_stays_silent(self) -> None:
        """На паузе переключение меняет тон, но не запускает звук."""
        streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        player = make_player(make_config(seconds_per_tone=1.0), stream_factory=factory)
        _ = player.start()
        _ = fill(player, 256)
        player.pause()

        _ = player.start(1)

        self.assertEqual(player.state, PlayerState.PAUSED)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)
        self.assertEqual(len(streams), 1)
        self.assertFalse(streams[0].stopped)
        self.assertEqual(peak(fill(player, 4800)), 0.0)

        player.resume()

        self.assertGreater(peak(fill(player, 4800)), 0.0)

    def test_select_while_paused_keeps_pause(self) -> None:
        """На паузе выбор другого тона не прерывает паузу и не даёт звука."""
        player = self._player(make_config(seconds_per_tone=1.0))
        _ = player.start()
        _ = fill(player, 256)
        player.pause()

        player.select(1)

        self.assertEqual(player.state, PlayerState.PAUSED)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(peak(fill(player, 4800)), 0.0)

    def test_select_moves_position_without_sound(self) -> None:
        player = self._player()

        player.select(1)

        self.assertEqual(player.state, PlayerState.IDLE)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)
        self.assertEqual(peak(fill(player, 256)), 0.0)

    def test_select_stops_playback(self) -> None:
        streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        player = make_player(make_config(), stream_factory=factory)
        _ = player.start()

        player.select(1)

        self.assertEqual(player.state, PlayerState.IDLE)
        self.assertTrue(streams[0].stopped)
        self.assertTrue(streams[0].closed)

    def test_select_bounds(self) -> None:
        player = self._player()

        player.select(-3)
        self.assertEqual(player.tone_index, 0)

        player.select(42)
        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)

    def test_selected_tone_keeps_progress(self) -> None:
        player = self._player(make_config(seconds_per_tone=1.0))

        player.select(1)

        progress = player.progress()
        self.assertEqual(progress.tone_index, 1)
        self.assertEqual(progress.tone_elapsed, 0.0)
        self.assertAlmostEqual(progress.total_elapsed, 1.0, delta=1e-6)
        self.assertAlmostEqual(progress.total_remaining, 1.0, delta=1e-6)

    def test_finish_keeps_final_position(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()
        _ = fill(player, int(RATE * 0.1))

        player.finish()

        self.assertEqual(player.state, PlayerState.FINISHED)
        self.assertEqual(player.progress().tone_index, 1)
        self.assertEqual(player.progress().total_remaining, 0.0)

    def test_finish_before_list_end_shows_played_tone(self) -> None:
        """Автовоспроизведение выключено: сеанс заканчивается текущим тоном.

        Прогресс не должен прыгать на последний тон и показывать весь сеанс
        пройденным, пока играет только первая частота.
        """
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()
        player.set_autoplay(False)

        _ = fill(player, int(RATE * 0.05))

        self.assertEqual(player.state, PlayerState.FINISHED)
        progress = player.progress()
        self.assertEqual(progress.tone_index, 0)
        self.assertEqual(player.current_frequency, 500.0)
        self.assertAlmostEqual(progress.tone_remaining, 0.0)
        self.assertAlmostEqual(progress.total_elapsed, 0.05, delta=1e-6)
        self.assertAlmostEqual(progress.total_remaining, 0.05, delta=1e-6)

    def test_current_frequency_follows_tone(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()

        self.assertEqual(player.current_frequency, 500.0)
        _ = fill(player, int(RATE * 0.05))
        self.assertEqual(player.current_frequency, 1000.0)

    def test_falls_back_to_stereo_on_mono_error(self) -> None:
        attempts: list[dict[str, object]] = []

        def factory(**kwargs: object) -> FakeStream:
            attempts.append(kwargs)
            if kwargs.get("channels") == 1:
                raise OSError("нет моно")
            return FakeStream(**kwargs)

        player = make_player(make_config(), stream_factory=factory)
        _ = player.start()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["channels"], 1)
        self.assertEqual(attempts[1]["channels"], 2)

    def test_falls_back_to_default_device(self) -> None:
        attempts: list[dict[str, object]] = []

        def factory(**kwargs: object) -> FakeStream:
            attempts.append(kwargs)
            if kwargs.get("device") is not None:
                raise OSError("устройство недоступно")
            return FakeStream(**kwargs)

        player = make_player(device=3, stream_factory=factory)
        _ = player.start()

        self.assertIn("запасной вариант", player.last_status)
        self.assertEqual(attempts[-1]["device"], None)

    def test_stereo_buffer_gets_signal_in_both_channels(self) -> None:
        player = self._player()

        def factory(**kwargs: object) -> FakeStream:
            if kwargs.get("channels") == 1:
                raise OSError("нет моно")
            return FakeStream(**kwargs)

        player = make_player(make_config(), stream_factory=factory)
        _ = player.start()
        buffer = OutputBuffer(4 * 512 * 2)
        player.fill_buffer(buffer)
        stereo = buffer.samples()

        left = stereo[0::2]
        right = stereo[1::2]
        self.assertGreater(peak(left), 0.0)
        self.assertEqual(peak(left), peak(right))

    def test_level_change_applies_to_playing_tone(self) -> None:
        player = self._player(make_config(seconds_per_tone=1.0))
        _ = player.start()
        _ = fill(player, 256)

        player.set_level_at(0, 6.0)
        buffer = fill(player, 24000)

        self.assertAlmostEqual(peak(buffer), math.pow(10.0, -0.3), delta=0.01)
        self.assertAlmostEqual(player.level_at(0), 6.0)
        self.assertAlmostEqual(player.current_gain_db, -6.0)
        self.assertEqual(player.levels, (6.0, 0.0))

    def test_levels_survive_stop_and_restart(self) -> None:
        player = self._player()
        player.set_level_at(1, -4.0)
        _ = player.start()

        player.stop()

        self.assertEqual(player.levels, (0.0, -4.0))

    def test_selected_band_level_is_used_when_idle(self) -> None:
        player = make_player(levels=(0.0, -4.0))

        player.select(1)

        self.assertAlmostEqual(player.current_gain_db, -16.0)

    def test_reconfigure_replaces_bands(self) -> None:
        player = make_player(make_config(seconds_per_tone=0.05))
        new_config = make_config(frequencies=(250.0, 750.0, 1250.0), seconds_per_tone=2.0)

        player.reconfigure(new_config, (1.0, 2.0, 3.0))

        self.assertEqual(player.config, new_config)
        self.assertEqual(player.levels, (1.0, 2.0, 3.0))
        self.assertEqual(player.current_frequency, 250.0)
        self.assertAlmostEqual(player.progress().total_remaining, 6.0)
        self.assertEqual(player.state, PlayerState.IDLE)

    def test_reconfigure_keeps_selected_band_when_possible(self) -> None:
        player = make_player(make_config(frequencies=(500.0, 1000.0, 1500.0)))
        player.select(1)

        player.reconfigure(make_config(frequencies=(500.0, 900.0, 1500.0, 2000.0)), (0.0,) * 4)

        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 900.0)

    def test_reconfigure_clamps_selected_band(self) -> None:
        player = make_player(make_config(frequencies=(500.0, 1000.0, 1500.0)))
        player.select(2)

        player.reconfigure(make_config(frequencies=(500.0, 1000.0)), (0.0, 0.0))

        self.assertEqual(player.tone_index, 1)
        self.assertEqual(player.current_frequency, 1000.0)

    def test_reconfigure_keeps_master_and_autoplay(self) -> None:
        player = make_player(master_db=-20.0)
        player.set_autoplay(False)

        player.reconfigure(make_config(seconds_per_tone=0.05), (0.0, 0.0))
        _ = player.start()
        _ = fill(player, int(RATE * 0.05))

        self.assertAlmostEqual(player.master_db, -20.0)
        self.assertEqual(player.state, PlayerState.FINISHED)
        self.assertEqual(player.progress().tone_index, 0)

    def test_reconfigure_after_finish_returns_to_idle(self) -> None:
        player = self._player(make_config(seconds_per_tone=0.05))
        _ = player.start()
        _ = fill(player, int(RATE * 0.1))
        self.assertEqual(player.state, PlayerState.FINISHED)

        player.reconfigure(make_config(), (0.0, 0.0))

        self.assertEqual(player.state, PlayerState.IDLE)

    def test_reconfigure_blocked_during_playback_and_pause(self) -> None:
        player = self._player()
        _ = player.start()

        with self.assertRaises(AudioError):
            player.reconfigure(make_config(), (0.0, 0.0))
        player.pause()
        with self.assertRaises(AudioError):
            player.reconfigure(make_config(), (0.0, 0.0))
        self.assertEqual(player.levels, (0.0, 0.0))

    def test_reconfigure_rejects_wrong_level_count(self) -> None:
        player = self._player()

        with self.assertRaises(ValueError):
            player.reconfigure(make_config(), (0.0,))
        self.assertEqual(player.levels, (0.0, 0.0))

    def test_reconfigured_player_opens_stream_with_new_rate(self) -> None:
        streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        player = make_player(stream_factory=factory)
        config = ToneConfig(
            frequencies=(500.0,), seconds_per_tone=1.0, sample_rate=96000, fade_ms=20.0
        )

        player.reconfigure(config, (0.0,))
        _ = player.start()

        self.assertEqual(streams[0].kwargs["samplerate"], 96000)

    def test_device_change_blocked_during_playback(self) -> None:
        player = self._player()
        _ = player.start()

        with self.assertRaises(AudioError):
            player.set_device(1)

    def test_device_change_allowed_when_idle(self) -> None:
        player = self._player()

        player.set_device(1)

        self.assertEqual(player.device, 1)


class TestDevices(unittest.TestCase):
    ENTRIES = [
        {"name": "Microphone", "max_output_channels": 0, "hostapi": 0},
        {"name": "Speakers", "max_output_channels": 2, "hostapi": 0},
        {"name": "Speakers", "max_output_channels": 2, "hostapi": 1},
        {"name": "", "max_output_channels": 2, "hostapi": 7},
    ]
    HOST_APIS = ({"name": "MME"}, {"name": "Windows WASAPI"})

    def _devices(self) -> list[OutputDevice]:
        with (
            mock.patch("audio_engine.sd.query_devices", create=True, return_value=self.ENTRIES),
            mock.patch("audio_engine.sd.query_hostapis", create=True, return_value=self.HOST_APIS),
        ):
            return list_output_devices()

    def test_only_output_devices_are_listed(self) -> None:
        devices = self._devices()

        self.assertEqual([device.index for device in devices], [1, 2, 3])

    def test_label_includes_host_api(self) -> None:
        devices = self._devices()

        self.assertEqual(devices[0].label, "Speakers [MME]")
        self.assertEqual(devices[1].label, "Speakers [Windows WASAPI]")

    def test_same_name_on_different_host_apis_is_distinguishable(self) -> None:
        labels = [device.label for device in self._devices()]

        self.assertEqual(len(set(labels)), len(labels))

    def test_unknown_host_api_and_blank_name(self) -> None:
        device = self._devices()[2]

        self.assertEqual(device.name, "Устройство 3")
        self.assertEqual(device.label, "Устройство 3")

    def test_find_device_by_label(self) -> None:
        devices = self._devices()

        found = find_device(devices, "Speakers [Windows WASAPI]")

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.index, 2)

    def test_find_device_missing_or_default(self) -> None:
        devices = self._devices()

        self.assertIsNone(find_device(devices, "Нет такого [MME]"))
        self.assertIsNone(find_device(devices, None))

    def test_query_failure_gives_empty_list(self) -> None:
        with mock.patch("audio_engine.sd.query_devices", create=True, side_effect=OSError("нет")):
            self.assertEqual(list_output_devices(), [])

    def test_host_api_failure_keeps_devices_without_interface(self) -> None:
        with (
            mock.patch("audio_engine.sd.query_devices", create=True, return_value=self.ENTRIES),
            mock.patch("audio_engine.sd.query_hostapis", create=True, side_effect=OSError("нет")),
        ):
            devices = list_output_devices()

        self.assertEqual(devices[0].label, "Speakers")


if __name__ == "__main__":
    unittest.main()
