"""Главное окно проигрывателя контрольных тонов."""

import math
from collections.abc import Callable, Sequence
from pathlib import Path
from string import Template
from typing import Protocol, cast

from PyQt6.QtCore import QLocale, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audio_engine import DEFAULT_DEVICE_LABEL, StreamFactory, TonePlayer, list_output_devices
from calibration import CalibrationResult, apply_measurement
from config_loader import save_gain_db
from error import AudioError, ConfigError
from models import PlayerState, ToneConfig

MIN_OFFSET_DB = -12.0
MAX_OFFSET_DB = 12.0
LEVEL_STEP_DB = 0.5
REFRESH_INTERVAL_MS = 50

# Диапазон и шаг поля «Измерено» (звуковое давление звукомера), дБ.
MIN_MEASURED_DB = 0.0
MAX_MEASURED_DB = 200.0
MEASURED_STEP_DB = 0.1

# -----------------------------------------------------------------------------
# Оформление
# -----------------------------------------------------------------------------

COLOR_BG = "#14181d"
COLOR_SURFACE = "#1b2128"
COLOR_SURFACE_HOVER = "#222a32"
COLOR_BORDER = "#2c343d"
COLOR_BORDER_HOVER = "#3b4651"
COLOR_TEXT = "#e6ebf0"
COLOR_MUTED = "#9aa6b2"
COLOR_DISABLED = "#6b7886"
COLOR_TRACK = "#232b33"
COLOR_ACCENT = "#62b6ff"
COLOR_ACCENT_HOVER = "#7ac2ff"
COLOR_ON_ACCENT = "#0b1117"
COLOR_ICON = "#c5ced8"
COLOR_ICON_DISABLED = "#4d5762"
COLOR_SWITCH_OFF = "#3a434d"

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
FREQUENCY_VALUE_PX = 72
FREQUENCY_UNIT_PX = 30
TICK_WIDTH = 10.0
TICK_HEIGHT = 4
TICK_GAP = 4.0

_STYLESHEET = Template("""
QMainWindow {
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
QLabel#statCaption, QLabel#scaleLabel, QLabel#statusLabel {
    color: $muted;
}
QLabel#subtitleLabel, QLabel#statusLabel {
    font-size: 13px;
}
QLabel#statCaption {
    font-size: 12px;
}
QLabel#titleLabel {
    font-size: 16px;
    font-weight: 500;
}
QLabel#targetValue {
    color: $accent;
}
QFrame#card, QFrame#statCard {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#statCard {
    border-radius: 10px;
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
    border-radius: 4px;
    background-color: $track;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 4px;
}

QSlider::groove:horizontal {
    height: 6px;
    background-color: $track;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: $accent;
    width: 18px;
    margin: -6px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background-color: $accent_hover;
}

QDoubleSpinBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 0 12px;
    font-size: 15px;
    selection-background-color: $border_hover;
    selection-color: $text;
}
QDoubleSpinBox:hover:enabled {
    border-color: $border_hover;
}
QDoubleSpinBox:focus {
    border-color: $accent;
}

QComboBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 0 12px;
    font-size: 15px;
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
    width: 32px;
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


class VoidSignal(Protocol):
    def connect(self, slot: Callable[[], None], /) -> object: ...


class IntSignal(Protocol):
    def connect(self, slot: Callable[[int], None], /) -> object: ...


class BoolSignal(Protocol):
    def connect(self, slot: Callable[[bool], None], /) -> object: ...


class FloatSignal(Protocol):
    def connect(self, slot: Callable[[float], None], /) -> object: ...


def connect_action(signal: VoidSignal, slot: Callable[[], None]) -> None:
    _ = signal.connect(slot)


def connect_index(signal: IntSignal, slot: Callable[[int], None]) -> None:
    _ = signal.connect(slot)


def connect_toggle(signal: BoolSignal, slot: Callable[[bool], None]) -> None:
    _ = signal.connect(slot)


def connect_value(signal: FloatSignal, slot: Callable[[float], None]) -> None:
    _ = signal.connect(slot)


# Вид главной кнопки по режиму: подпись и значок.
PLAY_BUTTON_LOOK = {
    "start": ("Старт", "start"),
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


def autoplay_state_text(enabled: bool) -> str:
    """Короткая подпись состояния автовоспроизведения под заголовком карточки."""
    return "Включено" if enabled else "Выключено"


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
    """Нарисовать значок kind в квадрате size × size (сетка 24 × 24, как в макете)."""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    qcolor = QColor(color)

    if kind in ("previous", "next"):
        pen = QPen(
            qcolor,
            2.0,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        points = ((15, 5), (8, 12), (15, 19)) if kind == "previous" else ((9, 5), (16, 12), (9, 19))
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
    else:
        painter.setBrush(qcolor)
        if kind == "start":
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
            if kind == "pause":
                painter.drawRoundedRect(QRectF(6, 4, 4, 16), 1, 1)
                painter.drawRoundedRect(QRectF(14, 4, 4, 16), 1, 1)
            elif kind == "stop":
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
        "previous": _make_icon("previous", 22, COLOR_ICON, COLOR_ICON_DISABLED),
        "next": _make_icon("next", 22, COLOR_ICON, COLOR_ICON_DISABLED),
        "start": _make_icon("start", 18, COLOR_ON_ACCENT, COLOR_DISABLED),
        "pause": _make_icon("pause", 18, COLOR_ON_ACCENT, COLOR_DISABLED),
        "stop": _make_icon("stop", 18, COLOR_TEXT, COLOR_DISABLED),
    }


class ToggleSwitch(QAbstractButton):
    """Переключатель-«тумблер»; API как у отмечаемой кнопки (isChecked, toggled)."""

    _TRACK_WIDTH = 52
    _TRACK_HEIGHT = 30
    _KNOB = 24
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


class ClickableFrame(QFrame):
    """Карточка, щелчок по любой части которой переключает связанный тумблер."""

    def __init__(self, target: QAbstractButton) -> None:
        super().__init__()
        self._target = target
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:
        if (
            a0 is not None
            and a0.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(a0.position().toPoint())
        ):
            self._target.toggle()
        super().mouseReleaseEvent(a0)


class ToneTicks(QWidget):
    """Лента из засечек по числу тонов; текущий тон выделен акцентным цветом."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self._current = 0
        self.setFixedHeight(TICK_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, count: int, current: int) -> None:
        if (count, current) == (self._count, self._current):
            return
        self._count = count
        self._current = current
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        if self._count <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        available = float(self.width())
        tick_width = (available - TICK_GAP * (self._count - 1)) / self._count
        tick_width = max(2.0, min(TICK_WIDTH, tick_width))
        total = tick_width * self._count + TICK_GAP * (self._count - 1)
        x = (available - total) / 2
        radius = TICK_HEIGHT / 2
        for index in range(self._count):
            painter.setBrush(QColor(COLOR_ACCENT if index == self._current else COLOR_BORDER))
            painter.drawRoundedRect(QRectF(x, 0, tick_width, TICK_HEIGHT), radius, radius)
            x += tick_width + TICK_GAP


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
        center_x = self.width() - 22.0
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


class MainWindow(QMainWindow):
    """Окно с управлением воспроизведением и индикацией текущей частоты."""

    def __init__(
        self,
        config: ToneConfig,
        stream_factory: StreamFactory | None = None,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        # Файл, в который записывается калибровка; None — только в памяти.
        self._config_path = config_path
        self._player = TonePlayer(config, stream_factory=stream_factory)
        self._finishing_handled = False
        self._play_button_mode = ""
        # Введённые, но ещё не применённые показания звукомера по номерам тонов.
        self._measured: dict[int, float] = {}
        self._calibration_tone = -1

        self.setWindowTitle("TonePlayer")
        self.resize(560, 800)

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

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        """При закрытии окна остановить таймер и освободить устройство вывода."""
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
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(18)

        layout.addLayout(self._build_header())
        layout.addLayout(self._build_progress())
        layout.addLayout(self._build_transport())
        layout.addWidget(self._build_autoplay_card())
        layout.addLayout(self._build_level())
        layout.addWidget(self._build_calibration_card())
        # Лишняя высота окна уходит сюда: выбор устройства и статус остаются внизу.
        layout.addStretch(1)
        layout.addLayout(self._build_device())

        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

    def _build_header(self) -> QHBoxLayout:
        """Стрелки переключения тонов и крупная частота с лентой позиции."""
        row = QHBoxLayout()
        row.setSpacing(16)

        self.previous_button = self._nav_button("previous", "Предыдущая частота")
        connect_action(self.previous_button.clicked, self._on_previous_tone)
        row.addWidget(self.previous_button, 0, Qt.AlignmentFlag.AlignVCenter)

        center = QVBoxLayout()
        center.setSpacing(4)

        self.tone_label = QLabel()
        self.tone_label.setObjectName("toneLabel")
        self.tone_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tone_font = self.tone_label.font()
        tone_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        self.tone_label.setFont(tone_font)
        center.addWidget(self.tone_label)

        self.frequency_label = QLabel(self._frequency_html("—", ""))
        self.frequency_label.setTextFormat(Qt.TextFormat.RichText)
        self.frequency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.frequency_label)

        center.addSpacing(6)
        self.tone_ticks = ToneTicks()
        center.addWidget(self.tone_ticks)

        row.addLayout(center, 1)

        self.next_button = self._nav_button("next", "Следующая частота")
        connect_action(self.next_button.clicked, self._on_next_tone)
        row.addWidget(self.next_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _nav_button(self, icon_kind: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("navButton")
        button.setFixedSize(48, 48)
        button.setIcon(self._icons[icon_kind])
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

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

    def _build_progress(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(14)
        self.tone_progress_value, self.tone_progress = self._add_progress_row(
            block, "Текущий тон · осталось"
        )
        self.total_progress_value, self.total_progress = self._add_progress_row(
            block, "Общее время · осталось"
        )
        return block

    def _add_progress_row(self, block: QVBoxLayout, caption: str) -> tuple[QLabel, QProgressBar]:
        """Строка «подпись — время и процент» над тонкой полосой прогресса."""
        row = QVBoxLayout()
        row.setSpacing(8)

        header = QHBoxLayout()
        caption_label = QLabel(caption)
        caption_label.setObjectName("captionLabel")
        value_label = QLabel()
        value_label.setTextFormat(Qt.TextFormat.RichText)
        value_label.setFont(self._mono_font(16))
        header.addWidget(caption_label)
        header.addStretch(1)
        header.addWidget(value_label)
        row.addLayout(header)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        row.addWidget(bar)

        block.addLayout(row)
        return value_label, bar

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        # Одна главная кнопка: «Старт», «Пауза» или «Продолжить» по состоянию.
        self.play_button = self._action_button("Старт", "start")
        self.play_button.setObjectName("playButton")
        connect_action(self.play_button.clicked, self._on_play_pause)
        self.stop_button = self._action_button("Стоп", "stop")
        connect_action(self.stop_button.clicked, self._on_stop)

        # Главное действие вдвое шире «Стопа».
        row.addWidget(self.play_button, 2)
        row.addWidget(self.stop_button, 1)
        return row

    def _action_button(self, text: str, icon_kind: str) -> QPushButton:
        button = QPushButton(text)
        button.setFixedHeight(56)
        button.setIcon(self._icons[icon_kind])
        button.setIconSize(QSize(18, 18))
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_autoplay_card(self) -> QFrame:
        tooltip = (
            "Включено: во время воспроизведения новая частота звучит сразу.\n"
            "Выключено: частота переключается без звука, запуск — кнопкой «Старт»."
        )
        self.autoplay_button = ToggleSwitch()
        self.autoplay_button.setChecked(True)
        self.autoplay_button.setAccessibleName(autoplay_text(True))
        self.autoplay_button.setToolTip(tooltip)
        connect_toggle(self.autoplay_button.toggled, self._on_autoplay_toggled)

        card = ClickableFrame(self.autoplay_button)
        card.setObjectName("card")
        card.setToolTip(tooltip)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Автовоспроизведение")
        title.setObjectName("titleLabel")
        self.autoplay_state_label = QLabel(autoplay_state_text(True))
        self.autoplay_state_label.setObjectName("subtitleLabel")
        for label in (title, self.autoplay_state_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            texts.addWidget(label)

        row.addLayout(texts, 1)
        row.addWidget(self.autoplay_button)
        return card

    def _build_level(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(12)

        header = QHBoxLayout()
        caption = QLabel("Уровень")
        caption.setObjectName("captionLabel")
        self.level_label = QLabel()
        self.level_label.setFont(self._mono_font(24, QFont.Weight.DemiBold))
        header.addWidget(caption, 0, Qt.AlignmentFlag.AlignBottom)
        header.addStretch(1)
        header.addWidget(self.level_label)
        block.addLayout(header)

        slider_block = QVBoxLayout()
        slider_block.setSpacing(4)
        self.level_slider = QSlider(Qt.Orientation.Horizontal)
        self.level_slider.setRange(offset_to_slider(MIN_OFFSET_DB), offset_to_slider(MAX_OFFSET_DB))
        self.level_slider.setSingleStep(1)
        self.level_slider.setPageStep(offset_to_slider(2.0))
        self.level_slider.setToolTip(f"Уровень сигнала, шаг {LEVEL_STEP_DB:g} дБ")
        self.level_slider.setValue(offset_to_slider(self._config.default_offset_db))
        # Значение из конфигурации может выходить за границы ползунка: тогда
        # уровень приводится к границе, чтобы подпись и звук не расходились.
        self._player.set_offset_db(slider_to_offset(self.level_slider.value()))
        connect_index(self.level_slider.valueChanged, self._on_level_changed)
        slider_block.addWidget(self.level_slider)

        scale = QHBoxLayout()
        scale.setContentsMargins(4, 0, 4, 0)
        scale.addWidget(self._scale_label(MIN_OFFSET_DB))
        scale.addStretch(1)
        scale.addWidget(self._scale_label(0.0))
        scale.addStretch(1)
        scale.addWidget(self._scale_label(MAX_OFFSET_DB))
        slider_block.addLayout(scale)
        block.addLayout(slider_block)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        tone_card, self.tone_gain_label = self._stat_card("Тон")
        total_card, self.total_gain_label = self._stat_card("Итого")
        target_card, self.target_label = self._stat_card("Цель", accent=True)
        for card in (tone_card, total_card, target_card):
            stats.addWidget(card, 1)
        block.addLayout(stats)
        return block

    def _build_calibration_card(self) -> QFrame:
        """Карточка калибровки: показание звукомера пересчитывается в gain_db_i тона."""
        tooltip = (
            "Введите давление, которое показал звукомер для звучащей частоты, и нажмите\n"
            "«Применить»: калибровка тона сдвинется на разность «цель − измерено»\n"
            "и будет записана в config.toml."
        )
        card = QFrame()
        card.setObjectName("card")
        card.setToolTip(tooltip)
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(10)

        header = QHBoxLayout()
        self.calibration_title = QLabel("Калибровка")
        self.calibration_title.setObjectName("titleLabel")
        self.calibration_gain_label = QLabel()
        self.calibration_gain_label.setFont(self._mono_font(17, QFont.Weight.DemiBold))
        header.addWidget(self.calibration_title)
        header.addStretch(1)
        header.addWidget(self.calibration_gain_label)
        box.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(10)
        caption = QLabel("Измерено звукомером")
        caption.setObjectName("captionLabel")

        self.measured_spin = QDoubleSpinBox()
        # Десятичная точка, как в остальных значениях окна, независимо от локали системы.
        self.measured_spin.setLocale(
            QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
        )
        self.measured_spin.setRange(MIN_MEASURED_DB, MAX_MEASURED_DB)
        self.measured_spin.setDecimals(1)
        self.measured_spin.setSingleStep(MEASURED_STEP_DB)
        self.measured_spin.setSuffix(" дБ")
        self.measured_spin.setValue(self._config.target_spl_db)
        self.measured_spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.measured_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        # Значение фиксируется по Enter или уходу из поля; колесо мыши работает
        # только в поле с фокусом, чтобы показание не менялось случайно.
        self.measured_spin.setKeyboardTracking(False)
        self.measured_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.measured_spin.setFont(self._mono_font(16))
        self.measured_spin.setFixedSize(130, 44)
        self.measured_spin.setAccessibleName("Измерено звукомером, дБ")
        connect_value(self.measured_spin.valueChanged, self._on_measured_changed)

        self.apply_button = QPushButton("Применить")
        self.apply_button.setFixedHeight(44)
        self.apply_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.setToolTip(tooltip)
        connect_action(self.apply_button.clicked, self._on_apply_calibration)

        row.addWidget(caption, 1)
        row.addWidget(self.measured_spin)
        row.addWidget(self.apply_button)
        box.addLayout(row)

        self.calibration_note = QLabel()
        self.calibration_note.setObjectName("statusLabel")
        self.calibration_note.setWordWrap(True)
        self.calibration_note.setVisible(False)
        box.addWidget(self.calibration_note)
        return card

    def _scale_label(self, value_db: float) -> QLabel:
        label = QLabel(format_scale(value_db))
        label.setObjectName("scaleLabel")
        label.setFont(self._mono_font(13))
        return label

    def _stat_card(self, caption: str, *, accent: bool = False) -> tuple[QFrame, QLabel]:
        """Карточка «подпись — значение» для показателей уровня."""
        card = QFrame()
        card.setObjectName("statCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(0)

        caption_label = QLabel(caption)
        caption_label.setObjectName("statCaption")
        value_label = QLabel()
        value_label.setFont(self._mono_font(17, QFont.Weight.DemiBold))
        if accent:
            value_label.setObjectName("targetValue")
        box.addWidget(caption_label)
        box.addWidget(value_label)
        return card, value_label

    def _build_device(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(8)

        caption = QLabel("Устройство вывода")
        caption.setObjectName("captionLabel")
        block.addWidget(caption)

        self.device_selector = ChevronComboBox()
        self.device_selector.setFixedHeight(44)
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
        block.addWidget(self.device_selector)
        return block

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
            # После завершения сеанса запуск идёт заново с первой частоты,
            # после ручного переключения — с выбранной.
            index = 0 if state is PlayerState.FINISHED else self._player.tone_index
            self._start_playback(index)

    def _on_previous_tone(self) -> None:
        self._navigate(-1)

    def _on_next_tone(self) -> None:
        self._navigate(1)

    def _on_autoplay_toggled(self, enabled: bool) -> None:
        self._player.set_autoplay(enabled)
        self.autoplay_state_label.setText(autoplay_state_text(enabled))
        self.autoplay_button.setAccessibleName(autoplay_text(enabled))

    def _navigate(self, offset: int) -> None:
        """Сменить выбранную частоту.

        Новая частота звучит сразу только если воспроизведение уже идёт и
        автовоспроизведение включено. До запуска, на паузе и после завершения
        сеанса частота лишь выбирается: запуск — кнопкой «Старт», продолжение —
        кнопкой «Продолжить».
        """
        index = min(max(self._player.tone_index + offset, 0), self._config.tone_count - 1)
        if index == self._player.tone_index:
            return
        if self._player.state is PlayerState.PLAYING and self.autoplay_button.isChecked():
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

    def _on_measured_changed(self, value: float) -> None:
        self._measured[self._calibration_tone] = value

    def _on_apply_calibration(self) -> None:
        """Пересчитать калибровку текущего тона по показанию звукомера и сохранить её."""
        # Кнопка не забирает фокус, поэтому набранный текст фиксируется явно.
        self.measured_spin.interpretText()
        index = self._player.tone_index
        result = apply_measurement(
            gain_db=self._player.gain_at(index),
            offset_db=self._player.offset_db,
            measured_db=self.measured_spin.value(),
            target_db=self._config.target_spl_db,
        )
        self._player.set_gain_at(index, result.new_gain_db)
        _ = self._measured.pop(index, None)
        self._show_measured(index)

        message = calibration_message(result)
        if self._config_path is not None:
            try:
                save_gain_db(self._config_path, self._player.gain_db)
            except ConfigError as error:
                message += f" Не сохранено: {error}"
            else:
                message += f" Записано в {self._config_path.name}."
        self._set_calibration_note(message)
        self._refresh()

    def _show_measured(self, index: int) -> None:
        """Показать в поле введённое для тона показание или целевое давление."""
        value = self._measured.get(index, self._config.target_spl_db)
        _ = self.measured_spin.blockSignals(True)
        self.measured_spin.setValue(value)
        _ = self.measured_spin.blockSignals(False)

    def _set_calibration_note(self, text: str) -> None:
        self.calibration_note.setText(text)
        self.calibration_note.setVisible(bool(text))

    def _on_stop(self) -> None:
        self._player.stop()
        self._finishing_handled = False
        self._refresh()

    def _on_level_changed(self, value: int) -> None:
        self._player.set_offset_db(slider_to_offset(value))
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

        self.frequency_label.setText(
            self._frequency_html(format_frequency_value(self._player.current_frequency))
        )

        # Показание и сообщение калибровки относятся к конкретному тону: при смене
        # тона поле показывает его собственное значение, а прежнее сообщение уходит.
        if progress.tone_index != self._calibration_tone:
            self._calibration_tone = progress.tone_index
            self._show_measured(progress.tone_index)
            self._set_calibration_note("")

        self.tone_label.setText(f"Тон {progress.tone_index + 1} из {self._config.tone_count}")
        self.tone_ticks.set_state(self._config.tone_count, progress.tone_index)

        tone_percent = int(round(progress.tone_fraction * 100))
        total_percent = int(round(progress.total_fraction * 100))
        self.tone_progress_value.setText(
            progress_value_html(format_duration(progress.tone_remaining), tone_percent)
        )
        self.total_progress_value.setText(
            progress_value_html(format_duration(progress.total_remaining), total_percent)
        )
        self.tone_progress.setValue(tone_percent)
        self.total_progress.setValue(total_percent)

        self.stop_button.setEnabled(state in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.device_selector.setEnabled(state not in (PlayerState.PLAYING, PlayerState.PAUSED))
        self.previous_button.setEnabled(progress.tone_index > 0)
        self.next_button.setEnabled(progress.tone_index < self._config.tone_count - 1)

        mode = play_button_mode(state)
        if mode != self._play_button_mode:
            self._play_button_mode = mode
            text, icon_kind = PLAY_BUTTON_LOOK[mode]
            self.play_button.setText(text)
            self.play_button.setIcon(self._icons[icon_kind])

        # Пустая строка статуса скрывается: иначе она занимает высоту и отступ
        # и оставляет пустое место под выбором устройства.
        status = self._player.last_status
        self.status_label.setText(status)
        self.status_label.setVisible(bool(status))

        offset_db = self._player.offset_db
        gain_db = self._player.gain_at(progress.tone_index)
        self.calibration_title.setText(
            "Калибровка · " + format_frequency(self._player.current_frequency)
        )
        self.calibration_gain_label.setText(format_db(gain_db))
        self.level_label.setText(format_db(offset_db))
        self.tone_gain_label.setText(format_db(gain_db))
        self.total_gain_label.setText(format_db(gain_db + offset_db))
        self.target_label.setText(format_db(self._config.target_spl_db, signed=False))


def format_frequency_value(frequency: float) -> str:
    """Число частоты с пробелом как разделителем тысяч, без единицы измерения."""
    return f"{frequency:,.0f}".replace(",", " ")


def format_frequency(frequency: float) -> str:
    return f"{format_frequency_value(frequency)} Гц"


def format_duration(seconds: float) -> str:
    """Человекочитаемое время: минуты и секунды с округлением вверх."""
    total = int(math.ceil(max(seconds, 0.0)))
    return f"{total // 60}:{total % 60:02d}"


def format_db(value: float, *, signed: bool = True) -> str:
    """Уровень в дБ; вместо дефиса — настоящий минус."""
    text = f"{value:+.1f}" if signed else f"{value:.0f}"
    return text.replace("-", "−") + " дБ"


def calibration_message(result: CalibrationResult) -> str:
    """Что произошло после применения показания звукомера."""
    if result.changed:
        text = f"Калибровка: {format_db(result.old_gain_db)} → {format_db(result.new_gain_db)}."
    else:
        text = "Калибровка не изменилась."
    if result.shortfall_db > 0:
        text += (
            f" Сигнал на полной шкале, а давления не хватает: поднимите усиление колонок"
            f" примерно на {result.shortfall_db:.1f} дБ и измерьте снова."
        )
    elif result.clamped_low:
        text += " Достигнут нижний предел диапазона: снизьте усиление колонок."
    return text


def format_scale(value_db: float) -> str:
    """Подпись деления шкалы уровня: «−12», «0», «+12»."""
    if value_db == 0:
        return "0"
    return f"{value_db:+.0f}".replace("-", "−")


def progress_value_html(duration: str, percent: int) -> str:
    """Время выделено, процент приглушён: «0:10 · 0%»."""
    return (
        f'<span style="color:{COLOR_TEXT}; font-weight:600;">{duration}</span>'
        f'<span style="color:{COLOR_MUTED};"> · {percent}%</span>'
    )


def offset_to_slider(offset_db: float) -> int:
    """Перевести уровень сигнала, дБ, в положение ползунка."""
    return round(offset_db / LEVEL_STEP_DB)


def slider_to_offset(value: int) -> float:
    """Перевести положение ползунка в уровень сигнала, дБ."""
    return value * LEVEL_STEP_DB
