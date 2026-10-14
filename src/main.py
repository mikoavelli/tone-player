import os
import sys
import traceback
from pathlib import Path
from types import TracebackType

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from config_loader import load_config
from error import ConfigError
from ui.main_window import MainWindow


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


def main() -> int:
    base_dir = get_base_dir()
    os.chdir(base_dir)

    sys.excepthook = handle_unhandled_exception

    app = QApplication(sys.argv)
    _ = app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    try:
        config = load_config(base_dir / "config.toml")
    except ConfigError as error:
        _ = QMessageBox.critical(None, "Ошибка конфигурации", str(error))
        return 1

    window = MainWindow(config=config, base_directory=base_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
