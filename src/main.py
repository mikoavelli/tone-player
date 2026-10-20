import os
import sys
import traceback
from contextlib import suppress
from pathlib import Path
from types import TracebackType

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from config_loader import load_config
from error import ConfigError
from main_window import MainWindow

CONFIG_NAME = "config.toml"


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


def config_candidates() -> list[Path]:
    """Каталоги, где может лежать config.toml, в порядке приоритета.

    Порядок зависит от упаковщика: у PyInstaller один файл — это
    sys.executable, у Nuitka onefile — argv[0], при запуске из исходников —
    корень проекта. Под Nuitka __file__ указывает во временный каталог
    распаковки, поэтому он в конец списка.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    if sys.argv and sys.argv[0]:
        with suppress(OSError):
            candidates.append(Path(sys.argv[0]).resolve().parent)
    candidates.append(Path(__file__).resolve().parent.parent)
    return candidates


def get_base_dir() -> Path:
    """Каталог с конфигурацией: первый, где реально лежит config.toml."""
    candidates = config_candidates()
    for candidate in candidates:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return candidates[0]


def main() -> int:
    base_dir = get_base_dir()
    os.chdir(base_dir)

    # QApplication создаётся до чтения конфигурации: сообщение об ошибке
    # показывается через QMessageBox, а любой виджет требует готового
    # приложения.
    app = QApplication(sys.argv)
    _ = app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    sys.excepthook = handle_unhandled_exception

    try:
        config = load_config(base_dir / CONFIG_NAME)
    except ConfigError as error:
        _ = QMessageBox.critical(None, "Ошибка конфигурации", str(error))
        return 1

    window = MainWindow(config=config, config_path=base_dir / CONFIG_NAME)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
