from collections import OrderedDict

from PySide6.QtCore import QMimeData, QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QPainterPath, QPixmap

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def rounded(pixmap: QPixmap, radius: float) -> QPixmap:
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(result.rect()), radius, radius)
    painter.fillPath(path, Qt.GlobalColor.white)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result


def file_mime(paths: list[str], image: QImage | None = None) -> QMimeData:
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(path) for path in paths])
    if image is not None and not image.isNull():
        data.setImageData(image)
    return data


class _Signals(QObject):
    image_ready = Signal(str, QImage, bool)


class _ReadTask(QRunnable):
    def __init__(self, path: str, bound: QSize | None, signals: _Signals):
        super().__init__()
        self._path = path
        self._bound = bound
        self._signals = signals

    def run(self):
        reader = QImageReader(self._path)
        reader.setAutoTransform(True)
        if self._bound is not None:
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(self._bound, Qt.AspectRatioMode.KeepAspectRatio))
        self._signals.image_ready.emit(self._path, reader.read(), self._bound is not None)


class ImageLoader(QObject):
    full_ready = Signal(str, QPixmap)
    full_failed = Signal(str)
    thumb_ready = Signal(str, QPixmap)

    def __init__(self, parent=None, cache_size: int = 12):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _Signals(self)
        self._signals.image_ready.connect(self._on_ready)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_size = cache_size
        self._pending_full: set[str] = set()
        self._pending_thumbs: set[str] = set()

    def cached(self, path: str) -> QPixmap | None:
        pixmap = self._cache.get(path)
        if pixmap is not None:
            self._cache.move_to_end(path)
        return pixmap

    def remember(self, path: str, pixmap: QPixmap) -> None:
        self._cache[path] = pixmap
        self._cache.move_to_end(path)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def wait_idle(self, milliseconds: int) -> None:
        self._pool.waitForDone(milliseconds)

    def forget(self, path: str) -> None:
        self._cache.pop(path, None)

    def request_full(self, path: str) -> None:
        if path in self._pending_full:
            return
        self._pending_full.add(path)
        self._pool.start(_ReadTask(path, None, self._signals), 10)

    def request_thumb(self, path: str, bound: QSize) -> None:
        if path in self._pending_thumbs:
            return
        self._pending_thumbs.add(path)
        self._pool.start(_ReadTask(path, bound, self._signals), 0)

    def _on_ready(self, path: str, image: QImage, thumbnail: bool) -> None:
        if thumbnail:
            self._pending_thumbs.discard(path)
            if not image.isNull():
                self.thumb_ready.emit(path, QPixmap.fromImage(image))
            return
        self._pending_full.discard(path)
        if image.isNull():
            self.full_failed.emit(path)
            return
        pixmap = QPixmap.fromImage(image)
        self.remember(path, pixmap)
        self.full_ready.emit(path, pixmap)
