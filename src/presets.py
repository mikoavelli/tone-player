"""Пресеты настроек: чистые функции над Settings, без Qt и файлов."""

from collections.abc import Collection
from dataclasses import dataclass, replace

from error import PresetError
from models import Preset, Settings, nyquist_hz
from settings_store import preset_name_problem


@dataclass(frozen=True)
class PresetApplication:
    """Результат применения пресета."""

    settings: Settings
    # Устройство из пресета, которого нет на этом компьютере; вместо него
    # выбрано устройство по умолчанию.
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


def save_preset(settings: Settings, name: str, *, overwrite: bool = False) -> Settings:
    """Сохранить текущие полосы и устройство под именем name и сделать пресет активным.

    Существующий пресет заменяется только при overwrite=True; он остаётся на
    своём месте в списке.
    """
    name = normalize_name(name)
    existing = find_preset(settings, name)
    if existing is not None and not overwrite:
        raise PresetError(f"Пресет '{existing.name}' уже существует.")

    preset = Preset(name=name, device=settings.device, bands=settings.bands)
    if existing is None:
        presets = (*settings.presets, preset)
    else:
        presets = tuple(preset if item is existing else item for item in settings.presets)
    return replace(settings, presets=presets, active_preset=name)


def delete_preset(settings: Settings, name: str) -> Settings:
    """Удалить пресет; настройки остаются как были, активный пресет сбрасывается."""
    target = _require(settings, name)
    active = settings.active_preset
    was_active = active is not None and active.casefold() == name.casefold()
    return replace(
        settings,
        presets=tuple(item for item in settings.presets if item is not target),
        active_preset=None if was_active else active,
    )


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
    """Загрузить полосы и устройство пресета и сделать его активным.

    Общий уровень не меняется: загрузка пресета не должна неожиданно изменить
    громкость. available_devices — метки устройств, найденных на этом компьютере.
    """
    preset = _require(settings, name)
    limit = nyquist_hz(settings.sample_rate)
    too_high = [band.frequency_hz for band in preset.bands if band.frequency_hz >= limit]
    if too_high:
        raise PresetError(
            f"Пресет '{preset.name}' содержит частоту {max(too_high):g} Гц, а при частоте "
            f"дискретизации {settings.sample_rate} Гц допустимы частоты ниже {limit:g} Гц."
        )

    device = preset.device
    missing: str | None = None
    if device is not None and device not in available_devices:
        missing = device
        device = None
    return PresetApplication(
        settings=replace(settings, bands=preset.bands, device=device, active_preset=preset.name),
        missing_device=missing,
    )


def is_modified(settings: Settings) -> bool:
    """Отличаются ли текущие полосы или устройство от активного пресета."""
    if settings.active_preset is None:
        return False
    preset = find_preset(settings, settings.active_preset)
    if preset is None:
        return False
    return settings.bands != preset.bands or settings.device != preset.device
