import os
import sys
import traceback
from contextlib import suppress
from pathlib import Path
from types import TracebackType

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from error import SettingsError, SettingsNotFoundError
from main_window import MainWindow
from settings_store import SETTINGS_NAME, load_settings_at_startup


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


def settings_candidates() -> list[Path]:
    """Каталоги, где может лежать settings.json, в порядке приоритета.

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
    """Каталог с настройками: первый, где реально лежит settings.json."""
    candidates = settings_candidates()
    for candidate in candidates:
        if (candidate / SETTINGS_NAME).is_file():
            return candidate
    return candidates[0]


def main() -> int:
    base_dir = get_base_dir()
    os.chdir(base_dir)

    # QApplication создаётся до чтения настроек: сообщение об ошибке
    # показывается через QMessageBox, а любой виджет требует готового
    # приложения.
    app = QApplication(sys.argv)
    _ = app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    sys.excepthook = handle_unhandled_exception

    settings_path = base_dir / SETTINGS_NAME
    try:
        settings = load_settings_at_startup(settings_path)
    except SettingsNotFoundError as error:
        _ = QMessageBox.critical(
            None,
            "Нет файла настроек",
            f"{error}\n\nПоложите {SETTINGS_NAME} рядом с программой: без него она не запускается.",
        )
        return 1
    except SettingsError as error:
        _ = QMessageBox.critical(None, "Ошибка настроек", str(error))
        return 1

    window = MainWindow(settings=settings, settings_path=settings_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
