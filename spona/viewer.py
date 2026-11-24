import math

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .annotations import (
    MARKER_COLORS,
    AnnotationStore,
    Brush,
    Stroke,
    paint_stroke,
    paint_strokes,
    smooth_path,
    stroke_hit,
)
from .icons import Glyph, glyph_pixmap
from .images import rounded
from .keybinds import MAIN, Keybinds
from .marker import MarkerPanel
from .theme import BACKGROUND, MUTED
from .widgets import Toast, separator, tool_button

CORNER = 12


class ImageView(QWidget):
    double_clicked = Signal()
    navigate = Signal(int)
    context_requested = Signal(int, object)
    drag_requested = Signal(int)
    annotation_changed = Signal(str)
    brush_resize_requested = Signal(int)
    draw_blocked = Signal()

    def __init__(self, parent=None, framed: bool = True, background: str = BACKGROUND):
        super().__init__(parent)
        self.store: AnnotationStore | None = None
        self._framed = framed
        self._margin = 18 if framed else 0
        self._gap = 16 if framed else 10
        self._background = QColor(background)
        self._current: list[QPixmap] = []
        self._current_labels: list[str] = []
        self._current_keys: list[str] = []
        self._previous: list[QPixmap] = []
        self._previous_labels: list[str] = []
        self._previous_keys: list[str] = []
        self._progress = 1.0
        self._flash = 0.0
        self._scaled_cache: dict[int, tuple[tuple[int, int], QPixmap]] = {}
        self._wheel_accumulator = 0
        self._press_position = QPointF()
        self._press_index = -1
        self._brush: Brush | None = None
        self._hover: QPointF | None = None
        self._live_points: list[QPointF] | None = None
        self._live_width = 1.0
        self._erase_before: list[Stroke] | None = None
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

    def keys(self) -> list[str]:
        return list(self._current_keys)

    def set_placeholder(self, title: str, detail: str) -> None:
        if (title, detail) == (self._title, self._detail):
            return
        self._title, self._detail = title, detail
        if not self._current:
            self.update()

    def set_images(
        self,
        pixmaps: list[QPixmap],
        labels: list[str] | None = None,
        keys: list[str] | None = None,
        animate: bool = True,
    ) -> None:
        labels = list(labels) if labels is not None else [""] * len(pixmaps)
        keys = list(keys) if keys is not None else [""] * len(pixmaps)
        same_images = [pixmap.cacheKey() for pixmap in pixmaps] == [pixmap.cacheKey() for pixmap in self._current]
        if same_images and keys == self._current_keys:
            if labels != self._current_labels:
                self._current_labels = labels
                self.update()
            return
        self._fade.stop()
        self._live_points = None
        self._erase_before = None
        if animate:
            self._previous, self._previous_labels, self._previous_keys = (
                self._current,
                self._current_labels,
                self._current_keys,
            )
        else:
            self._previous, self._previous_labels, self._previous_keys = [], [], []
        self._current, self._current_labels, self._current_keys = list(pixmaps), labels, keys
        self._prune_cache()
        self._progress = 0.0 if animate else 1.0
        if animate:
            self._fade.start()
        self.update()

    def flash(self) -> None:
        self._flash_animation.stop()
        self._flash_animation.start()

    def set_brush(self, brush: Brush | None) -> None:
        self._brush = brush
        self._live_points = None
        self._erase_before = None
        if brush is None:
            self._hover = None
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.update()

    def drawing(self) -> bool:
        return self._brush is not None

    def current_key(self) -> str | None:
        target = self._draw_target()
        return target[0] if target is not None else None

    def _draw_target(self):
        if self.store is None or len(self._current) != 1 or not self._current_keys or not self._current_keys[0]:
            return None
        rects = self._layout(self._current, False)
        if not rects:
            return None
        pixmap = self._current[0]
        rect = rects[0]
        return self._current_keys[0], pixmap, rect, rect.width() / max(1, pixmap.width())

    @staticmethod
    def _to_image(position: QPointF, rect: QRectF, scale: float) -> QPointF:
        return QPointF((position.x() - rect.left()) / scale, (position.y() - rect.top()) / scale)

    def _on_fade(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _on_fade_finished(self) -> None:
        self._progress = 1.0
        self._previous, self._previous_labels, self._previous_keys = [], [], []
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
        if self._brush is not None:
            self.mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_index = -1
            self.double_clicked.emit()

    def mousePressEvent(self, event):
        if self._brush is not None and event.button() == Qt.MouseButton.LeftButton:
            self._begin_draw(event.position())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position()
            self._press_index = self.index_at(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._brush is not None:
            self._hover = event.position()
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._continue_draw(event.position())
            self.update()
            return
        if self._press_index >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position() - self._press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                index = self._press_index
                self._press_index = -1
                self.drag_requested.emit(index)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._brush is not None and event.button() == Qt.MouseButton.LeftButton:
            self._finish_draw()
            return
        self._press_index = -1
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        if self._brush is not None:
            self.update()
        super().leaveEvent(event)

    def _begin_draw(self, position: QPointF) -> None:
        target = self._draw_target()
        if target is None:
            self.draw_blocked.emit()
            return
        key, _pixmap, rect, scale = target
        point = self._to_image(position, rect, scale)
        if self._brush.tool == "eraser":
            self._erase_before = list(self.store.strokes(key))
            self._erase(key, point, self._brush.size / 2 / scale)
        else:
            self._live_points = [point]
            self._live_width = max(0.5, self._brush.size / scale)
        self.update()

    def _continue_draw(self, position: QPointF) -> None:
        target = self._draw_target()
        if target is None:
            return
        key, _pixmap, rect, scale = target
        point = self._to_image(position, rect, scale)
        if self._live_points is not None:
            last = self._live_points[-1]
            if math.hypot(point.x() - last.x(), point.y() - last.y()) * scale >= 1.2:
                self._live_points.append(point)
        elif self._erase_before is not None:
            self._erase(key, point, self._brush.size / 2 / scale)

    def _finish_draw(self) -> None:
        target = self._draw_target()
        if target is not None:
            key = target[0]
            if self._live_points:
                stroke = Stroke(self._live_points, self._brush.color, self._live_width, self._brush.opacity / 100)
                self.store.add(key, stroke)
                self.annotation_changed.emit(key)
            elif self._erase_before is not None and len(self._erase_before) != len(self.store.strokes(key)):
                self.store.commit(key, self.store.strokes(key), previous=self._erase_before)
                self.annotation_changed.emit(key)
        self._live_points = None
        self._erase_before = None
        self.update()

    def _erase(self, key: str, point: QPointF, radius: float) -> None:
        strokes = self.store.strokes(key)
        remaining = [stroke for stroke in strokes if not stroke_hit(stroke, point, radius)]
        if len(remaining) != len(strokes):
            self.store.set_silently(key, remaining)
            self.annotation_changed.emit(key)

    def contextMenuEvent(self, event):
        index = self.index_at(QPointF(event.pos()))
        if index >= 0:
            self.context_requested.emit(index, event.globalPos())

    def wheelEvent(self, event):
        self._wheel_accumulator += event.angleDelta().y()
        while abs(self._wheel_accumulator) >= 120:
            step = -1 if self._wheel_accumulator > 0 else 1
            self._wheel_accumulator += 120 * step
            if self._brush is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.brush_resize_requested.emit(-step)
            else:
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
            self._paint_group(
                painter, self._previous, self._previous_labels, self._previous_keys, 1.0 - self._progress, 1.0, False
            )
        if self._current:
            scale = 0.97 + 0.03 * self._progress
            self._paint_group(
                painter, self._current, self._current_labels, self._current_keys, self._progress, scale, True
            )
        else:
            painter.setOpacity(self._progress)
            self._paint_placeholder(painter)
        if self._brush is not None and self._hover is not None:
            painter.setOpacity(1.0)
            self._paint_brush_cursor(painter)
        painter.end()

    def _paint_group(self, painter, pixmaps, labels, keys, opacity, scale, is_current) -> None:
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
            key = keys[index] if index < len(keys) else ""
            self._paint_marks(painter, key, pixmap, rect, path, is_current and index == 0)
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

    def _paint_marks(self, painter, key, pixmap, rect, path, can_be_live) -> None:
        live = can_be_live and bool(self._live_points)
        if self.store is None or not key or not (self.store.has(key) or live):
            return
        scale = rect.width() / max(1, pixmap.width())
        painter.save()
        if self._framed:
            painter.setClipPath(path)
        else:
            painter.setClipRect(rect)
        painter.translate(rect.left(), rect.top())
        painter.scale(scale, scale)
        paint_strokes(painter, self.store.strokes(key))
        if live:
            paint_stroke(
                painter,
                smooth_path(self._live_points),
                self._brush.color,
                self._live_width,
                self._brush.opacity / 100,
            )
        painter.restore()

    def _paint_brush_cursor(self, painter) -> None:
        center = self._hover
        radius = max(1.5, self._brush.size / 2)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 170), 3))
        painter.drawEllipse(center, radius, radius)
        if self._brush.tool == "eraser":
            painter.setPen(QPen(QColor("#ffffff"), 1.4, Qt.PenStyle.DashLine))
        else:
            painter.setPen(QPen(QColor(self._brush.color), 1.6))
        painter.drawEllipse(center, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(center, 1.2, 1.2)
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


class FadingPanel:
    def __init__(self, widget: QWidget, owner, duration: int = 200):
        self.widget = widget
        self.effect = QGraphicsOpacityEffect(widget)
        self.effect.setOpacity(1.0)
        widget.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect, b"opacity", owner)
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._after)

    def fade(self, target: float) -> None:
        if target > 0:
            self.widget.show()
            self.widget.raise_()
        self.animation.stop()
        self.animation.setStartValue(self.effect.opacity())
        self.animation.setEndValue(target)
        self.animation.start()

    def _after(self) -> None:
        if self.effect.opacity() <= 0.01 and self.animation.endValue() == 0.0:
            self.widget.hide()


class FullscreenViewer(QWidget):
    closed = Signal()
    navigate = Signal(int)
    capture_requested = Signal()
    delete_requested = Signal()
    copy_requested = Signal()
    undo_requested = Signal()
    action_requested = Signal(str)
    save_marks_requested = Signal()
    brush_changed = Signal(dict)
    annotation_changed = Signal(str)

    def __init__(self, brush: Brush, store: AnnotationStore, keys: Keybinds, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Spona")
        self._brush = brush
        self._keys = keys
        self.view = ImageView(self, framed=False, background="#050506")
        self.view.store = store
        self.view.setMouseTracking(True)
        self.view.installEventFilter(self)
        self.view.double_clicked.connect(self.close_animated)
        self.view.navigate.connect(self.navigate)
        self.view.annotation_changed.connect(self._on_annotation_changed)
        self.view.brush_resize_requested.connect(lambda steps: self._marker.resize_brush(steps))
        self.view.draw_blocked.connect(self._on_draw_blocked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.toast = Toast(self.view, edge="top", margin=84)

        self._overlay = QFrame(self.view)
        self._overlay.setObjectName("overlay")
        bar = QHBoxLayout(self._overlay)
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(2)
        self._counter = QLabel()
        self._counter.setObjectName("counter")
        self._marker_button = tool_button(
            Glyph.PEN, f"Marker: draw on the image{keys.hint('marker')}", self.toggle_marker, text="Marker"
        )
        self._marker_button.setCheckable(True)
        bar.addWidget(
            tool_button(
                Glyph.PREVIOUS, f"Previous{keys.hint('previous')}", lambda: self.navigate.emit(-1), icon_size=16
            )
        )
        bar.addWidget(self._counter)
        bar.addWidget(
            tool_button(Glyph.NEXT, f"Next{keys.hint('next')}", lambda: self.navigate.emit(1), icon_size=16)
        )
        bar.addWidget(separator())
        bar.addWidget(self._marker_button)
        bar.addWidget(
            tool_button(Glyph.CAMERA, f"Capture{keys.hint('capture', every=True)}", self.capture_requested.emit)
        )
        bar.addWidget(tool_button(Glyph.COPY, f"Copy{keys.hint('copy')}", self.copy_requested.emit))
        bar.addWidget(tool_button(Glyph.DELETE, f"Delete image{keys.hint('delete')}", self.delete_requested.emit))
        bar.addWidget(tool_button(Glyph.UNDO, f"Undo{keys.hint('undo')}", self.undo_requested.emit))
        bar.addWidget(separator())
        bar.addWidget(tool_button(Glyph.EXIT_FULLSCREEN, "Exit fullscreen (Esc)", self.close_animated))
        self._overlay_fade = FadingPanel(self._overlay, self, 220)

        self._marker = MarkerPanel(brush, keys, self.view)
        self._marker.changed.connect(self._on_brush_changed)
        self._marker.undo_requested.connect(self.undo)
        self._marker.redo_requested.connect(self.redo)
        self._marker.clear_requested.connect(self.clear_marks)
        self._marker.copy_requested.connect(self.copy_requested.emit)
        self._marker.save_requested.connect(self.save_marks_requested.emit)
        self._marker.done_requested.connect(self.toggle_marker)
        self._marker_fade = FadingPanel(self._marker, self, 200)
        self._marker.hide()

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(2400)
        self._idle.timeout.connect(self._hide_chrome)
        self._window_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._window_animation.setDuration(170)
        self._window_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._window_animation.finished.connect(self._after_window_animation)
        self._closing = False

    def marker_active(self) -> bool:
        return self.view.drawing()

    def set_counter(self, position: int, count: int, selected: int) -> None:
        if selected > 1:
            self._counter.setText(f"{selected} selected")
        else:
            self._counter.setText(f"{position} / {count}" if count else "0 / 0")
        self._place_panels()
        self._refresh_history()

    def open_on(self, screen, marker: bool = False) -> None:
        self.setWindowOpacity(0.0)
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._animate_window(1.0)
        self._show_chrome()
        if marker:
            self.toggle_marker()

    def toggle_marker(self) -> None:
        if self.view.drawing():
            self.view.set_brush(None)
            self._marker_fade.fade(0.0)
            self._marker_button.setChecked(False)
            self._show_chrome()
            return
        self.view.set_brush(self._brush)
        self._marker.sync()
        self._place_panels()
        self._marker_fade.fade(1.0)
        self._marker_button.setChecked(True)
        self._refresh_history()
        if self.view.current_key() is None:
            self._on_draw_blocked()

    def undo(self) -> None:
        key = self.view.current_key()
        if key and self.view.store.undo(key):
            self._on_annotation_changed(key)
        else:
            self.toast.show_message("No marks to undo here")

    def redo(self) -> None:
        key = self.view.current_key()
        if key and self.view.store.redo(key):
            self._on_annotation_changed(key)

    def clear_marks(self) -> None:
        key = self.view.current_key()
        if key and self.view.store.clear(key):
            self._on_annotation_changed(key)
            back = self._keys.text("undo") or "Undo"
            self.toast.show_message(f"Marks cleared  ·  {back} brings them back")

    def refresh(self) -> None:
        self.view.update()
        self._refresh_history()

    def _on_annotation_changed(self, key: str) -> None:
        self.view.update()
        self._refresh_history()
        self.annotation_changed.emit(key)

    def _refresh_history(self) -> None:
        key = self.view.current_key()
        store = self.view.store
        if key is None or store is None:
            self._marker.set_history(False, False, False)
            return
        self._marker.set_history(store.can_undo(key), store.can_redo(key), store.has(key))

    def _on_brush_changed(self) -> None:
        self.view.update()
        self.brush_changed.emit(self._brush.to_dict())

    def _on_draw_blocked(self) -> None:
        self.toast.show_message("Select a single image to draw on it", "error")

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
        if not self.view.drawing():
            self.view.unsetCursor()
        if self._overlay_fade.effect.opacity() < 1.0 or not self._overlay.isVisible():
            self._overlay_fade.fade(1.0)
        self._idle.start()

    def _hide_chrome(self) -> None:
        if self._overlay.underMouse() or self._marker.underMouse():
            self._idle.start()
            return
        self._overlay_fade.fade(0.0)
        if not self.view.drawing():
            self.view.setCursor(Qt.CursorShape.BlankCursor)

    def _place_panels(self) -> None:
        self._overlay.adjustSize()
        self._overlay.move((self.view.width() - self._overlay.width()) // 2, self.view.height() - self._overlay.height() - 28)
        self._marker.adjustSize()
        self._marker.move((self.view.width() - self._marker.width()) // 2, 24)

    def eventFilter(self, watched, event):
        if watched is self.view:
            if event.type() == QEvent.Type.MouseMove:
                self._show_chrome()
            elif event.type() == QEvent.Type.Resize:
                self._place_panels()
        return False

    def keyPressEvent(self, event):
        drawing = self.view.drawing()
        if event.key() == Qt.Key.Key_Escape:
            if drawing:
                self.toggle_marker()
            else:
                self.close_animated()
            return
        action = self._keys.match_drawing(event) if drawing else self._keys.match(event, MAIN)
        if action is None:
            super().keyPressEvent(event)
            return
        self._run(action)

    def _run(self, action: str) -> None:
        if action.startswith("color"):
            self._marker.set_color(MARKER_COLORS[int(action[-1]) - 1])
            return
        handlers = {
            "marker": self.toggle_marker,
            "undo": self.undo if self.view.drawing() else self.undo_requested.emit,
            "redo": self.redo,
            "save_marks": self.save_marks_requested.emit,
            "clear_marks": self.clear_marks,
            "pen": lambda: self._marker.set_tool("pen"),
            "eraser": lambda: self._marker.set_tool("eraser"),
            "smaller": lambda: self._marker.resize_brush(-1),
            "bigger": lambda: self._marker.resize_brush(1),
            "fullscreen": self.close_animated,
            "previous": lambda: self.navigate.emit(-1),
            "next": lambda: self.navigate.emit(1),
            "capture": self.capture_requested.emit,
            "delete": self.delete_requested.emit,
            "copy": self.copy_requested.emit,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()
        else:
            self.action_requested.emit(action)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
