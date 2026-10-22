class SettingsError(Exception):
    """Базовая ошибка работы с настройками."""


class SettingsNotFoundError(SettingsError):
    """Файл настроек не найден."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Файл настроек '{path}' не найден.")


class SettingsReadError(SettingsError):
    """Файл настроек не удалось прочитать (права, диск, кодировка)."""


class SettingsFormatError(SettingsError):
    """Файл настроек повреждён: неверный синтаксис или недопустимые данные."""


class SettingsVersionError(SettingsError):
    """Файл настроек создан другой, неподдерживаемой версией программы."""


class SettingsSaveError(SettingsError):
    """Не удалось записать файл настроек."""


class BandError(SettingsError):
    """Недопустимая операция с набором полос эквалайзера."""


class PresetError(SettingsError):
    """Недопустимая операция с пресетом: пустое имя, нет такого пресета."""


class AudioError(Exception):
    """Не удалось начать или продолжить воспроизведение."""
