"""Настраивает окружение для тестов до любых других импортов.

Пакет `tests` при импорте подключает `src` к `sys.path`
(см. tests/__init__.py), поэтому здесь можно импортировать испытуемые модули
и реэкспортировать их для тестов.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from audio_engine import (
    AudioBlock,
    OutputDevice,
    StreamFactory,
    ToneGenerator,
    TonePlayer,
    find_device,
    list_output_devices,
)
from error import (
    AudioError,
    BandError,
    PresetError,
    SettingsFormatError,
    SettingsNotFoundError,
    SettingsReadError,
    SettingsSaveError,
    SettingsVersionError,
)
from models import Band, LevelScale, PlayerState, Preset, Settings, ToneConfig
from settings_store import (
    SETTINGS_NAME,
    load_settings,
    load_settings_at_startup,
    parse_settings,
    quarantine_corrupted,
    save_settings,
    settings_to_data,
    settings_to_text,
    validate_settings,
)

__all__ = [
    "SETTINGS_NAME",
    "AudioBlock",
    "AudioError",
    "Band",
    "BandError",
    "LevelScale",
    "OutputDevice",
    "PlayerState",
    "Preset",
    "PresetError",
    "SettingsFormatError",
    "SettingsNotFoundError",
    "SettingsReadError",
    "SettingsSaveError",
    "SettingsVersionError",
    "Settings",
    "StreamFactory",
    "ToneConfig",
    "ToneGenerator",
    "TonePlayer",
    "find_device",
    "list_output_devices",
    "load_settings",
    "load_settings_at_startup",
    "parse_settings",
    "quarantine_corrupted",
    "save_settings",
    "settings_to_data",
    "settings_to_text",
    "validate_settings",
]
