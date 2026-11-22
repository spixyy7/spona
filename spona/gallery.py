import os
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QFileSystemWatcher,
    QItemSelectionModel,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from .images import IMAGE_SUFFIXES, ImageLoader, file_mime, rounded
from .theme import ACCENT, ACCENT_DARK, ACCENT_TINT, MUTED, SURFACE_HIGH

PATH_ROLE = int(Qt.ItemDataRole.UserRole)
THUMB_ROLE = int(Qt.ItemDataRole.UserRole) + 1
THUMB_SIZE = QSize(56, 102)
CELL_SIZE = QSize(74, 120)
CHECK_RADIUS = 8.5


def scan_images(folder: Path) -> list[str]:
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if os.path.splitext(entry.name)[1].lower() not in IMAGE_SUFFIXES:
                    continue
                try:
                    if entry.is_file():
                        found.append((entry.stat().st_mtime, os.path.normpath(entry.path)))
                except OSError:
                    continue
    except OSError:
        return []
    found.sort(reverse=True)
    return [path for _, path in found]


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, gallery: "Gallery"):
        super().__init__(gallery)
        self._gallery = gallery

    def sizeHint(self, option, index):
        return CELL_SIZE

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        cell = QRectF(option.rect).adjusted(3, 3, -3, -3)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.setBrush(QColor(ACCENT_TINT))
            painter.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 10, 10)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SURFACE_HIGH))
            painter.drawRoundedRect(cell, 10, 10)
        pixmap = index.data(THUMB_ROLE)
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            pixmap = self._gallery.placeholder
        ratio = pixmap.devicePixelRatio()
        size = QSizeF(pixmap.width() / ratio, pixmap.height() / ratio)
        bound = cell.adjusted(6, 6, -6, -6)
        if size.width() > bound.width() or size.height() > bound.height():
            size = size.scaled(bound.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(
            bound.center().x() - size.width() / 2,
            bound.center().y() - size.height() / 2,
            size.width(),
            size.height(),
        )
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if self._gallery.selection_mode or hovered or (selected and self._gallery.multiple_selected()):
            self._paint_check(painter, check_center(option.rect), selected)
        painter.restore()

    @staticmethod
    def _paint_check(painter, center: QPointF, selected: bool) -> None:
        radius = CHECK_RADIUS
        if selected:
            painter.setPen(QPen(QColor(ACCENT_DARK), 1))
            painter.setBrush(QColor(ACCENT))
        else:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawEllipse(center, radius, radius)
        if selected:
            painter.setPen(
                QPen(QColor(ACCENT_DARK), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mark = QPainterPath(QPointF(center.x() - 3.6, center.y() + 0.2))
            mark.lineTo(center.x() - 1.0, center.y() + 2.8)
            mark.lineTo(center.x() + 3.8, center.y() - 2.6)
            painter.drawPath(mark)


def check_center(rect) -> QPointF:
    cell = QRectF(rect).adjusted(3, 3, -3, -3)
    return QPointF(cell.right() - CHECK_RADIUS - 3, cell.top() + CHECK_RADIUS + 3)


class Gallery(QListWidget):
    selection_changed = Signal(list)
    contents_changed = Signal()
    context_requested = Signal(object)
    activated_path = Signal(str)
    selection_mode_changed = Signal(bool)

    def __init__(self, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.setObjectName("gallery")
        self._loader = loader
        self._loader.thumb_ready.connect(self._store_thumb)
        self._items: dict[str, QListWidgetItem] = {}
        self._thumbs: dict[str, QPixmap] = {}
        self._folder: Path | None = None
        self._hidden: set[str] = set()
        self.selection_mode = False
        self._swipe_select: bool | None = None
        self.placeholder = self._make_placeholder()
        self.setItemDelegate(ThumbDelegate(self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setGridSize(CELL_SIZE)
        self.setSpacing(0)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemDoubleClicked.connect(lambda item: self.activated_path.emit(item.data(PATH_ROLE)))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(CELL_SIZE.height() + 22)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(350)
        self._sync_timer.timeout.connect(self.sync)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _path: self._sync_timer.start())
        self._scroll_animation = QPropertyAnimation(self.horizontalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(220)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    @property
    def folder(self) -> Path | None:
        return self._folder

    def thumb_bound(self) -> QSize:
        ratio = self.devicePixelRatioF()
        return QSize(round(THUMB_SIZE.width() * ratio), round(THUMB_SIZE.height() * ratio))

    def selected_paths(self) -> list[str]:
        items = sorted(self.selectedItems(), key=self.row)
        return [item.data(PATH_ROLE) for item in items]

    def position(self) -> tuple[int, int]:
        return self.currentRow() + 1, self.count()

    def step(self, delta: int) -> None:
        if not self.count():
            return
        row = self.currentRow()
        self.go(0 if row < 0 else max(0, min(self.count() - 1, row + delta)))

    def go(self, row: int) -> None:
        if not self.count():
            return
        if row < 0:
            row = self.count() + row
        self.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def load_folder(self, folder: Path) -> None:
        self.set_selection_mode(False)
        self._folder = Path(folder)
        self._watch(reset=True)
        self.blockSignals(True)
        self.clear()
        self._items.clear()
        self._thumbs.clear()
        for path in self._visible(scan_images(self._folder)):
            self.addItem(self._create_item(path))
        if self.count():
            self.setCurrentRow(0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.blockSignals(False)
        self.horizontalScrollBar().setValue(0)
        self.selection_changed.emit(self.selected_paths())
        self.contents_changed.emit()

    def add_batch(self, entries: list[tuple[str, QPixmap | None]], select: bool = True) -> None:
        self.blockSignals(True)
        for path, thumb in reversed(entries):
            if thumb is not None:
                self._store_thumb(path, thumb)
            if path not in self._items:
                self.insertItem(0, self._create_item(path))
        if not select:
            self.blockSignals(False)
            self._watch()
            self.contents_changed.emit()
            return
        self.clearSelection()
        first = None
        for path, _thumb in entries:
            item = self._items.get(path)
            if item is not None:
                item.setSelected(True)
                if first is None:
                    first = item
        if first is not None:
            self.setCurrentItem(first, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.blockSignals(False)
        self._watch()
        if first is not None:
            self._scroll_to(first)
        self.contents_changed.emit()

    def remove_paths(self, paths: list[str]) -> None:
        items = [self._items[path] for path in paths if path in self._items]
        if not items:
            return
        first_row = min(self.row(item) for item in items)
        selection_affected = any(item.isSelected() for item in items)
        self.blockSignals(True)
        for path in paths:
            item = self._items.pop(path, None)
            self._thumbs.pop(path, None)
            if item is not None:
                self.takeItem(self.row(item))
        if selection_affected and not self.selectedItems() and self.count():
            self.setCurrentRow(min(first_row, self.count() - 1), QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.blockSignals(False)
        if selection_affected:
            current = self.currentItem()
            if current is not None:
                self._scroll_to(current)
            self.selection_changed.emit(self.selected_paths())
        self.contents_changed.emit()

    def sync(self) -> None:
        if self._folder is None:
            return
        on_disk = self._visible(scan_images(self._folder))
        present = set(on_disk)
        self.remove_paths([path for path in self._items if path not in present])
        added = False
        for index, path in enumerate(on_disk):
            if path not in self._items:
                self.insertItem(index, self._create_item(path))
                added = True
        if not self.selectedItems() and self.count():
            self.setCurrentRow(0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self._watch()
        if added:
            self.contents_changed.emit()

    def hide_paths(self, paths: list[str]) -> None:
        normalized = [os.path.normpath(path) for path in paths]
        self._hidden.update(normalized)
        self.remove_paths([path for path in normalized if path in self._items])

    def unhide_paths(self, paths: list[str]) -> None:
        self._hidden.difference_update(os.path.normpath(path) for path in paths)
        self._sync_timer.start()

    def _visible(self, paths: list[str]) -> list[str]:
        return [path for path in paths if path not in self._hidden] if self._hidden else paths

    def _create_item(self, path: str) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(PATH_ROLE, path)
        item.setToolTip(os.path.basename(path))
        thumb = self._thumbs.get(path)
        if thumb is None:
            self._loader.request_thumb(path, self.thumb_bound())
        else:
            item.setData(THUMB_ROLE, thumb)
        self._items[path] = item
        return item

    def _store_thumb(self, path: str, pixmap: QPixmap) -> None:
        ratio = self.devicePixelRatioF()
        thumb = rounded(pixmap, 6 * ratio)
        thumb.setDevicePixelRatio(ratio)
        self._thumbs[path] = thumb
        item = self._items.get(path)
        if item is not None:
            item.setData(THUMB_ROLE, thumb)

    def _make_placeholder(self) -> QPixmap:
        ratio = max(2.0, self.devicePixelRatioF())
        pixmap = QPixmap(round(THUMB_SIZE.width() * ratio), round(THUMB_SIZE.height() * ratio))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1d1f23"))
        painter.drawRoundedRect(QRectF(pixmap.rect()), 6 * ratio, 6 * ratio)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def _watch(self, reset: bool = False) -> None:
        watched = self._watcher.directories()
        if reset and watched:
            self._watcher.removePaths(watched)
            watched = []
        if self._folder is not None and self._folder.is_dir() and str(self._folder) not in watched:
            self._watcher.addPath(str(self._folder))

    def multiple_selected(self) -> bool:
        return len(self.selectionModel().selectedIndexes()) > 1

    def set_selection_mode(self, enabled: bool) -> None:
        if enabled == self.selection_mode:
            return
        self.selection_mode = enabled
        self.setDragEnabled(not enabled)
        if not enabled and self.multiple_selected():
            current = self.currentRow()
            row = current if current >= 0 else self.row(self.selectedItems()[0])
            self.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.viewport().update()
        self.selection_mode_changed.emit(enabled)

    def select_all(self) -> None:
        if not self.count():
            return
        self.set_selection_mode(True)
        self.selectAll()

    def _toggle_item(self, item: QListWidgetItem, selected: bool | None = None) -> None:
        index = self.indexFromItem(item)
        model = self.selectionModel()
        if selected is None:
            flag = QItemSelectionModel.SelectionFlag.Toggle
        elif selected:
            flag = QItemSelectionModel.SelectionFlag.Select
        else:
            flag = QItemSelectionModel.SelectionFlag.Deselect
        model.select(index, flag)
        model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _on_check(self, item: QListWidgetItem, position) -> bool:
        center = check_center(self.visualItemRect(item))
        dx = position.x() - center.x()
        dy = position.y() - center.y()
        return dx * dx + dy * dy <= (CHECK_RADIUS + 5) ** 2

    def mousePressEvent(self, event):
        position = event.position()
        item = self.itemAt(position.toPoint())
        modifiers = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        if event.button() == Qt.MouseButton.LeftButton and item is not None and not modifiers:
            if self.selection_mode or self._on_check(item, position):
                if not self.selection_mode:
                    self.blockSignals(True)
                    self.clearSelection()
                    self.blockSignals(False)
                    self.set_selection_mode(True)
                self._toggle_item(item)
                self._swipe_select = item.isSelected()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._swipe_select is not None and event.buttons() & Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item.isSelected() != self._swipe_select:
                self._toggle_item(item, self._swipe_select)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._swipe_select is not None:
            self._swipe_select = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.selection_mode:
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def _on_context_menu(self, position) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.context_requested.emit(self.viewport().mapToGlobal(position))

    def startDrag(self, supported_actions):
        paths = self.selected_paths()
        if not paths:
            return
        drag = QDrag(self)
        drag.setMimeData(file_mime(paths))
        thumb = self._thumbs.get(paths[0])
        if thumb is not None:
            drag.setPixmap(thumb)
        drag.exec(Qt.DropAction.CopyAction)

    def _on_selection_changed(self) -> None:
        self.viewport().update()
        current = self.currentItem()
        if current is not None and current.isSelected():
            self._scroll_to(current)
        self.selection_changed.emit(self.selected_paths())

    def _scroll_to(self, item: QListWidgetItem) -> None:
        rect = self.visualItemRect(item)
        width = self.viewport().width()
        if rect.left() >= 0 and rect.right() <= width:
            return
        self._animate_scroll(self.horizontalScrollBar().value() + rect.center().x() - width // 2)

    def _animate_scroll(self, target: int) -> None:
        bar = self.horizontalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), int(target)))
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            super().wheelEvent(event)
            return
        bar = self.horizontalScrollBar()
        running = self._scroll_animation.state() == QPropertyAnimation.State.Running
        base = int(self._scroll_animation.endValue()) if running else bar.value()
        self._animate_scroll(base - delta)
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(MUTED))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, "Your screenshots will show up here")
            painter.end()
