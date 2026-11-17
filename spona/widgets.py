from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QSizePolicy, QToolButton

from .icons import glyph_icon


def tool_button(glyph: str, tooltip: str, slot, text: str | None = None, icon_size: int = 18) -> QToolButton:
    button = QToolButton()
    button.setIcon(glyph_icon(glyph, size=icon_size))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoRaise(True)
    if text:
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.clicked.connect(slot)
    return button


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
        host.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, kind: str = "info", duration: int = 2000) -> None:
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setMaximumWidth(max(160, self.parentWidget().width() - 40))
        self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.maximumWidth() - 40))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._fade_to(1.0)
        self._timer.start(duration)

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
