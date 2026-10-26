"""Пресеты настроек: чистые функции над Settings, без Qt и файлов."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from error import PresetError
from models import Band, Preset, Settings
from settings_store import preset_name_problem


@dataclass(frozen=True)
class PresetApplication:
    """Результат применения пресета."""

    settings: Settings
    missing_device: str | None


def normalize_name(name: str) -> str:
    """Имя без лишних пробелов по краям; бросает PresetError, если оно недопустимо."""
    stripped = name.strip()
    problem = preset_name_problem(stripped)
    if problem is not None:
        raise PresetError(problem)
    return stripped


def find_preset(settings: Settings, name: str) -> Preset | None:
    """Пресет с таким именем; регистр не важен."""
    key = name.casefold()
    for preset in settings.presets:
        if preset.name.casefold() == key:
            return preset
    return None


def _require(settings: Settings, name: str) -> Preset:
    preset = find_preset(settings, name)
    if preset is None:
        raise PresetError(f"Пресет '{name}' не найден.")
    return preset


def save_preset(
    settings: Settings,
    name: str,
    *,
    bands: Sequence[Band] | None = None,
    overwrite: bool = False,
) -> Settings:
    """Сохранить полосы (частоты и уровни) под именем name и сделать пресет активным."""
    name = normalize_name(name)
    existing = find_preset(settings, name)
    if existing is not None and not overwrite:
        raise PresetError(f"Пресет '{existing.name}' уже существует.")

    preset_bands = tuple(bands) if bands is not None else settings.bands
    preset = Preset(name=name, device=settings.device, bands=preset_bands)
    if existing is None:
        presets = (*settings.presets, preset)
    else:
        presets = tuple(preset if item is existing else item for item in settings.presets)
    return replace(settings, presets=presets, bands=preset_bands, active_preset=name)


def delete_preset(settings: Settings, name: str) -> Settings:
    """Удалить пресет; активный пресет переходит на дефолтный."""
    target = _require(settings, name)
    active = settings.active_preset
    was_active = active is not None and active.casefold() == name.casefold()
    new_presets = tuple(item for item in settings.presets if item is not target)
    new_active = None if was_active else active
    new_bands = (
        settings.default_bands if (was_active and settings.default_bands) else settings.bands
    )
    return replace(settings, presets=new_presets, active_preset=new_active, bands=new_bands)


def rename_preset(settings: Settings, name: str, new_name: str) -> Settings:
    """Переименовать пресет; имя не должно совпадать с другим пресетом."""
    target = _require(settings, name)
    new_name = normalize_name(new_name)
    other = find_preset(settings, new_name)
    if other is not None and other is not target:
        raise PresetError(f"Пресет '{other.name}' уже существует.")

    renamed = replace(target, name=new_name)
    active = settings.active_preset
    is_active = active is not None and active.casefold() == target.name.casefold()
    return replace(
        settings,
        presets=tuple(renamed if item is target else item for item in settings.presets),
        active_preset=new_name if is_active else active,
    )


def apply_preset(
    settings: Settings, name: str, available_devices: Collection[str]
) -> PresetApplication:
    """Применить пресет: заменяет полосы (частоты и уровни) и выбирает его устройство."""
    preset = _require(settings, name)
    device = preset.device
    missing: str | None = None
    if device is not None and device not in available_devices:
        missing = device
        device = None
    return PresetApplication(
        settings=replace(settings, bands=preset.bands, device=device, active_preset=preset.name),
        missing_device=missing,
    )


def is_modified(settings: Settings, current_bands: Sequence[Band] | None = None) -> bool:
    """Отличаются ли текущие полосы или устройство от активного пресета."""
    if settings.active_preset is None:
        return False
    preset = find_preset(settings, settings.active_preset)
    if preset is None:
        return False
    bands = tuple(current_bands) if current_bands is not None else settings.bands
    return bands != preset.bands or settings.device != preset.device
