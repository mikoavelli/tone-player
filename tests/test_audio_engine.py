import math
import sys
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audio_engine import AudioBlock, ToneGenerator, TonePlayer  # noqa: E402
from error import AudioError  # noqa: E402
from models import PlayerState, ToneConfig  # noqa: E402

RATE = 48000


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
    gain_db: tuple[float, ...] = (-12.0, -12.0),
    seconds_per_tone: float = 1.0,
    fade_ms: float = 20.0,
    default_offset_db: float = 0.0,
) -> ToneConfig:
    return ToneConfig(
        frequencies=frequencies,
        gain_db=gain_db,
        seconds_per_tone=seconds_per_tone,
        target_spl_db=90.0,
        sample_rate=RATE,
        fade_ms=fade_ms,
        device=None,
        default_offset_db=default_offset_db,
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
    def test_amplitude_follows_calibration(self) -> None:
        generator = ToneGenerator(make_config())

        chunk = generator.render(24000)

        self.assertAlmostEqual(peak(chunk), math.pow(10.0, -0.6), delta=0.01)
        self.assertAlmostEqual(generator.current_gain_db, -12.0)

    def test_offset_changes_amplitude(self) -> None:
        generator = ToneGenerator(make_config())
        _ = generator.render(256)
        generator.set_offset_db(-6.0)

        chunk = generator.render(24000)

        self.assertAlmostEqual(peak(chunk), math.pow(10.0, -0.9), delta=0.01)

    def test_amplitude_is_clipped_to_full_scale(self) -> None:
        generator = ToneGenerator(make_config(gain_db=(0.0, 0.0), default_offset_db=12.0))

        chunk = generator.render(24000)

        self.assertLessEqual(peak(chunk), 1.0)

    def test_generated_frequency(self) -> None:
        generator = ToneGenerator(make_config(frequencies=(1000.0,), gain_db=(0.0,)))

        chunk = generator.render(RATE)

        self.assertEqual(count_positive_crossings(chunk), 1000)

    def test_fade_in_and_out(self) -> None:
        generator = ToneGenerator(make_config(frequencies=(1000.0,), gain_db=(0.0,)))

        chunk = generator.render(RATE)

        fade = int(0.02 * RATE)
        self.assertLess(edges(chunk), 1e-3)
        head = peak(chunk[: fade // 2])
        middle = peak(chunk[RATE // 2 : RATE // 2 + 100])
        self.assertLess(head, 0.55)
        self.assertGreater(middle, 0.9)

    def test_tone_advances_after_duration(self) -> None:
        generator = ToneGenerator(make_config(seconds_per_tone=0.1))

        _ = generator.render(int(RATE * 0.1) - 1)
        self.assertEqual(generator.index, 0)

        _ = generator.render(2)
        self.assertEqual(generator.index, 1)
        self.assertEqual(generator.config.frequency_at(generator.index), 1000.0)

    def test_finished_after_last_tone(self) -> None:
        generator = ToneGenerator(make_config(seconds_per_tone=0.05))

        chunk = generator.render(int(RATE * 0.1))

        self.assertTrue(generator.finished)
        self.assertGreater(peak(chunk), 0.0)
        self.assertEqual(generator.index, 2)

    def test_reset_returns_to_start(self) -> None:
        generator = ToneGenerator(make_config(seconds_per_tone=0.05))
        _ = generator.render(int(RATE * 0.1))

        generator.reset()

        self.assertEqual(generator.index, 0)
        self.assertEqual(generator.frames_in_tone, 0)
        self.assertFalse(generator.finished)

    def test_seek_moves_to_tone(self) -> None:
        generator = ToneGenerator(make_config())

        generator.seek(1)

        self.assertEqual(generator.index, 1)
        self.assertEqual(generator.frames_in_tone, 0)
        self.assertFalse(generator.finished)

    def test_seek_after_finish_clears_finished(self) -> None:
        generator = ToneGenerator(make_config(seconds_per_tone=0.05))
        _ = generator.render(int(RATE * 0.1))
        self.assertTrue(generator.finished)

        generator.seek(1)

        self.assertFalse(generator.finished)
        self.assertEqual(generator.index, 1)


class TestTonePlayer(unittest.TestCase):
    def _player(self, config: ToneConfig | None = None) -> TonePlayer:
        return TonePlayer(config or make_config(), stream_factory=FakeStream)

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

        player = TonePlayer(make_config(), stream_factory=factory)
        _ = player.start()

        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].started)
        self.assertEqual(streams[0].kwargs["samplerate"], RATE)
        self.assertEqual(streams[0].kwargs["channels"], 1)

    def test_start_failure_raises_audio_error(self) -> None:
        def factory(**kwargs: object) -> FakeStream:
            raise OSError("устройство занято")

        player = TonePlayer(make_config(), stream_factory=factory)

        with self.assertRaises(AudioError):
            _ = player.start()
        self.assertEqual(player.state, PlayerState.IDLE)

    def test_offset_changes_amplitude_live(self) -> None:
        player = self._player(make_config(gain_db=(-6.0, -6.0)))
        _ = player.start()
        player.set_offset_db(-6.0)

        buffer = fill(player, 24000)

        self.assertAlmostEqual(peak(buffer), math.pow(10.0, -0.6), delta=0.01)
        self.assertAlmostEqual(player.current_gain_db, -12.0)

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

        player = TonePlayer(make_config(), stream_factory=factory)
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

        player = TonePlayer(make_config(), stream_factory=factory)
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

        config = ToneConfig(
            frequencies=(500.0, 1000.0),
            gain_db=(-12.0, -12.0),
            seconds_per_tone=1.0,
            target_spl_db=90.0,
            sample_rate=RATE,
            fade_ms=20.0,
            device=3,
            default_offset_db=0.0,
        )
        player = TonePlayer(config, stream_factory=factory)
        _ = player.start()

        self.assertIn("запасной вариант", player.last_status)
        self.assertEqual(attempts[-1]["device"], None)

    def test_stereo_buffer_gets_signal_in_both_channels(self) -> None:
        player = self._player()

        def factory(**kwargs: object) -> FakeStream:
            if kwargs.get("channels") == 1:
                raise OSError("нет моно")
            return FakeStream(**kwargs)

        player = TonePlayer(make_config(), stream_factory=factory)
        _ = player.start()
        buffer = OutputBuffer(4 * 512 * 2)
        player.fill_buffer(buffer)
        stereo = buffer.samples()

        left = stereo[0::2]
        right = stereo[1::2]
        self.assertGreater(peak(left), 0.0)
        self.assertEqual(peak(left), peak(right))

    def test_device_change_blocked_during_playback(self) -> None:
        player = self._player()
        _ = player.start()

        with self.assertRaises(AudioError):
            player.set_device(1)

    def test_device_change_allowed_when_idle(self) -> None:
        player = self._player()

        player.set_device(1)

        self.assertEqual(player.device, 1)


if __name__ == "__main__":
    unittest.main()
