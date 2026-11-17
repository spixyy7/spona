from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .icons import Glyph, glyph_pixmap
from .images import rounded
from .theme import BACKGROUND, MUTED
from .widgets import Toast, separator, tool_button

CORNER = 12


class ImageView(QWidget):
    double_clicked = Signal()
    navigate = Signal(int)
    context_requested = Signal(int, object)
    drag_requested = Signal(int)

    def __init__(self, parent=None, framed: bool = True, background: str = BACKGROUND):
        super().__init__(parent)
        self._framed = framed
        self._margin = 18 if framed else 0
        self._gap = 16 if framed else 10
        self._background = QColor(background)
        self._current: list[QPixmap] = []
        self._current_labels: list[str] = []
        self._previous: list[QPixmap] = []
        self._previous_labels: list[str] = []
        self._progress = 1.0
        self._flash = 0.0
        self._scaled_cache: dict[int, tuple[tuple[int, int], QPixmap]] = {}
        self._wheel_accumulator = 0
        self._press_position = QPointF()
        self._press_index = -1
        self._title = ""
        self._detail = ""
        self._placeholder_icon = glyph_pixmap(Glyph.PHONE, "#3b3f46", 54)
        self._label_font = QFont(self.font())
        self._label_font.setPixelSize(12)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(220, 220)
        self._fade = QVariantAnimation(self)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(260)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade)
        self._fade.finished.connect(self._on_fade_finished)
        self._flash_animation = QVariantAnimation(self)
        self._flash_animation.setStartValue(0.55)
        self._flash_animation.setEndValue(0.0)
        self._flash_animation.setDuration(420)
        self._flash_animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._flash_animation.valueChanged.connect(self._on_flash)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(140)
        self._settle.timeout.connect(self.update)

    def pixmaps(self) -> list[QPixmap]:
        return list(self._current)

    def labels(self) -> list[str]:
        return list(self._current_labels)

    def set_placeholder(self, title: str, detail: str) -> None:
        if (title, detail) == (self._title, self._detail):
            return
        self._title, self._detail = title, detail
        if not self._current:
            self.update()

    def set_images(self, pixmaps: list[QPixmap], labels: list[str] | None = None, animate: bool = True) -> None:
        labels = list(labels) if labels is not None else [""] * len(pixmaps)
        if [pixmap.cacheKey() for pixmap in pixmaps] == [pixmap.cacheKey() for pixmap in self._current]:
            if labels != self._current_labels:
                self._current_labels = labels
                self.update()
            return
        self._fade.stop()
        if animate:
            self._previous, self._previous_labels = self._current, self._current_labels
        else:
            self._previous, self._previous_labels = [], []
        self._current, self._current_labels = list(pixmaps), labels
        self._prune_cache()
        self._progress = 0.0 if animate else 1.0
        if animate:
            self._fade.start()
        self.update()

    def flash(self) -> None:
        self._flash_animation.stop()
        self._flash_animation.start()

    def _on_fade(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _on_fade_finished(self) -> None:
        self._progress = 1.0
        self._previous, self._previous_labels = [], []
        self._prune_cache()
        self.update()

    def _on_flash(self, value) -> None:
        self._flash = float(value)
        self.update()

    def _prune_cache(self) -> None:
        keep = {pixmap.cacheKey() for pixmap in self._current + self._previous}
        self._scaled_cache = {key: value for key, value in self._scaled_cache.items() if key in keep}

    def resizeEvent(self, event):
        self._settle.start()
        super().resizeEvent(event)

    def index_at(self, position: QPointF) -> int:
        labelled = len(self._current) > 1 and any(self._current_labels)
        for index, rect in enumerate(self._layout(self._current, labelled)):
            if rect.contains(position):
                return index
        return -1

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_index = -1
            self.double_clicked.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position()
            self._press_index = self.index_at(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_index >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position() - self._press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                index = self._press_index
                self._press_index = -1
                self.drag_requested.emit(index)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_index = -1
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        index = self.index_at(QPointF(event.pos()))
        if index >= 0:
            self.context_requested.emit(index, event.globalPos())

    def wheelEvent(self, event):
        self._wheel_accumulator += event.angleDelta().y()
        while abs(self._wheel_accumulator) >= 120:
            step = -1 if self._wheel_accumulator > 0 else 1
            self._wheel_accumulator += 120 * step
            self.navigate.emit(step)
        event.accept()

    def _layout(self, pixmaps: list[QPixmap], labelled: bool) -> list[QRectF]:
        if not pixmaps:
            return []
        label_space = 26 if labelled else 0
        area = QRectF(self.rect()).adjusted(self._margin, self._margin, -self._margin, -self._margin - label_space)
        if area.width() < 4 or area.height() < 4:
            return []
        aspects = [pixmap.width() / max(1, pixmap.height()) for pixmap in pixmaps]
        height = min(area.height(), (area.width() - self._gap * (len(pixmaps) - 1)) / sum(aspects))
        if height <= 1:
            return []
        widths = [aspect * height for aspect in aspects]
        total = sum(widths) + self._gap * (len(widths) - 1)
        ratio = self.devicePixelRatioF()
        x = area.center().x() - total / 2
        y = area.center().y() - height / 2
        rects = []
        for width in widths:
            rects.append(
                QRectF(
                    round(x * ratio) / ratio,
                    round(y * ratio) / ratio,
                    round(width * ratio) / ratio,
                    round(height * ratio) / ratio,
                )
            )
            x += width + self._gap
        return rects

    def _scaled(self, pixmap: QPixmap, rect: QRectF) -> QPixmap:
        ratio = self.devicePixelRatioF()
        size = (max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio)))
        entry = self._scaled_cache.get(pixmap.cacheKey())
        if entry is not None and entry[0] == size:
            return entry[1]
        scaled = pixmap.scaled(
            size[0],
            size[1],
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._framed:
            scaled = rounded(scaled, CORNER * ratio)
        scaled.setDevicePixelRatio(ratio)
        self._scaled_cache[pixmap.cacheKey()] = (size, scaled)
        return scaled

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._background)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        if self._previous and self._progress < 1.0:
            self._paint_group(painter, self._previous, self._previous_labels, 1.0 - self._progress, 1.0, False)
        if self._current:
            scale = 0.97 + 0.03 * self._progress
            self._paint_group(painter, self._current, self._current_labels, self._progress, scale, True)
        else:
            painter.setOpacity(self._progress)
            self._paint_placeholder(painter)
        painter.end()

    def _paint_group(self, painter, pixmaps, labels, opacity, scale, is_current) -> None:
        if opacity <= 0.0:
            return
        labelled = len(pixmaps) > 1 and any(labels)
        rects = self._layout(pixmaps, labelled)
        if not rects:
            return
        painter.save()
        painter.setOpacity(opacity)
        if scale != 1.0:
            center = rects[0].united(rects[-1]).center()
            painter.translate(center.x(), center.y())
            painter.scale(scale, scale)
            painter.translate(-center.x(), -center.y())
        radius = CORNER if self._framed else 0
        for index, (pixmap, rect) in enumerate(zip(pixmaps, rects)):
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            if self._settle.isActive():
                painter.save()
                if self._framed:
                    painter.setClipPath(path)
                painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
                painter.restore()
            else:
                painter.drawPixmap(rect.topLeft(), self._scaled(pixmap, rect))
            if self._framed:
                painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            if is_current and self._flash > 0.0:
                painter.fillPath(path, QColor(255, 255, 255, round(255 * self._flash)))
            if labelled and index < len(labels) and labels[index]:
                painter.setFont(self._label_font)
                painter.setPen(QColor(MUTED))
                text = painter.fontMetrics().elidedText(labels[index], Qt.TextElideMode.ElideMiddle, int(rect.width()))
                painter.drawText(QRectF(rect.left(), rect.bottom() + 6, rect.width(), 18), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_placeholder(self, painter) -> None:
        if not self._title:
            return
        center = QRectF(self.rect()).center()
        painter.drawPixmap(QPointF(center.x() - 27, center.y() - 78), self._placeholder_icon)
        title_font = QFont(self.font())
        title_font.setPixelSize(16)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#c9ccd2"))
        painter.drawText(QRectF(0, center.y() - 12, self.width(), 26), Qt.AlignmentFlag.AlignCenter, self._title)
        detail_font = QFont(self.font())
        detail_font.setPixelSize(13)
        painter.setFont(detail_font)
        painter.setPen(QColor(MUTED))
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        painter.drawText(QRectF(32, center.y() + 18, self.width() - 64, 60), flags, self._detail)


class FullscreenViewer(QWidget):
    closed = Signal()
    navigate = Signal(int)
    capture_requested = Signal()
    delete_requested = Signal()
    copy_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Spona")
        self.view = ImageView(self, framed=False, background="#050506")
        self.view.setMouseTracking(True)
        self.view.installEventFilter(self)
        self.view.double_clicked.connect(self.close_animated)
        self.view.navigate.connect(self.navigate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.toast = Toast(self.view, edge="top")
        self._overlay = QFrame(self.view)
        self._overlay.setObjectName("overlay")
        bar = QHBoxLayout(self._overlay)
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(2)
        self._counter = QLabel()
        self._counter.setObjectName("counter")
        bar.addWidget(tool_button(Glyph.PREVIOUS, "Previous (←)", lambda: self.navigate.emit(-1), icon_size=16))
        bar.addWidget(self._counter)
        bar.addWidget(tool_button(Glyph.NEXT, "Next (→)", lambda: self.navigate.emit(1), icon_size=16))
        bar.addWidget(separator())
        bar.addWidget(tool_button(Glyph.CAMERA, "Capture (F5 or Space)", self.capture_requested.emit))
        bar.addWidget(tool_button(Glyph.COPY, "Copy (Ctrl+C)", self.copy_requested.emit))
        bar.addWidget(tool_button(Glyph.DELETE, "Delete (Delete)", self.delete_requested.emit))
        bar.addWidget(separator())
        bar.addWidget(tool_button(Glyph.EXIT_FULLSCREEN, "Exit fullscreen (Esc)", self.close_animated))
        self._overlay_effect = QGraphicsOpacityEffect(self._overlay)
        self._overlay_effect.setOpacity(1.0)
        self._overlay.setGraphicsEffect(self._overlay_effect)
        self._overlay_animation = QPropertyAnimation(self._overlay_effect, b"opacity", self)
        self._overlay_animation.setDuration(220)
        self._overlay_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(2400)
        self._idle.timeout.connect(self._hide_chrome)
        self._window_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._window_animation.setDuration(170)
        self._window_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._window_animation.finished.connect(self._after_window_animation)
        self._closing = False

    def set_counter(self, position: int, count: int, selected: int) -> None:
        if selected > 1:
            self._counter.setText(f"{selected} selected")
        else:
            self._counter.setText(f"{position} / {count}" if count else "0 / 0")
        self._overlay.adjustSize()
        self._place_overlay()

    def open_on(self, screen) -> None:
        self.setWindowOpacity(0.0)
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._animate_window(1.0)
        self._show_chrome()

    def close_animated(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._animate_window(0.0)

    def _animate_window(self, target: float) -> None:
        self._window_animation.stop()
        self._window_animation.setStartValue(self.windowOpacity())
        self._window_animation.setEndValue(target)
        self._window_animation.start()

    def _after_window_animation(self) -> None:
        if self._closing:
            self.close()

    def _show_chrome(self) -> None:
        self.view.unsetCursor()
        if self._overlay_effect.opacity() < 1.0:
            self._overlay_animation.stop()
            self._overlay_animation.setStartValue(self._overlay_effect.opacity())
            self._overlay_animation.setEndValue(1.0)
            self._overlay_animation.start()
        self._idle.start()

    def _hide_chrome(self) -> None:
        if self._overlay.underMouse():
            self._idle.start()
            return
        self._overlay_animation.stop()
        self._overlay_animation.setStartValue(self._overlay_effect.opacity())
        self._overlay_animation.setEndValue(0.0)
        self._overlay_animation.start()
        self.view.setCursor(Qt.CursorShape.BlankCursor)

    def _place_overlay(self) -> None:
        self._overlay.adjustSize()
        self._overlay.move((self.view.width() - self._overlay.width()) // 2, self.view.height() - self._overlay.height() - 28)

    def eventFilter(self, watched, event):
        if watched is self.view:
            if event.type() == QEvent.Type.MouseMove:
                self._show_chrome()
            elif event.type() == QEvent.Type.Resize:
                self._place_overlay()
        return False

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_F11, Qt.Key.Key_F, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.close_animated()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.navigate.emit(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self.navigate.emit(1)
        elif key in (Qt.Key.Key_F5, Qt.Key.Key_Space):
            self.capture_requested.emit()
        elif key == Qt.Key.Key_Delete:
            self.delete_requested.emit()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy_requested.emit()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
