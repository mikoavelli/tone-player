"""Форматирование чисел для интерфейса."""

import math

_MINUS = "\u2212"


def format_frequency_value(frequency: float) -> str:
    """Число частоты с пробелом как разделителем тысяч, без единицы измерения."""
    return f"{frequency:,.0f}".replace(",", " ")


def format_duration(seconds: float) -> str:
    """Человекочитаемое время: минуты и секунды с округлением вверх."""
    total = int(math.ceil(max(seconds, 0.0)))
    return f"{total // 60}:{total % 60:02d}"


def format_db(value: float, *, unit: bool = True) -> str:
    """Уровень в дБ; вместо дефиса — настоящий минус, лишние нули убраны."""
    text = f"{value:+.2f}".rstrip("0").rstrip(".").replace("-", _MINUS)
    return f"{text} дБ" if unit else text


def format_scale(value_db: float) -> str:
    """Подпись деления шкалы уровня: «−10», «0», «+7.5»."""
    if value_db == 0:
        return "0"
    return f"{value_db:+g}".replace("-", _MINUS)
