"""Протоколы сигналов Qt и помощники подключения.

PyQt типизирует `connect` частично, поэтому сигналы описываются узкими
протоколами: так строгая проверка типов видит ожидаемую подпись слота.
"""

from collections.abc import Callable
from typing import Protocol


class VoidSignal(Protocol):
    def connect(self, slot: Callable[[], None], /) -> object: ...


class IntSignal(Protocol):
    def connect(self, slot: Callable[[int], None], /) -> object: ...


class BoolSignal(Protocol):
    def connect(self, slot: Callable[[bool], None], /) -> object: ...


class TextSignal(Protocol):
    def connect(self, slot: Callable[[str], None], /) -> object: ...


def connect_action(signal: VoidSignal, slot: Callable[[], None]) -> None:
    _ = signal.connect(slot)


def connect_index(signal: IntSignal, slot: Callable[[int], None]) -> None:
    _ = signal.connect(slot)


def connect_toggle(signal: BoolSignal, slot: Callable[[bool], None]) -> None:
    _ = signal.connect(slot)


def connect_text(signal: TextSignal, slot: Callable[[str], None]) -> None:
    _ = signal.connect(slot)
