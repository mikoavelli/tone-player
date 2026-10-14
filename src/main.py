import os
import sys
import time
import traceback
from pathlib import Path
from types import TracebackType

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from audio_engine import TonePlayer
from config_loader import load_config
from error import AudioError, ConfigError
from models import ToneConfig
from ui.main_window import MainWindow

SELFTEST_LOG = "selftest.log"


def handle_unhandled_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    traceback_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
    traceback_text = "".join(traceback_lines)

    dialog = QMessageBox()
    dialog.setIcon(QMessageBox.Icon.Critical)
    dialog.setWindowTitle("Непредвиденная ошибка")
    dialog.setText(f"Произошла непредвиденная системная ошибка:\n{exc_value}")
    dialog.setDetailedText(traceback_text)
    _ = dialog.exec()


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def run_selftest(config: ToneConfig, base_dir: Path) -> int:
    """Коротко проверить вывод звука без запуска окна."""
    lines: list[str] = []
    player = TonePlayer(config)
    lines.append(f"Устройство вывода: {player.device_name}")
    lines.append(f"Тонов: {config.tone_count} по {config.seconds_per_tone:.1f} с")
    try:
        player.start()
        time.sleep(1.0)
        progress = player.progress()
        lines.append(f"Состояние: {player.state.value}")
        lines.append(f"Частота: {player.current_frequency:.0f} Гц")
        lines.append(f"Позиция в тоне: {progress.tone_elapsed:.2f} с")
        if player.last_status:
            lines.append(f"Замечания PortAudio: {player.last_status}")
        player.stop()
        lines.append("Проверка пройдена")
        code = 0
    except AudioError as error:
        lines.append(f"Ошибка: {error}")
        code = 1

    report = "\n".join(lines)
    (base_dir / SELFTEST_LOG).write_text(report + "\n", encoding="utf-8")
    print(report)
    return code


def main() -> int:
    base_dir = get_base_dir()
    os.chdir(base_dir)

    try:
        config = load_config(base_dir / "config.toml")
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}")
        return 1

    if "--selftest" in sys.argv:
        return run_selftest(config, base_dir)

    sys.excepthook = handle_unhandled_exception

    app = QApplication(sys.argv)
    _ = app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    window = MainWindow(config=config, base_directory=base_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
