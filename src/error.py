class ConfigError(Exception):
    """Базовая ошибка работы с конфигурацией."""


class ConfigNotFoundError(ConfigError):
    """Файл конфигурации не найден."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Конфигурационный файл '{path}' не найден.")


class ConfigValidationError(ConfigError):
    """Файл конфигурации содержит некорректные данные."""


class AudioError(Exception):
    """Не удалось начать или продолжить воспроизведение."""
