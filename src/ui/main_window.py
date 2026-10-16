"""Главное окно проигрывателя контрольных тонов."""

import math
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audio_engine import DEFAULT_DEVICE_LABEL, StreamFactory, TonePlayer, list_output_devices
from error import AudioError
from models import PlayerState, ToneConfig

MIN_OFFSET_DB = -60.0
MAX_OFFSET_DB = 12.0
REFRESH_INTERVAL_MS = 50

_STYLESHEET = """
QMainWindow {
    background-color: #1e2126;
}
QWidget {
    color: #e6e8ec;
    font-size: 13px;
}
QLabel#frequencyLabel {
    font-size: 58px;
    font-weight: 700;
    color: #6ec1ff;
}
QLabel#toneLabel {
    font-size: 16px;
    color: #9aa3ad;
}
QLabel#remainingLabel {
    font-size: 20px;
    font-weight: 600;
}
QPushButton {
    background-color: #31363d;
    border: 1px solid #454b54;
    border-radius: 6px;
    padding: 9px 22px;
}
QPushButton:hover:enabled {
    background-color: #3c424a;
}
QPushButton:disabled {
    color: #61686f;
    background-color: #262a30;
}
QProgressBar {
    border: 1px solid #454b54;
    border-radius: 5px;
    background-color: #262a30;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #4a90d9;
}
QSlider::groove:horizontal {
    height: 6px;
    background-color: #262a30;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #6ec1ff;
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
}
QComboBox {
    background-color: #31363d;
    border: 1px solid #454b54;
    border-radius: 6px;
    padding: 6px 10px;
}
QComboBox:disabled {
    color: #61686f;
}
QComboBox QAbstractItemView {
    background-color: #262a30;
    border: 1px solid #454b54;
    selection-background-color: #3c424a;
}
QPushButton#navButton {
    padding: 9px 0;
    font-size: 18px;
    font-weight: 600;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #454b54;
    border-radius: 4px;
    background-color: #31363d;
}
QCheckBox::indicator:checked {
    background-color: #4a90d9;
    border-color: #6ec1ff;
}
QCheckBox:disabled {
    color: #61686f;
}
"""


class VoidSignal(Protocol):
    def connect(self, slot: Callable[[], None], /) -> object: ...


class IntSignal(Protocol):
    def connect(self, slot: Callable[[int], None], /) -> object: ...


def connect_action(signal: VoidSignal, slot: Callable[[], None]) -> None:
    _ = signal.connect(slot)


def connect_index(signal: IntSignal, slot: Callable[[int], None]) -> None:
    _ = signal.connect(slot)


class MainWindow(QMainWindow):
    """Окно с управлением воспроизведением и индикацией текущей частоты."""

    def __init__(
        self,
        config: ToneConfig,
        base_directory: Path,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._base_directory = base_directory
        self._player = TonePlayer(config, stream_factory=stream_factory)
        self._finishing_handled = False

        self.setWindowTitle("TonePlayer")
        self.resize(560, 560)
        self.setStyleSheet(_STYLESHEET)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        connect_action(self._timer.timeout, self._refresh)
        self._timer.start()

        self._refresh()

    # -------------------------------------------------------------------------
    # Свойства
    # -------------------------------------------------------------------------

    @property
    def player(self) -> TonePlayer:
        """Проигрыватель, управляемый окном."""
        return self._player

    # -------------------------------------------------------------------------
    # Построение интерфейса
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        frequency_row = QHBoxLayout()
        frequency_row.setSpacing(10)

        self.previous_button = QPushButton("◀")
        self.previous_button.setObjectName("navButton")
        self.previous_button.setFixedWidth(64)
        self.previous_button.setToolTip("Предыдущая частота")
        connect_action(self.previous_button.clicked, self._on_previous_tone)
        frequency_row.addWidget(self.previous_button)

        self.frequency_label = QLabel("—")
        self.frequency_label.setObjectName("frequencyLabel")
        self.frequency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frequency_row.addWidget(self.frequency_label, 1)

        self.next_button = QPushButton("▶")
        self.next_button.setObjectName("navButton")
        self.next_button.setFixedWidth(64)
        self.next_button.setToolTip("Следующая частота")
        connect_action(self.next_button.clicked, self._on_next_tone)
        frequency_row.addWidget(self.next_button)

        layout.addLayout(frequency_row)

        self.tone_label = QLabel()
        self.tone_label.setObjectName("toneLabel")
        self.tone_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.tone_label)

        self.remaining_label = QLabel("Осталось: 0:00")
        self.remaining_label.setObjectName("remainingLabel")
        self.remaining_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.remaining_label)

        self.tone_progress = QProgressBar()
        self.tone_progress.setFormat("Текущий тон: %p%")
        layout.addWidget(self.tone_progress)

        self.total_progress = QProgressBar()
        self.total_progress.setFormat("Общее время: %p%")
        layout.addWidget(self.total_progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.start_button = QPushButton("Старт")
        connect_action(self.start_button.clicked, self._on_start)
        self.pause_button = QPushButton("Пауза")
        connect_action(self.pause_button.clicked, self._on_pause)
        self.stop_button = QPushButton("Стоп")
        connect_action(self.stop_button.clicked, self._on_stop)
        for button in (self.start_button, self.pause_button, self.stop_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.autoplay_checkbox = QCheckBox("Автовоспроизведение при переключении")
        self.autoplay_checkbox.setToolTip(
            "Включено: переключение частоты сразу её воспроизводит.\n"
            "Выключено: частота переключается без звука, запуск — кнопкой «Старт»."
        )
        self.autoplay_checkbox.setChecked(True)
        layout.addWidget(self.autoplay_checkbox)

        level_row = QHBoxLayout()
        level_row.setSpacing(8)
        level_row.addWidget(QLabel(f"{MIN_OFFSET_DB:.0f}"))
        self.level_slider = QSlider(Qt.Orientation.Horizontal)
        self.level_slider.setRange(int(MIN_OFFSET_DB), int(MAX_OFFSET_DB))
        self.level_slider.setValue(int(self._config.default_offset_db))
        connect_index(self.level_slider.valueChanged, self._on_level_changed)
        level_row.addWidget(self.level_slider, 1)
        level_row.addWidget(QLabel(f"+{MAX_OFFSET_DB:.0f}"))
        layout.addLayout(level_row)

        self.level_label = QLabel()
        self.level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.level_label)

        self.device_selector = QComboBox()
        self.device_selector.addItem(DEFAULT_DEVICE_LABEL, None)
        for index, name in list_output_devices():
            self.device_selector.addItem(f"{index}: {name}", index)
        configured = self._config.device
        if configured is not None:
            position = self.device_selector.findData(configured)
            if position >= 0:
                self.device_selector.setCurrentIndex(position)
        self._player.set_device(configured)
        connect_index(self.device_selector.currentIndexChanged, self._on_device_changed)
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Устройство вывода:"))
        device_row.addWidget(self.device_selector, 1)
        layout.addLayout(device_row)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.device_label = QLabel(f"Каталог приложения: {self._base_directory.name}")
        self.device_label.setToolTip(str(self._base_directory))
        layout.addWidget(self.device_label)

    # -------------------------------------------------------------------------
    # Обработчики
    # -------------------------------------------------------------------------

    def _on_start(self) -> None:
        # После завершения сеанса «Старт» запускает всё заново с первой
        # частоты, после ручного переключения — с выбранной.
        index = 0 if self._player.state is PlayerState.FINISHED else self._player.tone_index
        self._start_playback(index)

    def _on_previous_tone(self) -> None:
        self._navigate(-1)

    def _on_next_tone(self) -> None:
        self._navigate(1)

    def _navigate(self, offset: int) -> None:
        """Сменить выбранную частоту, воспроизводя её при включённом автозапуске."""
        index = min(max(self._player.tone_index + offset, 0), self._config.tone_count - 1)
        if index == self._player.tone_index:
            return
        if self.autoplay_checkbox.isChecked():
            self._start_playback(index)
            return
        self._player.select(index)
        self._finishing_handled = False
        self._refresh()

    def _start_playback(self, index: int) -> None:
        """Запустить воспроизведение с тона index, показав ошибку устройства."""
        try:
            self._player.start(index)
        except AudioError as error:
            _ = QMessageBox.critical(self, "Ошибка воспроизведения", str(error))
            return
        self._finishing_handled = False
        self._refresh()

    def _on_pause(self) -> None:
        if self._player.state is PlayerState.PLAYING:
            self._player.pause()
        elif self._player.state is PlayerState.PAUSED:
            self._player.resume()
        self._refresh()

    def _on_stop(self) -> None:
        self._player.stop()
        self._finishing_handled = False
        self._refresh()

    def _on_level_changed(self, value: int) -> None:
        self._player.set_offset_db(float(value))
        self._refresh()

    def _on_device_changed(self, index: int) -> None:
        device = cast("object", self.device_selector.itemData(index))
        try:
            self._player.set_device(device if isinstance(device, int) else None)
        except AudioError:
            return
        self._refresh()

    # -------------------------------------------------------------------------
    # Обновление состояния
    # -------------------------------------------------------------------------

    def _refresh(self) -> None:
        state = self._player.state
        progress = self._player.progress()

        if state is PlayerState.FINISHED and not self._finishing_handled:
            self._finishing_handled = True
            self._player.finish()
            state = self._player.state
            progress = self._player.progress()

        self.frequency_label.setText(format_frequency(self._player.current_frequency))

        self.tone_label.setText(f"Тон {progress.tone_index + 1} из {self._config.tone_count}")
        self.remaining_label.setText(
            f"Осталось: {format_duration(progress.total_remaining)}"
            f"   •   тон: {format_duration(progress.tone_remaining)}"
        )
        self.tone_progress.setValue(int(round(progress.tone_fraction * 100)))
        self.total_progress.setValue(int(round(progress.total_fraction * 100)))

        self.start_button.setEnabled(state in (PlayerState.IDLE, PlayerState.FINISHED))
        self.pause_button.setEnabled(state in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.stop_button.setEnabled(state in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.device_selector.setEnabled(state not in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.previous_button.setEnabled(progress.tone_index > 0)
        self.next_button.setEnabled(progress.tone_index < self._config.tone_count - 1)
        self.pause_button.setText("Продолжить" if state is PlayerState.PAUSED else "Пауза")

        self.status_label.setText(self._player.last_status)

        offset_db = self._player.offset_db
        gain_db = self._config.gain_at(progress.tone_index)
        self.level_label.setText(
            f"Уровень: {offset_db:+.1f} дБ   •   тон: {gain_db:+.1f} дБ   •   "
            f"итого: {gain_db + offset_db:+.1f} дБ   •   цель: {self._config.target_spl_db:.0f} дБ"
        )


def format_frequency(frequency: float) -> str:
    return f"{frequency:,.0f} Гц".replace(",", " ")


def format_duration(seconds: float) -> str:
    """Человекочитаемое время: минуты и секунды с округлением вверх."""
    total = int(math.ceil(max(seconds, 0.0)))
    return f"{total // 60}:{total % 60:02d}"
