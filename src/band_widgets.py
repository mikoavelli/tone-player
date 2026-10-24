"""Виджеты эквалайзера: вертикальные ползунки, шкала и колонки полос."""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QRegion,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from models import Band, LevelScale
from theme import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_BORDER_HOVER,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_SURFACE_HOVER,
    COLOR_TEXT,
)
from ui_signals import connect_action, connect_index, connect_toggle
from ui_text import format_db, format_frequency_value, format_scale

COLUMN_WIDTH = 66
COLUMN_VALUE_HEIGHT = 20
COLUMN_FREQUENCY_HEIGHT = 18
COLUMN_PLAY_SIZE = 28
COLUMN_SPACING = 6
COLUMN_MARGIN = 4
# Ползунок внутри колонки: ширина колонки минус поля по бокам.
SLIDER_WIDTH = COLUMN_WIDTH - 2 * COLUMN_MARGIN
RULER_WIDTH = 40
# Промежуток между шкалой и колонкой: одинаковый в ленте полос и у мастера.
RULER_SPACING = 4
FRAME_RADIUS = 10


def wheel_scroll_delta(event: QWheelEvent) -> int:
    """Перевести колесо или жест тачпада в приращение горизонтальной прокрутки."""
    pixel = event.pixelDelta()
    if not pixel.isNull():
        return pixel.x() or pixel.y()
    angle = event.angleDelta()
    return angle.x() or angle.y()


def track_y(position: int, maximum: int, height: int) -> float:
    """Высота на дорожке ползунка для деления position (сверху — максимум)."""
    ratio = position / maximum if maximum > 0 else 0.0
    return height - ratio * height


def paint_column_frame(widget: QWidget, *, accent: bool) -> None:
    """Нарисовать рамку колонки; accent — яркая рамка воспроизведения."""
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(COLOR_SURFACE_HOVER if accent else COLOR_SURFACE))
    painter.setPen(QPen(QColor(COLOR_ACCENT if accent else COLOR_BORDER), 1))
    painter.drawRoundedRect(
        QRectF(widget.rect()).adjusted(1, 1, -1, -1), FRAME_RADIUS, FRAME_RADIUS
    )
    painter.end()


class LevelSlider(QSlider):
    """Вертикальный ползунок с чертой нуля и сбросом двойным щелчком."""

    navigate_requested = pyqtSignal(int)
    scroll_requested = pyqtSignal(int)
    activate_requested = pyqtSignal()
    focus_changed = pyqtSignal(bool)

    def __init__(self, scale: LevelScale, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._scale = scale
        self.setRange(0, scale.position_count)
        self._apply_steps()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _apply_steps(self) -> None:
        self.setSingleStep(1)
        self.setPageStep(max(round(1.0 / self._scale.step_db), 1))

    def set_scale(self, scale: LevelScale) -> None:
        """Сменить диапазон и шаг, сохранив положение, если оно помещается."""
        self._scale = scale
        self.setRange(0, scale.position_count)
        self._apply_steps()
        self.update()

    def zero_position(self) -> int:
        """Деление, соответствующее 0 дБ."""
        return self._scale.position(0.0)

    def handle_rect(self, position: int | None = None) -> QRect:
        """Прямоугольник ручки по геометрии стиля для деления position (по умолчанию текущего)."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        if position is not None:
            option.sliderPosition = position
            option.sliderValue = position
        style = self.style()
        if style is None:
            return QRect()
        return style.subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )

    def handle_center_y(self, position: int) -> float:
        """Точная высота центра ручки для деления position по геометрии стиля."""
        rect = self.handle_rect(position)
        if rect.isNull():
            return track_y(position, self.maximum(), self.height())
        return float(rect.center().y())

    def paintEvent(self, ev: QPaintEvent | None) -> None:
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        handle = self.handle_rect()

        # Черта нуля не должна просвечивать сквозь ручку.
        painter.save()
        if not handle.isNull():
            painter.setClipRegion(
                QRegion(self.rect()).subtracted(QRegion(handle.adjusted(-2, -2, 2, 2)))
            )
        y = self.handle_center_y(self.zero_position())
        painter.setPen(QPen(QColor(COLOR_BORDER_HOVER), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(8.0, y), QPointF(self.width() - 8.0, y))
        painter.restore()

        # Фокус с клавиатуры: светлое кольцо внутри кругляша (ручки).
        if self.hasFocus() and not handle.isNull():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLOR_TEXT), 2))
            ring = QRectF(handle).adjusted(1.5, 1.5, -1.5, -1.5)
            painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)

    def mouseDoubleClickEvent(self, a0: QMouseEvent | None) -> None:
        self.setValue(self.zero_position())
        if a0 is not None:
            a0.accept()

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:
        self.setValue(self.zero_position())
        if a0 is not None:
            a0.accept()

    def keyPressEvent(self, ev: QKeyEvent | None) -> None:
        if ev is not None and ev.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.navigate_requested.emit(-1 if ev.key() == Qt.Key.Key_Left else 1)
            ev.accept()
            return
        if ev is not None and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_requested.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def wheelEvent(self, e: QWheelEvent | None) -> None:
        if e is None:
            return
        self.scroll_requested.emit(wheel_scroll_delta(e))
        e.accept()

    def focusInEvent(self, a0: QFocusEvent | None) -> None:
        super().focusInEvent(a0)
        self.focus_changed.emit(True)

    def focusOutEvent(self, a0: QFocusEvent | None) -> None:
        super().focusOutEvent(a0)
        self.focus_changed.emit(False)


class LevelRuler(QWidget):
    """Общая шкала уровня слева от полос: максимум, ноль и минимум."""

    def __init__(self, scale: LevelScale, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._slider: LevelSlider | None = None
        self.setFixedWidth(RULER_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_scale(self, scale: LevelScale) -> None:
        self._scale = scale
        self.update()

    def set_slider(self, slider: LevelSlider) -> None:
        self._slider = slider
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)

        positions = (
            (self._scale.position(self._scale.max_db), format_scale(self._scale.max_db)),
            (self._scale.position(0.0), "0"),
            (self._scale.position(self._scale.min_db), format_scale(self._scale.min_db)),
        )
        if self._slider is not None:
            slider_top = self._slider.mapToGlobal(QPoint(0, 0)).y()
            ruler_top = self.mapToGlobal(QPoint(0, 0)).y()
            top = float(slider_top - ruler_top)
            y_positions = [
                top + self._slider.handle_center_y(position) for position, _ in positions
            ]
        else:
            y_positions = [
                track_y(position, self._scale.position_count, self.height())
                for position, _ in positions
            ]
        for (_, text), y in zip(positions, y_positions, strict=True):
            painter.setPen(QPen(QColor(COLOR_BORDER_HOVER), 1))
            painter.drawLine(QPointF(self.width() - 12.0, y), QPointF(self.width() - 5.0, y))
            painter.setPen(QColor(COLOR_MUTED))
            label_y = min(max(y - 8.0, 0.0), max(0.0, self.height() - 16.0))
            painter.drawText(
                QRectF(0.0, label_y, self.width() - 15.0, 16.0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                text,
            )


class MasterFrame(QWidget):
    """Колонка общей громкости: той же ширины и с той же рамкой, что у полосы."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._focused = False
        self.setFixedWidth(COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_focused(self, focused: bool) -> None:
        if self._focused != focused:
            self._focused = focused
            self.update()


class BandColumn(QWidget):
    """Колонка одной полосы: уровень, ползунок, частота и кнопка прослушивания."""

    def __init__(
        self,
        index: int,
        band: Band,
        scale: LevelScale,
        icons: dict[str, QIcon],
        *,
        on_level: Callable[[int, float], None],
        on_play: Callable[[int], None],
        on_select: Callable[[int], None],
        on_activate: Callable[[int], None],
        on_navigate: Callable[[int], None],
        on_scroll: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._scale = scale
        self._on_level = on_level
        self._on_play = on_play
        self._on_select = on_select
        self._on_activate_action = on_activate
        self._on_navigate = on_navigate
        self._on_scroll = on_scroll
        self._icons = icons
        self._current = False
        self._playing = False
        self._hover = False
        self._editing = False
        self._master_focused = False

        self.setFixedWidth(COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(COLUMN_MARGIN, COLUMN_MARGIN, COLUMN_MARGIN, COLUMN_MARGIN)
        layout.setSpacing(COLUMN_SPACING)

        self.value_label = QLabel()
        self.value_label.setObjectName("bandValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setFixedHeight(COLUMN_VALUE_HEIGHT)
        layout.addWidget(self.value_label)

        self.slider = LevelSlider(scale, self)
        self.slider.setAccessibleName(f"Уровень {format_frequency_value(band.frequency_hz)} Гц")
        self.slider.setValue(scale.position(band.level_db))
        connect_index(self.slider.valueChanged, self._on_value_changed)
        connect_action(self.slider.sliderPressed, self._on_slider_pressed)
        connect_action(self.slider.sliderReleased, self._on_slider_released)
        connect_index(self.slider.navigate_requested, self._on_navigate)
        connect_index(self.slider.scroll_requested, self._on_scroll)
        connect_action(self.slider.activate_requested, self._on_slider_activate)
        connect_toggle(self.slider.focus_changed, self._on_slider_focus_changed)
        layout.addWidget(self.slider, 1)

        self.frequency_label = QLabel(format_frequency_value(band.frequency_hz))
        self.frequency_label.setObjectName("bandFrequency")
        self.frequency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frequency_label.setFixedHeight(COLUMN_FREQUENCY_HEIGHT)
        layout.addWidget(self.frequency_label)

        self.play_button = QPushButton()
        self.play_button.setObjectName("bandPlay")
        self.play_button.setFixedSize(COLUMN_PLAY_SIZE, COLUMN_PLAY_SIZE)
        self.play_button.setIcon(icons["bandStart"])
        self.play_button.setIconSize(QSize(14, 14))
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        connect_action(self.play_button.clicked, self._on_play_clicked)
        layout.addWidget(self.play_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.set_level(band.level_db)
        self._update_tooltip()
        self.set_current(False, False)

    @property
    def index(self) -> int:
        return self._index

    def set_level(self, level_db: float) -> None:
        """Показать уровень, не порождая сигнала изменения."""
        self.slider.blockSignals(True)
        self.slider.setValue(self._scale.position(level_db))
        self.slider.blockSignals(False)
        self._update_text()

    def set_current(self, current: bool, playing: bool) -> None:
        """current — колонка воспроизводится; playing — активный звук."""
        if (current, playing) == (self._current, self._playing):
            return
        self._current = current
        self._playing = playing
        icon = self._icons["bandPause"] if current and playing else self._icons["bandStart"]
        self.play_button.setIcon(icon)
        self._update_text()
        self.update()

    def set_master_focused(self, focused: bool) -> None:
        if self._master_focused != focused:
            self._master_focused = focused
            self.update()

    def set_scale(self, scale: LevelScale) -> None:
        self._scale = scale
        self.slider.set_scale(scale)
        self._update_tooltip()

    def _level(self) -> float:
        return self._scale.at_position(self.slider.value())

    def _update_text(self) -> None:
        visible = self._current or self.slider.hasFocus() or self._hover or self._editing
        self.value_label.setText(format_db(self._level(), unit=False) if visible else "")
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        text = (
            f"{self.frequency_label.text()} Гц\n"
            f"Уровень: {format_db(self._level())}\n"
            "↑ ↓ — изменить, ← → — выбор ползунка\n"
            "Enter — включить воспроизведение, Пробел — пауза\n"
            "Двойной щелчок или правая кнопка — сбросить в 0 дБ"
        )
        self.setToolTip(text)
        self.slider.setToolTip(text)
        self.frequency_label.setToolTip(text)

    def _on_value_changed(self, _position: int) -> None:
        self._update_text()
        self._on_level(self._index, self._level())

    def _on_slider_pressed(self) -> None:
        self._editing = True
        self._on_select(self._index)
        self._update_text()

    def _on_slider_released(self) -> None:
        self._editing = False
        self._update_text()

    def _on_slider_activate(self) -> None:
        self._on_activate_action(self._index)

    def _on_slider_focus_changed(self, _focused: bool) -> None:
        self._update_text()

    def _on_play_clicked(self) -> None:
        self._on_play(self._index)

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        self._on_select(self._index)
        self.slider.setFocus()
        super().mousePressEvent(a0)

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:
        self.set_level(0.0)
        self._on_level(self._index, 0.0)
        if a0 is not None:
            a0.accept()

    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hover = True
        self._update_text()
        super().enterEvent(event)

    def leaveEvent(self, a0: QEvent | None) -> None:
        self._hover = False
        self._editing = False
        self._update_text()
        super().leaveEvent(a0)

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        # Рамка рисуется исключительно вокруг воспроизводимой частоты
        if self._current:
            paint_column_frame(self, accent=self._playing)
        super().paintEvent(a0)


class BandStrip(QWidget):
    """Лента полос эквалайзера со шкалой; помещается в прокручиваемую область."""

    def __init__(
        self,
        scale: LevelScale,
        icons: dict[str, QIcon],
        *,
        on_level: Callable[[int, float], None],
        on_play: Callable[[int], None],
        on_select: Callable[[int], None],
        on_activate: Callable[[int], None],
        on_navigate: Callable[[int], None],
        on_scroll: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scale = scale
        self._icons = icons
        self._on_level = on_level
        self._on_play = on_play
        self._on_select = on_select
        self._on_activate = on_activate
        self._on_navigate = on_navigate
        self._on_scroll = on_scroll
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 0, 8, 0)
        self._layout.setSpacing(RULER_SPACING)
        self._columns: list[BandColumn] = []
        self._master_focused = False
        self._ruler = LevelRuler(scale)
        self._layout.addWidget(self._ruler)
        self._layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

    def set_bands(self, bands: Sequence[Band], scale: LevelScale) -> None:
        """Перестроить колонки под новый набор полос."""
        self._clear()
        self._scale = scale
        self._ruler = LevelRuler(scale)
        self._layout.addWidget(self._ruler)
        for index, band in enumerate(bands):
            column = BandColumn(
                index,
                band,
                scale,
                self._icons,
                on_level=self._on_level,
                on_play=self._on_play,
                on_select=self._on_select,
                on_activate=self._on_activate,
                on_navigate=self._on_navigate,
                on_scroll=self._on_scroll,
            )
            column.set_master_focused(self._master_focused)
            self._columns.append(column)
            self._layout.addWidget(column)
        if self._columns:
            self._ruler.set_slider(self._columns[0].slider)
        self._layout.addStretch(1)

    def set_current(self, index: int, playing: bool) -> None:
        for number, column in enumerate(self._columns):
            column.set_current(number == index, playing and number == index)
            column.set_master_focused(self._master_focused)

    def set_master_focused(self, focused: bool) -> None:
        if self._master_focused != focused:
            self._master_focused = focused
            for column in self._columns:
                column.set_master_focused(focused)

    def set_scale(self, scale: LevelScale) -> None:
        self._scale = scale
        self._ruler.set_scale(scale)
        for column in self._columns:
            column.set_scale(scale)

    def column(self, index: int) -> BandColumn:
        return self._columns[index]

    def _clear(self) -> None:
        self._columns.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
