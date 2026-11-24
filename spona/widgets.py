import html

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtWidgets import QAbstractButton, QFrame, QGraphicsOpacityEffect, QLabel, QSizePolicy, QToolButton, QWidget

from .icons import glyph_icon
from .theme import ACCENT


def tool_button(glyph: str, tooltip: str, slot, text: str | None = None, icon_size: int = 18) -> QToolButton:
    button = QToolButton()
    button.setIcon(glyph_icon(glyph, size=icon_size))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoRaise(True)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if text:
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.clicked.connect(slot)
    return button


class ResponsiveBar(QObject):
    def __init__(self, bar: QWidget, stages: list[list[QAbstractButton]], reserves: dict | None = None):
        super().__init__(bar)
        self._bar = bar
        self._stages = [list(stage) for stage in stages]
        self._reserves = reserves or {}
        self._texts: dict[QAbstractButton, str] = {}
        self._calibration: dict[QAbstractButton, tuple[int, int]] = {}
        self._level = 0
        self._scheduled = False
        for button in self._buttons():
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bar.installEventFilter(self)

    def set_text(self, button: QAbstractButton, text: str) -> None:
        self._texts[button] = text
        if isinstance(button, QToolButton) or button not in self._collapsed(self._level):
            button.setText(text)
        self._schedule()

    def _buttons(self) -> list[QAbstractButton]:
        return [button for stage in self._stages for button in stage]

    def _collapsed(self, level: int) -> set:
        return {button for stage in self._stages[:level] for button in stage}

    def _text(self, button: QAbstractButton) -> str:
        if isinstance(button, QToolButton):
            return button.text()
        return self._texts.get(button, button.text())

    def _show(self, button: QAbstractButton, collapsed: bool) -> None:
        if isinstance(button, QToolButton):
            style = Qt.ToolButtonStyle.ToolButtonIconOnly if collapsed else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            button.setToolButtonStyle(style)
        else:
            button.setText("" if collapsed else self._text(button))

    def _apply(self, level: int) -> None:
        self._level = level
        collapsed = self._collapsed(level)
        for button in self._buttons():
            self._show(button, button in collapsed)

    def _calibrate(self) -> None:
        for button in self._buttons():
            button.ensurePolished()
            if not isinstance(button, QToolButton):
                self._texts.setdefault(button, button.text())
            text = self._text(button)
            self._show(button, True)
            icon_width = button.sizeHint().width()
            self._show(button, False)
            full_width = button.sizeHint().width()
            extra = full_width - icon_width - button.fontMetrics().horizontalAdvance(text)
            self._calibration[button] = (icon_width, extra)
        self._apply(self._level)

    def _width(self, widget: QWidget, collapsed: set, level: int) -> int:
        calibration = self._calibration.get(widget)
        if calibration is not None:
            icon_width, extra = calibration
            if widget in collapsed:
                return icon_width
            return icon_width + extra + widget.fontMetrics().horizontalAdvance(self._text(widget))
        reserve = self._reserves.get(widget)
        if reserve is not None:
            return reserve[min(level, len(reserve) - 1)]
        if hasattr(widget, "full_width"):
            return widget.full_width()
        if widget.layout() is not None:
            return self._measure(widget.layout(), collapsed, level)
        return widget.sizeHint().width()

    def _measure(self, layout, collapsed: set, level: int) -> int:
        widths = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                if not widget.isHidden():
                    widths.append(self._width(widget, collapsed, level))
            elif item.layout() is not None:
                widths.append(self._measure(item.layout(), collapsed, level))
            elif item.spacerItem() is not None:
                widths.append(item.spacerItem().sizeHint().width())
        margins = layout.contentsMargins()
        return margins.left() + margins.right() + sum(widths) + layout.spacing() * max(0, len(widths) - 1)

    def fit(self) -> None:
        self._scheduled = False
        if not self._bar.isVisible() or self._bar.layout() is None:
            return
        if not self._calibration:
            self._calibrate()
        available = self._bar.width()
        chosen = len(self._stages)
        for level in range(len(self._stages) + 1):
            if self._measure(self._bar.layout(), self._collapsed(level), level) <= available:
                chosen = level
                break
        if chosen != self._level:
            self._apply(chosen)

    def _schedule(self) -> None:
        if not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self.fit)

    def eventFilter(self, watched, event):
        if watched is self._bar and event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self._schedule()
        return False


def separator(height: int = 22) -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedSize(1, height)
    return line


class ElidedLabel(QLabel):
    def __init__(self, parent=None, mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle):
        super().__init__(parent)
        self._full_text = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(self._full_text, self._mode, max(0, self.width() - 2)))


class Toast(QLabel):
    def __init__(self, host, edge: str = "bottom", margin: int = 24):
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edge = edge
        self._margin = margin
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._after_animation)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fade_to(0.0))
        self._callback = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.linkActivated.connect(self._run_action)
        host.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, kind: str = "info", duration: int = 2000, action=None) -> None:
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setMaximumWidth(max(160, self.parentWidget().width() - 40))
        room = self.maximumWidth() - 40
        self._callback = action[1] if action is not None else None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, action is None)
        if action is None:
            self.setTextFormat(Qt.TextFormat.PlainText)
            self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room))
        else:
            label = action[0]
            room -= round(self.fontMetrics().horizontalAdvance(label) * 1.15) + 24
            plain = html.escape(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(60, room)))
            self.setTextFormat(Qt.TextFormat.RichText)
            self.setText(
                plain.replace("  ", "&nbsp; ")
                + f'&nbsp; &nbsp; <a href="action" style="color:{ACCENT}; text-decoration:none; font-weight:600">'
                + html.escape(label).replace(" ", "&nbsp;")
                + "</a>"
            )
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._fade_to(1.0)
        self._timer.start(duration)

    def _run_action(self, _link: str = "") -> None:
        callback, self._callback = self._callback, None
        if callback is not None:
            self._timer.stop()
            self._fade_to(0.0)
            QTimer.singleShot(0, callback)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._callback is not None:
            self._timer.stop()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._callback is not None and self.isVisible():
            self._timer.start(1500)

    def _fade_to(self, value: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._effect.opacity())
        self._animation.setEndValue(value)
        self._animation.start()

    def _after_animation(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.hide()

    def _place(self) -> None:
        host = self.parentWidget()
        x = (host.width() - self.width()) // 2
        y = self._margin if self._edge == "top" else host.height() - self.height() - self._margin
        self.move(x, y)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
