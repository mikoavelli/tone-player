"""Главное окно: эквалайзер, транспорт и настройки."""

import math
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from string import Template
from typing import cast

from PyQt6.QtCore import (
    QFileSystemWatcher,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from audio_engine import (
    DEFAULT_DEVICE_LABEL,
    OutputDevice,
    StreamFactory,
    TonePlayer,
    find_device,
    list_output_devices,
)
from band_widgets import (
    COLUMN_FREQUENCY_HEIGHT,
    COLUMN_MARGIN,
    COLUMN_PLAY_SIZE,
    COLUMN_SPACING,
    COLUMN_VALUE_HEIGHT,
    RULER_SPACING,
    BandStrip,
    LevelRuler,
    LevelSlider,
    MasterFrame,
    wheel_scroll_delta,
)
from error import AudioError, PresetError, SettingsError
from models import (
    MASTER_BASE_DB,
    MASTER_GAIN_SCALE,
    Band,
    PlayerState,
    Settings,
    ToneConfig,
)
from presets import apply_preset, delete_preset, find_preset, save_preset
from settings_dialog import SettingsDialog
from settings_store import load_settings, save_settings
from theme import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_BORDER_HOVER,
    COLOR_DISABLED,
    COLOR_ICON,
    COLOR_ICON_DISABLED,
    COLOR_MUTED,
    COLOR_ON_ACCENT,
    COLOR_SURFACE,
    COLOR_SURFACE_HOVER,
    COLOR_SWITCH_OFF,
    COLOR_TEXT,
    COLOR_TRACK,
)
from ui_signals import connect_action, connect_index, connect_text, connect_toggle
from ui_text import format_db, format_duration, format_frequency_value

REFRESH_INTERVAL_MS = 50
# Изменения настроек записываются не на каждый шаг ползунка, а после паузы.
SAVE_DELAY_MS = 600
# Служебные сообщения (например, «пресет сохранён») не должны висеть постоянно.
NOTICE_TIMEOUT_MS = 3500
# Внешние правки settings.json подхватываются после короткой паузы.
SETTINGS_RELOAD_DELAY_MS = 250

# -----------------------------------------------------------------------------
# Оформление
# -----------------------------------------------------------------------------

# Шрифты перечислены по предпочтению: берётся первый установленный в системе,
# иначе — системный шрифт по умолчанию (или моноширинный).
_SANS_FAMILIES = ("IBM Plex Sans", "Noto Sans", "Segoe UI", "Ubuntu", "DejaVu Sans")
_MONO_FAMILIES = (
    "IBM Plex Mono",
    "JetBrains Mono",
    "Noto Sans Mono",
    "Consolas",
    "DejaVu Sans Mono",
)

BASE_FONT_PX = 14
FREQUENCY_VALUE_PX = 36
FREQUENCY_UNIT_PX = 16
FREQUENCY_BLOCK_WIDTH = 170

_STYLESHEET = Template("""
QMainWindow, QDialog {
    background-color: $bg;
}
QWidget {
    color: $text;
}
QToolTip {
    background-color: $surface;
    color: $text;
    border: 1px solid $border;
    padding: 4px 6px;
}
QLabel {
    background: transparent;
}
QLabel#toneLabel, QLabel#captionLabel, QLabel#subtitleLabel,
QLabel#statCaption, QLabel#scaleLabel, QLabel#statusLabel,
QLabel#bandValue, QLabel#bandFrequency, QLabel#sectionLabel,
QLabel#fieldLabel {
    color: $muted;
}
QLabel#subtitleLabel, QLabel#statusLabel {
    font-size: 13px;
}
QLabel#captionLabel, QLabel#fieldLabel {
    font-size: 13px;
}
QLabel#sectionLabel {
    color: $text;
    font-size: 16px;
    font-weight: 600;
    padding-top: 6px;
}
QLabel#bandValue {
    font-size: 12px;
}
QLabel#bandFrequency {
    font-size: 11px;
}

QFrame#card, QFrame#statCard {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#divider {
    background-color: $border;
}
QScrollArea {
    background: transparent;
    border: none;
}
BandStrip {
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}

QPushButton {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 0 12px;
    font-size: 16px;
    font-weight: 500;
}
QPushButton:hover:enabled {
    background-color: $surface;
    border-color: $border_hover;
}
QPushButton:pressed:enabled {
    background-color: $track;
}
QPushButton:focus {
    border-color: $accent;
}
QPushButton:disabled {
    color: $disabled;
}
QPushButton#navButton {
    background-color: $surface;
    padding: 0;
}
QPushButton#navButton:hover:enabled {
    background-color: $surface_hover;
}
QPushButton#navButton:pressed:enabled {
    background-color: $track;
}
QPushButton#navButton:disabled {
    background-color: transparent;
}
QPushButton#bandPlay {
    background-color: transparent;
    border: none;
    border-radius: 14px;
    padding: 0;
}
QPushButton#bandPlay:hover:enabled {
    background-color: $track;
}
QPushButton#playButton {
    background-color: $accent;
    color: $on_accent;
    border: 1px solid $accent;
    font-size: 17px;
    font-weight: 600;
}
QPushButton#playButton:hover:enabled {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QPushButton#playButton:pressed:enabled {
    background-color: $accent;
}
QPushButton#playButton:focus {
    border: 2px solid $text;
}
QPushButton#playButton:disabled {
    background-color: $surface;
    color: $disabled;
    border: 1px solid $border;
}

QProgressBar {
    border: none;
    border-radius: 3px;
    background-color: $track;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 3px;
}

QSlider::groove:vertical {
    width: 6px;
    background-color: $track;
    border-radius: 3px;
}
QSlider::handle:vertical {
    background-color: $accent;
    height: 16px;
    margin: 0 -6px;
    border-radius: 8px;
}
QSlider::handle:vertical:hover {
    background-color: $accent_hover;
}

QComboBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 0 12px;
    font-size: 14px;
}
QComboBox:hover:enabled {
    border-color: $border_hover;
}
QComboBox:focus {
    border-color: $accent;
}
QComboBox:disabled {
    color: $disabled;
}
QComboBox::drop-down {
    border: none;
    width: 30px;
}
QComboBox::down-arrow {
    image: none;
}
QComboBox QAbstractItemView {
    background-color: $surface;
    border: 1px solid $border;
    selection-background-color: $border;
    selection-color: $text;
    outline: 0;
    padding: 4px;
}

QDoubleSpinBox, QSpinBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 5px 8px;
    min-height: 24px;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: $accent;
}
QPlainTextEdit {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
}
QLineEdit {
    background-color: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 22px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:focus {
    border-color: $accent;
}

QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: $border_hover;
    border-radius: 4px;
    min-width: 40px;
}
QScrollBar::handle:horizontal:hover {
    background: $muted;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
""").substitute(
    bg=COLOR_BG,
    surface=COLOR_SURFACE,
    surface_hover=COLOR_SURFACE_HOVER,
    border=COLOR_BORDER,
    border_hover=COLOR_BORDER_HOVER,
    text=COLOR_TEXT,
    muted=COLOR_MUTED,
    disabled=COLOR_DISABLED,
    track=COLOR_TRACK,
    accent=COLOR_ACCENT,
    accent_hover=COLOR_ACCENT_HOVER,
    on_accent=COLOR_ON_ACCENT,
)


PLAY_BUTTON_LOOK = {
    "start": ("Запустить", "start"),
    "pause": ("Пауза", "pause"),
    "resume": ("Продолжить", "start"),
}


def play_button_mode(state: PlayerState) -> str:
    """Режим главной кнопки: что она сделает при нажатии в состоянии state."""
    if state is PlayerState.PLAYING:
        return "pause"
    if state is PlayerState.PAUSED:
        return "resume"
    return "start"


def autoplay_text(enabled: bool) -> str:
    """Подпись переключателя автовоспроизведения по его состоянию."""
    return "Автовоспроизведение: " + ("включено" if enabled else "выключено")


def autoplay_label() -> str:
    """Постоянная подпись переключателя автовоспроизведения."""
    return "Автовоспроизведение"


def pick_font_family(candidates: Sequence[str], fallback: str) -> str:
    """Первое семейство из candidates, установленное в системе, иначе fallback."""
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return fallback


# -----------------------------------------------------------------------------
# Иконки и собственные виджеты
# -----------------------------------------------------------------------------


def _draw_glyph(kind: str, size: int, color: str) -> QPixmap:
    """Нарисовать значок kind в квадрате size × size (сетка 24 × 24)."""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    qcolor = QColor(color)

    if kind == "settings":
        painter.setPen(
            QPen(
                qcolor,
                2.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(12.0, 12.0), 4.0, 4.0)
        for angle in range(0, 360, 45):
            radians = math.radians(angle)
            painter.drawLine(
                QPointF(12.0 + 6.5 * math.cos(radians), 12.0 + 6.5 * math.sin(radians)),
                QPointF(12.0 + 9.5 * math.cos(radians), 12.0 + 9.5 * math.sin(radians)),
            )
    elif kind == "presetSave":
        pen = QPen(
            qcolor,
            1.9,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(7.0, 4.0),
                    QPointF(17.0, 4.0),
                    QPointF(17.0, 20.0),
                    QPointF(12.0, 15.5),
                    QPointF(7.0, 20.0),
                ]
            )
        )
    elif kind == "delete":
        painter.setPen(
            QPen(
                qcolor,
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(5.0, 7.0), QPointF(19.0, 7.0))
        painter.drawLine(QPointF(9.0, 4.0), QPointF(15.0, 4.0))
        painter.drawRoundedRect(QRectF(7.0, 7.0, 10.0, 13.0), 2.0, 2.0)
        painter.drawLine(QPointF(10.0, 10.0), QPointF(10.0, 17.0))
        painter.drawLine(QPointF(14.0, 10.0), QPointF(14.0, 17.0))
    else:
        glyph = "start" if kind == "bandStart" else "pause" if kind == "bandPause" else kind
        painter.setBrush(qcolor)
        if glyph == "start":
            # Контур того же цвета скругляет углы треугольника.
            painter.setPen(
                QPen(
                    qcolor,
                    1.5,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPolygon(QPolygonF([QPointF(7.5, 5), QPointF(7.5, 19), QPointF(19.5, 12)]))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            if glyph == "pause":
                painter.drawRoundedRect(QRectF(6, 4, 4, 16), 1, 1)
                painter.drawRoundedRect(QRectF(14, 4, 4, 16), 1, 1)
            elif glyph == "stop":
                painter.drawRoundedRect(QRectF(5, 5, 14, 14), 2, 2)
    painter.end()
    return pixmap


def _make_icon(kind: str, size: int, normal: str, disabled: str) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_draw_glyph(kind, size, normal), QIcon.Mode.Normal)
    icon.addPixmap(_draw_glyph(kind, size, disabled), QIcon.Mode.Disabled)
    return icon


def make_icons() -> dict[str, QIcon]:
    """Значки кнопок: цвет в обычном и в отключённом состоянии подобран под тему."""
    return {
        "start": _make_icon("start", 18, COLOR_ON_ACCENT, COLOR_DISABLED),
        "pause": _make_icon("pause", 18, COLOR_ON_ACCENT, COLOR_DISABLED),
        "stop": _make_icon("stop", 18, COLOR_TEXT, COLOR_DISABLED),
        "settings": _make_icon("settings", 20, COLOR_ICON, COLOR_ICON_DISABLED),
        "presetSave": _make_icon("presetSave", 18, COLOR_ICON, COLOR_ICON_DISABLED),
        "delete": _make_icon("delete", 18, COLOR_ICON, COLOR_ICON_DISABLED),
        "bandStart": _make_icon("bandStart", 14, COLOR_ICON, COLOR_ICON_DISABLED),
        "bandPause": _make_icon("bandPause", 14, COLOR_ICON, COLOR_ICON_DISABLED),
    }


class ToggleSwitch(QAbstractButton):
    """Переключатель-«тумблер»; API как у отмечаемой кнопки (isChecked, toggled)."""

    _TRACK_WIDTH = 48
    _TRACK_HEIGHT = 26
    _KNOB = 20
    _PAD = 3
    _RING = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        # Фокус только с клавиатуры: после щелчка мышью кольцо не остаётся.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._TRACK_WIDTH + 2 * self._RING, self._TRACK_HEIGHT + 2 * self._RING)

    def paintEvent(self, e: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.5)

        checked = self.isChecked()
        track = QRectF(self._RING, self._RING, self._TRACK_WIDTH, self._TRACK_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLOR_ACCENT if checked else COLOR_SWITCH_OFF))
        painter.drawRoundedRect(track, self._TRACK_HEIGHT / 2, self._TRACK_HEIGHT / 2)

        knob_x = track.right() - self._PAD - self._KNOB if checked else track.left() + self._PAD
        painter.setBrush(QColor(COLOR_ON_ACCENT if checked else COLOR_MUTED))
        painter.drawEllipse(QRectF(knob_x, track.top() + self._PAD, self._KNOB, self._KNOB))

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLOR_TEXT), 2))
            radius = self.height() / 2 - 1
            painter.drawRoundedRect(
                QRectF(1, 1, self.width() - 2, self.height() - 2), radius, radius
            )


class ChevronComboBox(QComboBox):
    """Выпадающий список со стрелкой-шевроном, нарисованной в цвет темы."""

    def paintEvent(self, e: QPaintEvent | None) -> None:
        super().paintEvent(e)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = COLOR_MUTED if self.isEnabled() else COLOR_ICON_DISABLED
        painter.setPen(
            QPen(
                QColor(color),
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        center_x = self.width() - 20.0
        center_y = self.height() / 2
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(center_x - 4, center_y - 2),
                    QPointF(center_x, center_y + 2),
                    QPointF(center_x + 4, center_y - 2),
                ]
            )
        )


class BandScrollArea(QScrollArea):
    """Прокрутка полос: колесо мыши двигает ленту по горизонтали."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def wheelEvent(self, a0: QWheelEvent | None) -> None:
        if a0 is None:
            return
        bar = self.horizontalScrollBar()
        if bar is None:
            return
        bar.setValue(bar.value() - wheel_scroll_delta(a0))
        a0.accept()


class MainWindow(QMainWindow):
    """Окно эквалайзера: полосы, мастер, транспорт и настройки."""

    def __init__(
        self,
        settings: Settings,
        settings_path: Path,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._settings_path = settings_path
        self._settings_file_bytes = self._read_settings_file()
        self._config = ToneConfig.from_settings(settings)
        self._player = TonePlayer(
            self._config,
            settings.levels,
            MASTER_BASE_DB + settings.master_gain_db,
            stream_factory=stream_factory,
            level_scale=settings.level_scale,
        )
        self._player.set_autoplay(settings.autoplay)
        self._devices: list[OutputDevice] = []
        self._finishing_handled = False
        self._play_button_mode = ""
        self._bands: BandStrip | None = None
        self._last_band_index = -1
        # Сообщения под окном: о недоступном устройстве и о неудачной записи настроек.
        self._notice = ""
        self._save_error = ""
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_TIMEOUT_MS)
        connect_action(self._notice_timer.timeout, self._clear_notice)

        self.setWindowTitle("TonePlayer")
        self.resize(1040, 720)
        self.setMinimumSize(760, 560)

        self._sans_family = pick_font_family(
            _SANS_FAMILIES,
            QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family(),
        )
        self._mono_family = pick_font_family(
            _MONO_FAMILIES,
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family(),
        )
        base_font = QFont(self._sans_family)
        base_font.setPixelSize(BASE_FONT_PX)
        self.setFont(base_font)
        self.setStyleSheet(_STYLESHEET)

        self._icons = make_icons()
        self._build_ui()
        self._refresh_presets()

        application = cast("QApplication", QApplication.instance())
        connect_action(
            application.focusChanged,
            lambda: self._on_focus_changed(None, application.focusWidget()),
        )

        self._space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._space_shortcut.setAutoRepeat(False)
        connect_action(self._space_shortcut.activated, self._on_play_pause)

        self._settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self._settings_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        connect_action(self._settings_shortcut.activated, self._open_settings)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        connect_action(self._timer.timeout, self._refresh)
        self._timer.start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        connect_action(self._save_timer.timeout, self._save_settings)

        self._settings_watcher = QFileSystemWatcher(self)
        _ = self._settings_watcher.addPath(str(self._settings_path))
        connect_text(self._settings_watcher.fileChanged, self._on_settings_file_changed)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(SETTINGS_RELOAD_DELAY_MS)
        connect_action(self._reload_timer.timeout, self._reload_settings)

        self._refresh()

    # -------------------------------------------------------------------------
    # Свойства
    # -------------------------------------------------------------------------

    @property
    def player(self) -> TonePlayer:
        """Проигрыватель, управляемый окном."""
        return self._player

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        """При закрытии окна дописать настройки, остановить таймер и освободить устройство."""
        if self._save_timer.isActive():
            self._save_settings()
        if self._save_error:
            _ = QMessageBox.warning(self, "Настройки не сохранены", self._save_error)
        self._notice_timer.stop()
        self._reload_timer.stop()
        self._timer.stop()
        self._player.stop()
        super().closeEvent(a0)

    # -------------------------------------------------------------------------
    # Построение интерфейса
    # -------------------------------------------------------------------------

    def _mono_font(self, pixel_size: int, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
        font = QFont(self._mono_family)
        font.setPixelSize(pixel_size)
        font.setWeight(weight)
        return font

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addLayout(self._build_header())
        layout.addLayout(self._build_playback())
        layout.addLayout(self._build_bands(), 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.settings_button = self._icon_button("settings", "Настройки")
        connect_action(self.settings_button.clicked, self._open_settings)
        row.addWidget(self.settings_button)

        self.preset_selector = ChevronComboBox()
        self.preset_selector.setFixedHeight(38)
        self.preset_selector.setMinimumWidth(150)
        self.preset_selector.setMaximumWidth(210)
        self.preset_selector.setToolTip("Пресет: уровни эквалайзера и устройство вывода")
        connect_index(self.preset_selector.currentIndexChanged, self._on_preset_changed)
        row.addWidget(self.preset_selector)

        self.preset_save_button = self._icon_button("presetSave", "Сохранить пресет")
        connect_action(self.preset_save_button.clicked, self._on_save_preset)
        row.addWidget(self.preset_save_button)

        self.preset_delete_button = self._icon_button("delete", "Удалить пресет")
        connect_action(self.preset_delete_button.clicked, self._on_delete_preset)
        row.addWidget(self.preset_delete_button)

        row.addStretch(1)

        self._devices = list_output_devices()
        device_label = QLabel("Устройство:")
        device_label.setObjectName("fieldLabel")
        row.addWidget(device_label)

        self.device_selector = ChevronComboBox()
        self.device_selector.setFixedHeight(38)
        self.device_selector.setMinimumWidth(150)
        self.device_selector.setMaximumWidth(270)
        self.device_selector.setToolTip("Устройство вывода")
        self.device_selector.addItem(DEFAULT_DEVICE_LABEL, None)
        for device in self._devices:
            self.device_selector.addItem(device.label, device.index)
        configured = find_device(self._devices, self._settings.device)
        self.device_selector.setCurrentIndex(
            0 if configured is None else self.device_selector.findData(configured.index)
        )
        if configured is None and self._settings.device is not None:
            self._show_notice(
                f"Устройство «{self._settings.device}» не найдено, выбрано устройство по умолчанию."
            )
        self._player.set_device(configured.index if configured is not None else None)
        connect_index(self.device_selector.currentIndexChanged, self._on_device_changed)
        row.addWidget(self.device_selector)

        self.autoplay_state_label = QLabel(autoplay_label())
        self.autoplay_state_label.setObjectName("captionLabel")
        row.addWidget(self.autoplay_state_label)

        self.autoplay_button = ToggleSwitch()
        self.autoplay_button.setChecked(self._settings.autoplay)
        self.autoplay_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.autoplay_button.setAccessibleName(autoplay_text(self._settings.autoplay))
        self.autoplay_button.setToolTip(
            "Включено: во время воспроизведения новая частота звучит сразу.\n"
            "Выключено: частота переключается без звука."
        )
        connect_toggle(self.autoplay_button.toggled, self._on_autoplay_toggled)
        row.addWidget(self.autoplay_button)
        return row

    def _icon_button(self, icon_kind: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("navButton")
        button.setFixedSize(38, 38)
        button.setIcon(self._icons[icon_kind])
        button.setIconSize(QSize(19, 19))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_playback(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.play_button = QPushButton()
        self.play_button.setObjectName("playButton")
        self.play_button.setIcon(self._icons["start"])
        self.play_button.setIconSize(QSize(18, 18))
        self.play_button.setFixedSize(38, 38)
        self.play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setToolTip("Запустить (Пробел)")
        self.play_button.setAccessibleName("Запустить")
        connect_action(self.play_button.clicked, self._on_play_pause)
        row.addWidget(self.play_button)

        self.stop_button = self._icon_button("stop", "Остановить")
        self.stop_button.setFixedSize(38, 38)
        connect_action(self.stop_button.clicked, self._on_stop)
        row.addWidget(self.stop_button)

        frequency_box = QVBoxLayout()
        frequency_box.setSpacing(0)
        self.frequency_label = QLabel(self._frequency_html("—", ""))
        self.frequency_label.setTextFormat(Qt.TextFormat.RichText)
        self.frequency_label.setFixedWidth(FREQUENCY_BLOCK_WIDTH)
        self.frequency_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        frequency_box.addWidget(self.frequency_label)

        self.tone_counter_label = QLabel()
        self.tone_counter_label.setObjectName("captionLabel")
        self.tone_counter_label.setFixedWidth(FREQUENCY_BLOCK_WIDTH)
        self.tone_counter_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        frequency_box.addWidget(self.tone_counter_label)
        row.addLayout(frequency_box)

        row.addSpacing(8)
        self.tone_progress = QProgressBar()
        self.tone_progress.setRange(0, 100)
        self.tone_progress.setTextVisible(False)
        self.tone_progress.setFixedHeight(8)
        self.tone_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.tone_progress, 1)

        self.remaining_label = QLabel()
        self.remaining_label.setFont(self._mono_font(14, QFont.Weight.DemiBold))
        self.remaining_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.remaining_label.setMinimumWidth(116)
        row.addWidget(self.remaining_label)
        return row

    def _frequency_html(self, value: str, unit: str = "Гц") -> str:
        """Частота: число моноширинным акцентным шрифтом и приглушённая единица."""
        html = (
            f"<span style=\"font-family:'{self._mono_family}'; font-size:{FREQUENCY_VALUE_PX}px; "
            f'font-weight:600; color:{COLOR_ACCENT};">{value}</span>'
        )
        if unit:
            html += (
                f"<span style=\"font-family:'{self._sans_family}'; "
                f"font-size:{FREQUENCY_UNIT_PX}px; "
                f'font-weight:500; color:{COLOR_MUTED};">&nbsp;{unit}</span>'
            )
        return html

    def _build_bands(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        grid.addWidget(self._build_master(), 0, 0)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedWidth(1)
        grid.addWidget(divider, 0, 1, 2, 1)

        self._band_scroll = BandScrollArea()
        grid.addWidget(self._band_scroll, 0, 2)

        # Полоса прокрутки — отдельный виджет в своей строке, поэтому она не
        # отнимает высоту у колонок и мастер выровнен с ними.
        self._band_bar = QScrollBar(Qt.Orientation.Horizontal)
        self._band_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = self._band_scroll.horizontalScrollBar()
        if inner is not None:
            connect_action(inner.rangeChanged, self._sync_band_bar)
            connect_index(inner.valueChanged, self._band_bar.setValue)
            connect_index(self._band_bar.valueChanged, inner.setValue)
        grid.addWidget(self._band_bar, 1, 2)

        grid.setRowStretch(0, 1)
        grid.setColumnStretch(2, 1)
        self._rebuild_bands()
        self._sync_band_bar()
        return grid

    def _sync_band_bar(self) -> None:
        inner = self._band_scroll.horizontalScrollBar()
        if inner is None:
            return
        self._band_bar.setRange(inner.minimum(), inner.maximum())
        self._band_bar.setPageStep(inner.pageStep())
        self._band_bar.setSingleStep(inner.singleStep())
        self._band_bar.setValue(inner.value())

    def _build_master(self) -> QWidget:
        # Устроен как лента полос: шкала слева, рядом колонка той же ширины и
        # с теми же отступами, что у полосы.
        box = QWidget()
        box.setObjectName("masterBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(RULER_SPACING)

        self.master_ruler = LevelRuler(MASTER_GAIN_SCALE)
        row.addWidget(self.master_ruler)

        frame = MasterFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(COLUMN_MARGIN, COLUMN_MARGIN, COLUMN_MARGIN, COLUMN_MARGIN)
        layout.setSpacing(COLUMN_SPACING)

        self.master_value_label = QLabel()
        self.master_value_label.setObjectName("bandValue")
        self.master_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.master_value_label.setFixedHeight(COLUMN_VALUE_HEIGHT)
        layout.addWidget(self.master_value_label)

        self.master_slider = LevelSlider(MASTER_GAIN_SCALE)
        self.master_slider.setAccessibleName("Общая громкость")
        self.master_slider.setToolTip("Общая громкость\n↑ ↓ — изменить; → — перейти к полосам")
        connect_index(self.master_slider.valueChanged, self._on_master_changed)
        connect_index(self.master_slider.navigate_requested, self._navigate)
        layout.addWidget(self.master_slider, 1)
        self.master_ruler.set_slider(self.master_slider)

        caption = QLabel("Громкость")
        caption.setObjectName("bandFrequency")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setFixedHeight(COLUMN_FREQUENCY_HEIGHT)
        caption.setToolTip("Общая громкость")
        layout.addWidget(caption)

        placeholder = QWidget()
        placeholder.setFixedHeight(COLUMN_PLAY_SIZE)
        layout.addWidget(placeholder)

        row.addWidget(frame)

        self._master_frame = frame
        self._sync_master()
        return box

    def _rebuild_bands(self) -> None:
        focused_index: int | None = None
        focus = QApplication.focusWidget()
        if focus is not None and self._bands is not None:
            for index in range(len(self._settings.bands)):
                if focus is self._bands.column(index).slider:
                    focused_index = index
                    break
        if self._bands is not None:
            self._bands.deleteLater()
        strip = BandStrip(
            self._settings.level_scale,
            self._icons,
            on_level=self._on_band_level,
            on_play=self._on_band_play,
            on_select=self._on_band_select,
            on_navigate=self._navigate,
            on_scroll=self._scroll_bands,
        )
        strip.set_bands(self._settings.bands, self._settings.level_scale)
        self._bands = strip
        self._band_scroll.setWidget(strip)
        self._last_band_index = -1
        if focused_index is not None:
            index = min(focused_index, len(self._settings.bands) - 1)
        else:
            index = min(self._player.tone_index, len(self._settings.bands) - 1)
        strip.column(index).slider.setFocus()

    # -------------------------------------------------------------------------
    # Обработчики
    # -------------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        """Главная кнопка: запуск, пауза или продолжение — по состоянию проигрывателя."""
        state = self._player.state
        if state is PlayerState.PLAYING:
            self._player.pause()
            self._refresh()
        elif state is PlayerState.PAUSED:
            self._player.resume()
            self._refresh()
        else:
            index = 0 if state is PlayerState.FINISHED else self._player.tone_index
            self._start_playback(index)

    def _on_autoplay_toggled(self, enabled: bool) -> None:
        self._player.set_autoplay(enabled)
        self._update_autoplay_text(enabled)
        self._store(replace(self._settings, autoplay=enabled))

    def _update_autoplay_text(self, enabled: bool) -> None:
        self.autoplay_button.setAccessibleName(autoplay_text(enabled))

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        if a0 is None:
            super().keyPressEvent(a0)
            return
        if a0.modifiers() == Qt.KeyboardModifier.NoModifier:
            key = a0.key()
            if key == Qt.Key.Key_Left:
                self._navigate(-1)
                a0.accept()
                return
            if key == Qt.Key.Key_Right:
                self._navigate(1)
                a0.accept()
                return
            if key == Qt.Key.Key_Up:
                self._nudge_active_band(1)
                a0.accept()
                return
            if key == Qt.Key.Key_Down:
                self._nudge_active_band(-1)
                a0.accept()
                return
            if key == Qt.Key.Key_Space:
                self._on_play_pause()
                a0.accept()
                return
        super().keyPressEvent(a0)

    def _navigate(self, offset: int) -> None:
        if self.master_slider.hasFocus():
            if offset > 0:
                self._focus_band(self._player.tone_index)
            return
        if offset < 0 and self._player.tone_index == 0:
            focus = QApplication.focusWidget()
            first_slider = self._bands.column(0).slider if self._bands is not None else None
            if focus is self or focus is first_slider:
                self.master_slider.setFocus()
                return
        index = min(max(self._player.tone_index + offset, 0), self._config.tone_count - 1)
        self._select_band(index)

    def _focus_band(self, index: int) -> None:
        if self._bands is not None:
            self._bands.column(index).slider.setFocus()

    def _scroll_bands(self, delta: int) -> None:
        bar = self._band_scroll.horizontalScrollBar()
        if bar is not None:
            bar.setValue(bar.value() - delta)

    def _nudge_active_band(self, direction: int) -> None:
        index = self._player.tone_index
        scale = self._settings.level_scale
        level = scale.clamp(self._settings.bands[index].level_db + direction * scale.step_db)
        if level != self._settings.bands[index].level_db:
            self._on_band_level(index, level)
        if self._bands is not None:
            self._bands.column(index).set_level(level)

    def _select_band(self, index: int) -> None:
        """Выбрать полосу; во время звучания новая частота звучит сразу, если включён авторежим."""
        if index == self._player.tone_index and self._player.state is not PlayerState.FINISHED:
            self._focus_band(index)
            return
        if self._player.state is PlayerState.PLAYING and self._settings.autoplay:
            self._start_playback(index)
            self._focus_band(index)
            return
        self._player.select(index)
        self._finishing_handled = False
        self._refresh()
        self._focus_band(index)

    def _start_playback(self, index: int) -> None:
        """Запустить воспроизведение с тона index, показав ошибку устройства."""
        try:
            self._player.start(index)
        except AudioError as error:
            _ = QMessageBox.critical(self, "Ошибка воспроизведения", str(error))
            return
        self._finishing_handled = False
        self._refresh()

    def _on_stop(self) -> None:
        self._player.stop()
        self._finishing_handled = False
        self._refresh()

    def _on_band_level(self, index: int, level_db: float) -> None:
        self._player.set_level_at(index, level_db)
        bands = tuple(
            Band(band.frequency_hz, level_db) if number == index else band
            for number, band in enumerate(self._settings.bands)
        )
        self._store(replace(self._settings, bands=bands))

    def _on_band_select(self, index: int) -> None:
        self._select_band(index)

    def _on_band_play(self, index: int) -> None:
        self._focus_band(index)
        state = self._player.state
        if index == self._player.tone_index:
            if state is PlayerState.PLAYING:
                self._player.pause()
                self._refresh()
                return
            if state is PlayerState.PAUSED:
                self._player.resume()
                self._refresh()
                return
        self._start_playback(index)

    def _on_master_changed(self, position: int) -> None:
        gain_db = MASTER_GAIN_SCALE.at_position(position)
        self._player.set_master_db(MASTER_BASE_DB + gain_db)
        self._store(replace(self._settings, master_gain_db=gain_db))
        self._refresh()

    def _on_device_changed(self, index: int) -> None:
        data = cast("object", self.device_selector.itemData(index))
        device = next((item for item in self._devices if item.index == data), None)
        try:
            self._player.set_device(device.index if device is not None else None)
        except AudioError:
            return
        self._show_notice("")
        self._store(replace(self._settings, device=device.label if device is not None else None))
        self._refresh()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.setStyleSheet(self.styleSheet())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dialog.settings())

    def _apply_settings(
        self,
        settings: Settings,
        *,
        notice: str = "",
        persist: bool = True,
    ) -> None:
        """Применить настройки целиком: остановить звук, пересобрать полосы и ползунки."""
        self._player.stop()
        self._settings = settings
        self._config = ToneConfig.from_settings(settings)
        self._player.reconfigure(self._config, settings.levels, level_scale=settings.level_scale)
        self._player.set_master_db(MASTER_BASE_DB + settings.master_gain_db)
        self._player.set_autoplay(settings.autoplay)
        configured = find_device(self._devices, settings.device)
        self._player.set_device(configured.index if configured is not None else None)
        text = notice
        if configured is None and settings.device is not None:
            text = f"Устройство «{settings.device}» не найдено, выбрано устройство по умолчанию."
        self._show_notice(text)
        self._sync_device_selector()
        self._sync_autoplay()
        self._sync_master()
        self._rebuild_bands()
        self._refresh_presets()
        if persist:
            self._store(settings)
        self._refresh()

    def _sync_device_selector(self) -> None:
        target = self._player.device
        self.device_selector.blockSignals(True)
        index = 0 if target is None else self.device_selector.findData(target)
        self.device_selector.setCurrentIndex(max(index, 0))
        self.device_selector.blockSignals(False)

    def _sync_autoplay(self) -> None:
        self.autoplay_button.blockSignals(True)
        self.autoplay_button.setChecked(self._settings.autoplay)
        self.autoplay_button.blockSignals(False)
        self._update_autoplay_text(self._settings.autoplay)

    def _sync_master(self) -> None:
        position = MASTER_GAIN_SCALE.position(self._settings.master_gain_db)
        self.master_slider.blockSignals(True)
        self.master_slider.setValue(position)
        self.master_slider.blockSignals(False)
        self.master_value_label.setText(format_db(self._settings.master_gain_db, unit=False))

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        master_focused = new is self.master_slider
        self._master_frame.set_focused(master_focused)
        if self._bands is not None:
            self._bands.set_master_focused(master_focused)

    def _refresh_presets(self) -> None:
        self.preset_selector.blockSignals(True)
        self.preset_selector.clear()
        self.preset_selector.addItem("По умолчанию", None)
        for preset in self._settings.presets:
            self.preset_selector.addItem(preset.name, preset.name)
        if self._settings.active_preset is not None:
            index = self.preset_selector.findData(self._settings.active_preset)
            if index >= 0:
                self.preset_selector.setCurrentIndex(index)
        self.preset_selector.blockSignals(False)
        self.preset_delete_button.setEnabled(self._settings.active_preset is not None)

    def _on_preset_changed(self, index: int) -> None:
        data = cast("object", self.preset_selector.itemData(index))
        if data is None:
            if self._settings.active_preset is not None:
                self._store(replace(self._settings, active_preset=None))
                self.preset_delete_button.setEnabled(False)
            return
        name = str(data)
        available = {device.label for device in self._devices}
        try:
            application = apply_preset(self._settings, name, available)
        except PresetError as error:
            _ = QMessageBox.warning(self, "Пресет не применён", str(error))
            self._refresh_presets()
            return
        notice = ""
        if application.missing_device is not None:
            notice = (
                f"Устройство «{application.missing_device}» из пресета не найдено, "
                "выбрано устройство по умолчанию."
            )
        self._apply_settings(application.settings, notice=notice)

    def _ask_preset_name(self, default: str) -> str | None:
        """Запросить имя пресета в оформленном диалоге."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Сохранить пресет")
        dialog.setMinimumWidth(400)
        dialog.setStyleSheet(self.styleSheet())

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Имя пресета:"))

        edit = QLineEdit(default)
        edit.setFixedHeight(38)
        edit.selectAll()
        layout.addWidget(edit)

        buttons = QDialogButtonBox()
        save = cast(
            "QPushButton",
            buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole),
        )
        _ = buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        save.setDefault(True)
        save.setObjectName("playButton")
        buttons.setFixedHeight(38)
        connect_action(buttons.accepted, dialog.accept)
        connect_action(buttons.rejected, dialog.reject)
        connect_action(edit.returnPressed, dialog.accept)
        layout.addWidget(buttons)

        edit.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return edit.text()

    def _on_save_preset(self) -> None:
        default = self._settings.active_preset or "Пресет"
        name = self._ask_preset_name(default)
        if name is None:
            return
        try:
            existing = find_preset(self._settings, name.strip())
            overwrite = False
            if existing is not None:
                answer = QMessageBox.question(
                    self,
                    "Пресет уже существует",
                    f"Заменить пресет «{existing.name}»?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            settings = save_preset(self._settings, name, overwrite=overwrite)
        except PresetError as error:
            _ = QMessageBox.warning(self, "Пресет не сохранён", str(error))
            return
        self._show_notice(f"Пресет «{settings.active_preset}» сохранён.")
        self._store(settings)
        self._refresh_presets()
        self._refresh()

    def _on_delete_preset(self) -> None:
        name = self._settings.active_preset
        if name is None:
            return
        answer = QMessageBox.question(
            self,
            "Удалить пресет",
            f"Удалить пресет «{name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            settings = delete_preset(self._settings, name)
        except PresetError as error:
            _ = QMessageBox.warning(self, "Пресет не удалён", str(error))
            return
        self._store(settings)
        self._refresh_presets()
        self._refresh()

    def _store(self, settings: Settings) -> None:
        """Принять новые настройки; файл записывается после короткой паузы."""
        self._settings = settings
        self._save_timer.start()

    def _show_notice(self, text: str) -> None:
        """Показать служебное сообщение и убрать его через несколько секунд."""
        self._notice = text
        if text:
            self._notice_timer.start()
        else:
            self._notice_timer.stop()

    def _clear_notice(self) -> None:
        if not self._notice:
            return
        self._notice = ""
        self._refresh()

    def _read_settings_file(self) -> bytes:
        try:
            return self._settings_path.read_bytes()
        except OSError:
            return b""

    def _on_settings_file_changed(self, path: str) -> None:
        if Path(path) != self._settings_path:
            return
        # Редакторы часто заменяют файл целиком; после этого наблюдение нужно вернуть.
        _ = self._settings_watcher.addPath(str(self._settings_path))
        self._reload_timer.start()

    def _reload_settings(self) -> None:
        """Подхватить settings.json, изменённый снаружи, не затирая его."""
        _ = self._settings_watcher.addPath(str(self._settings_path))
        content = self._read_settings_file()
        if not content or content == self._settings_file_bytes:
            return
        self._save_timer.stop()
        try:
            settings = load_settings(self._settings_path)
        except SettingsError as error:
            self._show_notice(f"Не удалось перечитать настройки: {error}")
            self._refresh()
            return
        self._settings_file_bytes = content
        if settings == self._settings:
            return
        self._apply_settings(
            settings,
            notice="Настройки перечитаны из файла.",
            persist=False,
        )

    def _save_settings(self) -> None:
        self._save_timer.stop()
        content = self._read_settings_file()
        if content and content != self._settings_file_bytes:
            self._reload_settings()
            return
        try:
            save_settings(self._settings_path, self._settings)
        except SettingsError as error:
            self._save_error = str(error)
        else:
            self._save_error = ""
            self._settings_file_bytes = self._read_settings_file()
            _ = self._settings_watcher.addPath(str(self._settings_path))

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

        self.frequency_label.setText(
            self._frequency_html(format_frequency_value(self._player.current_frequency))
        )
        self.tone_counter_label.setText(
            f"Тон {progress.tone_index + 1} из {self._config.tone_count}"
        )
        self.remaining_label.setText(f"осталось {format_duration(progress.tone_remaining)}")
        self.tone_progress.setValue(int(round(progress.tone_fraction * 100)))

        self.stop_button.setEnabled(state in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.device_selector.setEnabled(state not in (PlayerState.PLAYING, PlayerState.PAUSED))

        mode = play_button_mode(state)
        if mode != self._play_button_mode:
            self._play_button_mode = mode
            label, icon_kind = PLAY_BUTTON_LOOK[mode]
            self.play_button.setIcon(self._icons[icon_kind])
            self.play_button.setToolTip(f"{label} (Пробел)")
            self.play_button.setAccessibleName(label)

        if self._bands is not None:
            self._bands.set_current(progress.tone_index, state is PlayerState.PLAYING)
            if progress.tone_index != self._last_band_index:
                self._last_band_index = progress.tone_index
                self._band_scroll.ensureWidgetVisible(
                    self._bands.column(progress.tone_index), 48, 0
                )

        self.master_value_label.setText(
            format_db(self._player.master_db - MASTER_BASE_DB, unit=False)
        )

        status = self._player.last_status or self._save_error or self._notice
        self.status_label.setText(status)
        self.status_label.setVisible(bool(status))
