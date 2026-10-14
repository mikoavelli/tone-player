import math
import sys
import unittest
from pathlib import Path

import numpy as np

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


def fill(player: TonePlayer, frames: int) -> AudioBlock:
    """Прогнать блок через буфер вывода и вернуть полученный звук."""
    buffer = np.zeros((frames, 1), dtype=np.float32)
    player.fill_buffer(buffer)
    return buffer[:, 0]


def peak(chunk: AudioBlock) -> float:
    """Максимальный модуль сигнала в блоке."""
    return float(max(abs(chunk.max()), abs(chunk.min())))


def edges(chunk: AudioBlock) -> float:
    """Модули первого и последнего отсчётов блока."""
    return float(max(abs(chunk[:1].max()), abs(chunk[-1:].max())))


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
        self.assertLess(float(abs(chunk[: fade // 2].max())), 0.55)
        self.assertGreater(float(abs(chunk[RATE // 2 : RATE // 2 + 100].max())), 0.9)

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


class TestTonePlayer(unittest.TestCase):
    def _player(self, config: ToneConfig | None = None) -> TonePlayer:
        return TonePlayer(config or make_config(), stream_factory=FakeStream)

    def test_idle_outputs_silence(self) -> None:
        player = self._player()

        buffer = fill(player, 128)

        self.assertEqual(peak(buffer), 0.0)
        self.assertEqual(player.state, PlayerState.IDLE)
        self.assertEqual(player.current_frequency, 0.0)

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
        self.assertEqual(player.current_frequency, 0.0)

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


if __name__ == "__main__":
    unittest.main()
