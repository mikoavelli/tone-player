# -*- mode: python ; coding: utf-8 -*-
"""Сборка TonePlayer.

Приложению нужен только Qt Widgets, поэтому из поставки Qt убираются модули и
плагины, которые всё равно не загружаются. Это основной источник уменьшения
размера: Qt Widgets тянет за собой Qt6Pdf, Qt6Network, Qt6OpenGL, Qt6Multimedia,
Wayland, GTK и медиаплагины.

Для отладки сборки удобно временно заменить EXE(... exclude_binaries=True) и
добавить COLLECT: так появляется каталог _internal, где видно, что уехало.
"""

import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC

ROOT_DIR = Path(__name__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

IS_WINDOWS = sys.platform == "win32"
ONEDIR = os.environ.get("TONEPLAYER_ONEDIR") == "1"

# Модули Qt и Python, которые не нужны приложению.
EXCLUDED_MODULES = [
    "tkinter",
    "unittest",
    "pydoc",
    "numpy",
    "PIL",
    "PyQt6.QtNetwork",
    "PyQt6.QtOpenGL",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtXml",
    "PyQt6.QtPdf",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtBluetooth",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
]

# Qt-библиотеки, необходимые приложению.
QT_LIB_PREFIXES = ("Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6DBus", "Qt6Network")

# Qt6Core связан с ICU, который лежит рядом с Qt-библиотеками.
QT_LIB_SUBSTRINGS = ("icu",)

# Платформенные плагины, которые остаются в сборке (без префиксов lib/q).
PLATFORM_PLUGINS = {
    "windows": ("windows", "windowsv3", "minimal", "offscreen"),
    "linux": ("xcb", "minimal", "offscreen"),
}

# Каталоги плагинов Qt, которые остаются в сборке.
KEPT_PLUGIN_DIRS = ("platforms", "styles", "platforminputcontexts")

# Внешние библиотеки, которые тянутся только выкинутыми плагинами Qt.
# Х11-библиотеки нужны плагину xcb, поэтому они не трогаются.
EXCLUDED_LIB_SUBSTRINGS = (
    "libglycin",
    "libgtk-3",
    "libgdk-3",
    "libwayland-",
)


def stem_of(filename: str) -> str:
    """Имя файла без префиксов lib/q и без расширения."""
    stem = filename
    for prefix in ("lib", "q"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem.split(".", 1)[0]


def wanted_plugin(path: str) -> bool:
    """Оставить только платформенные плагины, стили и методы ввода."""
    normalized = path.replace("\\", "/")
    if "/plugins/" not in normalized:
        return True
    for directory in KEPT_PLUGIN_DIRS:
        if f"/plugins/{directory}/" in normalized:
            if directory != "platforms":
                return True
            allowed = PLATFORM_PLUGINS["windows" if IS_WINDOWS else "linux"]
            return stem_of(normalized.rsplit("/", 1)[-1]) in allowed
    return False


def wanted_binary(path: str) -> bool:
    """Отбросить лишние библиотеки Qt и зависимости ненужных плагинов."""
    normalized = path.replace("\\", "/")
    if not wanted_plugin(normalized):
        return False

    filename = normalized.rsplit("/", 1)[-1]
    if "Qt6/lib/" in normalized:
        stem = stem_of(filename)
        return stem.startswith(QT_LIB_PREFIXES) or any(
            item in stem for item in QT_LIB_SUBSTRINGS
        )
    return not any(item in filename for item in EXCLUDED_LIB_SUBSTRINGS)


a = Analysis(
    [str(SRC_DIR / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "audio_engine",
        "config_loader",
        "error",
        "models",
        "ui",
        "ui.main_window",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
)

dropped = len(a.binaries)
a.binaries = TOC([entry for entry in a.binaries if wanted_binary(str(entry[0]))])
print(f"Отброшено файлов: {dropped - len(a.binaries)} из {dropped}")

pyz = PYZ(a.pure)

EXE_OPTIONS = {
    "name": "TonePlayer",
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": not IS_WINDOWS,
    "upx": True,
    "upx_exclude": [],
    "runtime_tmpdir": None,
    "console": False,
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}

if ONEDIR:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, **EXE_OPTIONS)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=not IS_WINDOWS,
        upx=True,
        upx_exclude=[],
        name="TonePlayer",
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **EXE_OPTIONS)