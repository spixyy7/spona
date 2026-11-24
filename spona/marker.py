from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

from .annotations import MARKER_COLORS, Brush
from .icons import Glyph
from .keybinds import Keybinds
from .widgets import separator, tool_button

COLOR_NAMES = ("Red", "Yellow", "Green", "Blue", "White", "Black")


class ColorSwatch(QToolButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.isChecked():
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(outer)
        elif self.underMouse():
            painter.setPen(QPen(QColor("#6b6f78"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(outer)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(QRectF(self.rect()).adjusted(5, 5, -5, -5))
        painter.end()


class BrushPreview(QWidget):
    def __init__(self, brush: Brush, parent=None):
        super().__init__(parent)
        self._brush = brush
        self.setFixedSize(34, 34)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0f1012"))
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        center = QRectF(self.rect()).center()
        if self._brush.tool == "eraser":
            radius = min(14.0, max(2.0, self._brush.size / 2))
            painter.setPen(QPen(QColor("#e3e5e8"), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            radius = min(14.0, max(1.5, self._brush.size / 2))
            color = QColor(self._brush.color)
            color.setAlphaF(self._brush.opacity / 100)
            painter.setBrush(color)
        painter.drawEllipse(center, radius, radius)
        painter.end()


class MarkerPanel(QFrame):
    changed = Signal()
    undo_requested = Signal()
    redo_requested = Signal()
    clear_requested = Signal()
    copy_requested = Signal()
    save_requested = Signal()
    done_requested = Signal()

    def __init__(self, brush: Brush, keys: Keybinds, parent=None):
        super().__init__(parent)
        self.setObjectName("overlay")
        self._brush = brush
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        self._pen = tool_button(Glyph.PEN, f"Marker{keys.hint('pen')}", lambda: self.set_tool("pen"))
        self._eraser = tool_button(Glyph.ERASER, f"Eraser{keys.hint('eraser')}", lambda: self.set_tool("eraser"))
        tools = QButtonGroup(self)
        for button in (self._pen, self._eraser):
            button.setCheckable(True)
            tools.addButton(button)
            layout.addWidget(button)
        layout.addWidget(separator())

        self._swatches = QButtonGroup(self)
        for index, (color, name) in enumerate(zip(MARKER_COLORS, COLOR_NAMES), start=1):
            swatch = ColorSwatch(color)
            swatch.setToolTip(f"{name}{keys.hint(f'color{index}')}")
            swatch.clicked.connect(lambda _checked=False, value=color: self.set_color(value))
            self._swatches.addButton(swatch)
            layout.addWidget(swatch)
        layout.addWidget(separator())

        self._preview = BrushPreview(brush)
        layout.addWidget(self._preview)
        wheel = " or ".join(part for part in (keys.text("smaller"), keys.text("bigger"), "Ctrl+wheel") if part)
        self._size_slider, self._size_value = self._slider(layout, "Size", 1, 64, self._on_size, f"Size ({wheel})")
        self._opacity_slider, self._opacity_value = self._slider(layout, "Opacity", 5, 100, self._on_opacity, "Opacity")
        layout.addWidget(separator())

        self._undo = tool_button(Glyph.UNDO, f"Undo{keys.hint('undo')}", self.undo_requested.emit)
        self._redo = tool_button(Glyph.REDO, f"Redo{keys.hint('redo')}", self.redo_requested.emit)
        self._clear = tool_button(Glyph.CLEAR, f"Clear marks{keys.hint('clear_marks')}", self.clear_requested.emit)
        for button in (self._undo, self._redo, self._clear):
            layout.addWidget(button)
        layout.addWidget(separator())
        stop = " or ".join(part for part in (keys.text("marker"), "Esc") if part)
        layout.addWidget(tool_button(Glyph.COPY, f"Copy marked image{keys.hint('copy')}", self.copy_requested.emit))
        layout.addWidget(
            tool_button(Glyph.SAVE, f"Save as a new image{keys.hint('save_marks')}", self.save_requested.emit, text="Save")
        )
        layout.addWidget(separator())
        layout.addWidget(tool_button(Glyph.ACCEPT, f"Stop drawing ({stop})", self.done_requested.emit, text="Done"))
        self.sync()

    def _slider(self, layout, title, minimum, maximum, handler, tooltip):
        label = QLabel(title)
        label.setObjectName("counter")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setFixedWidth(104)
        slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider.setToolTip(tooltip)
        value = QLabel()
        value.setObjectName("counter")
        value.setMinimumWidth(50)
        slider.valueChanged.connect(handler)
        layout.addWidget(label)
        layout.addWidget(slider)
        layout.addWidget(value)
        return slider, value

    def sync(self) -> None:
        brush = self._brush
        self._pen.setChecked(brush.tool == "pen")
        self._eraser.setChecked(brush.tool == "eraser")
        for swatch in self._swatches.buttons():
            swatch.setChecked(swatch.color == brush.color)
        for slider, value in ((self._size_slider, brush.size), (self._opacity_slider, brush.opacity)):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self._size_value.setText(f"{brush.size} px")
        self._opacity_value.setText(f"{brush.opacity}%")
        self._preview.update()

    def set_history(self, can_undo: bool, can_redo: bool, has_marks: bool) -> None:
        self._undo.setEnabled(can_undo)
        self._redo.setEnabled(can_redo)
        self._clear.setEnabled(has_marks)

    def set_tool(self, tool: str) -> None:
        self._brush.tool = tool
        self._changed()

    def set_color(self, color: str) -> None:
        self._brush.color = color
        self._brush.tool = "pen"
        self._changed()

    def resize_brush(self, steps: int) -> None:
        size = self._brush.size
        step = 1 if size < 10 else 2 if size < 24 else 4
        self._brush.size = max(1, min(64, size + steps * step))
        self._changed()

    def _on_size(self, value: int) -> None:
        self._brush.size = value
        self._changed()

    def _on_opacity(self, value: int) -> None:
        self._brush.opacity = value
        self._changed()

    def _changed(self) -> None:
        self.sync()
        self.changed.emit()
