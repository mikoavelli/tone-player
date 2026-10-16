from collections.abc import Callable
from typing import Any, overload

CallbackFunction = Callable[..., None]

DEFAULT_SAMPLERATE: float

class PortAudioError(Exception): ...

class CallbackFlags:
    input_overflow: bool
    output_underflow: bool
    input_underflow: bool
    output_overflow: bool
    def __init__(self, flags: int = ...) -> None: ...

class CallbackStop(Exception): ...
class CallbackAbort(Exception): ...

class Stream:
    def __init__(
        self,
        samplerate: float = ...,
        blocksize: int | None = ...,
        device: int | str | None = ...,
        channels: int = ...,
        dtype: str = ...,
        latency: str | float = ...,
        extra_settings: dict[str, Any] | None = ...,
        callback: CallbackFunction | None = ...,
        finished_callback: CallbackFunction | None = ...,
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def abort(self) -> None: ...
    def __enter__(self) -> Stream: ...
    def __exit__(self, *args: object) -> None: ...

class RawOutputStream(Stream):
    def __init__(
        self,
        samplerate: float = ...,
        blocksize: int | None = ...,
        device: int | str | None = ...,
        channels: int = ...,
        dtype: str = ...,
        latency: str | float = ...,
        extra_settings: dict[str, Any] | None = ...,
        callback: CallbackFunction | None = ...,
        finished_callback: CallbackFunction | None = ...,
    ) -> None: ...
    def write(self, data: object) -> None: ...

class OutputStream(Stream):
    def __init__(
        self,
        samplerate: float = ...,
        blocksize: int | None = ...,
        device: int | str | None = ...,
        channels: int = ...,
        dtype: str = ...,
        latency: str | float = ...,
        extra_settings: dict[str, Any] | None = ...,
        callback: CallbackFunction | None = ...,
        finished_callback: CallbackFunction | None = ...,
    ) -> None: ...

class DeviceList(list[dict[str, object]]):
    def __getitem__(self, key: str) -> dict[str, object]: ...

@overload
def query_devices() -> DeviceList: ...
@overload
def query_devices(device: int | str | None = ..., kind: str | None = ...) -> dict[str, object]: ...
def query_outputs() -> DeviceList: ...
def query_inputs() -> DeviceList: ...
def wait(
    delay: float | None = ...,
    loop: Callable[[], Any] | None = ...,
) -> None: ...
def play(
    data: object,
    samplerate: float = ...,
    device: int | str | None = ...,
    channels: int = ...,
    blocking: bool = ...,
    **kwargs: Any,
) -> Stream: ...
def check_output_settings(
    device: int | str | None = ...,
    channels: int = ...,
    dtype: str = ...,
    samplerate: float | None = ...,
    extra_settings: dict[str, Any] | None = ...,
) -> Stream: ...
