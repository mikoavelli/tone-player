"""Настраивает окружение для тестов до любых других импортов.

Пакет `tests` при импорте подключает `src` к `sys.path`
(см. tests/__init__.py), поэтому здесь можно импортировать испытуемые модули
и реэкспортировать их для тестов.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from audio_engine import AudioBlock, ToneGenerator, TonePlayer
from calibration import CalibrationResult, apply_measurement
from config_loader import load_config, save_gain_db
from error import AudioError, ConfigNotFoundError, ConfigSaveError, ConfigValidationError
from models import PlayerState, ToneConfig

__all__ = [
    "AudioBlock",
    "AudioError",
    "CalibrationResult",
    "ConfigNotFoundError",
    "ConfigSaveError",
    "ConfigValidationError",
    "PlayerState",
    "ToneConfig",
    "ToneGenerator",
    "TonePlayer",
    "apply_measurement",
    "load_config",
    "save_gain_db",
]
