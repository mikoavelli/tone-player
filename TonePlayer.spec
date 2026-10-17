# -*- mode: python ; coding: utf-8 -*-
"""Сборка TonePlayer.

Приложению нужен только Qt Widgets, поэтому из поставки Qt убираются модули и
плагины, которые всё равно не загружаются: Qt6Pdf, Qt6OpenGL, Qt6Multimedia,
Qt6Qml и остальные неиспользуемые модули, а из плагинов остаются платформенные,
стили и методы ввода. Это основной источник уменьшения размера.

Платформенные плагины зависят от системы: на Windows это windows, windowsv3,
minimal и offscreen, на Linux — xcb, wayland, minimal и offscreen. Wayland
оставлен потому, что в Wayland-сессии Qt выбирает его по умолчанию (и часто
через переменную QT_QPA_PLATFORM), а без плагина приложение не запускается.
К wayland-плагину прилагаются shell-интеграция xdg-shell и оформление окна:
без них Qt не может создать окно.

Для отладки сборки удобно временно заменить EXE(... exclude_binaries=True) и
добавить COLLECT: так появляется каталог _internal, где видно, что уехало.
"""

import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC

# SPECPATH — каталог spec-файла, его задаёт сам PyInstaller; сборка не зависит
# от текущего каталога запуска.
ROOT_DIR = Path(SPECPATH).resolve()
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
# Qt6XcbQpa и Qt6WaylandClient — не отдельные модули, а части плагинов
# платформы: без первой не грузится libqxcb.so (он подхватывает системный Qt
# другой версии), без второй — libqwayland.so.
QT_LIB_PREFIXES = (
    "Qt6Core",
    "Qt6Gui",
    "Qt6Widgets",
    "Qt6DBus",
    "Qt6Network",
    "Qt6XcbQpa",
    "Qt6WaylandClient",
)

# Qt6Core связан с ICU, который лежит рядом с Qt-библиотеками.
QT_LIB_SUBSTRINGS = ("icu",)

# Платформенные плагины, которые остаются в сборке (без префиксов lib/q).
PLATFORM_PLUGINS = {
    "windows": ("windows", "windowsv3", "minimal", "offscreen"),
    "linux": ("xcb", "wayland", "minimal", "offscreen"),
}

# Каталоги плагинов Qt, которые остаются в сборке целиком.
KEPT_PLUGIN_DIRS = ("styles", "platforminputcontexts")

# Плагины Wayland: нужны только на Linux и не целиком.
# xdg-shell — оболочка обычных рабочих столов, bradient и adwaita — отрисовка
# заголовка окна (в niri и других композиторах без серверного оформления окно
# иначе остаётся без рамки), qt-plugin-wayland-egl — вывод через EGL.
WAYLAND_PLUGIN_FILES = {
    "wayland-shell-integration": ("xdg-shell",),
    "wayland-decoration-client": ("bradient", "adwaita"),
}

# Внешние библиотеки, которые тянутся только выкинутыми плагинами Qt.
# X11-библиотеки нужны плагину xcb, libwayland-* — плагину wayland, поэтому
# они не трогаются.
EXCLUDED_LIB_SUBSTRINGS = (
    "libglycin",
    "libgtk-3",
    "libgdk-3",
)


def stem_of(filename: str) -> str:
    """Имя файла без префиксов lib/q и без расширения."""
    stem = filename
    for prefix in ("lib", "q"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem.split(".", 1)[0]


def wanted_plugin(path: str) -> bool:
    """Оставить только нужные плагины: платформенные, Wayland, стили и ввод."""
    normalized = path.replace("\\", "/")
    if "/plugins/" not in normalized:
        return True
    directory, _, filename = normalized.split("/plugins/", 1)[1].partition("/")
    if not filename:
        return True
    if directory == "platforms":
        allowed = PLATFORM_PLUGINS["windows" if IS_WINDOWS else "linux"]
        return stem_of(filename) in allowed
    if directory in KEPT_PLUGIN_DIRS:
        return True
    if IS_WINDOWS:
        return False
    return any(item in filename for item in WAYLAND_PLUGIN_FILES.get(directory, ()))


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
    # UPX отключён: сжатие Qt DLL ломает их загрузку и повышает число ложных
    # срабатываний антивирусов, а на windows-latest UPX всё равно не установлен.
    "upx": False,
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
        upx=False,
        upx_exclude=[],
        name="TonePlayer",
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **EXE_OPTIONS)
