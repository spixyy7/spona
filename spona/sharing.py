import hashlib
import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QPixmap

from .images import IMAGE_SUFFIXES
from .naming import release_path, reserve_path, safe_name

FILE_LIMIT = 30
FILES_BYTES = 72 * 1024 * 1024
TEXT_LIMIT = 4 * 1024 * 1024
BLOB_KEEP_SECONDS = 20 * 60
FOLDER_KEEP_SECONDS = 6 * 3600


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def clipboard_payload(mime):
    if mime is None:
        return None
    if mime.hasUrls():
        urls = mime.urls()
        files = [url.toLocalFile() for url in urls if url.isLocalFile()]
        images = [path for path in files if path.lower().endswith(IMAGE_SUFFIXES) and os.path.isfile(path)]
        if images and len(images) == len(urls):
            return "files", images[:FILE_LIMIT]
        if files and len(files) == len(urls) and not mime.hasImage():
            return None
    if mime.hasImage():
        image = mime.imageData()
        if isinstance(image, QPixmap):
            image = image.toImage()
        if isinstance(image, QImage) and not image.isNull():
            return "image", QImage(image)
    if mime.hasText():
        text = mime.text()
        if text.strip():
            return "text", text
    return None


class PrepareSignals(QObject):
    ready = Signal(int, object)


class PrepareTask(QRunnable):
    def __init__(self, number: int, kind: str, value, signals: PrepareSignals):
        super().__init__()
        self._number = number
        self._kind = kind
        self._value = value
        self._signals = signals

    def run(self):
        items = []
        if self._kind == "image":
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if self._value.save(buffer, "PNG", 50):
                data = bytes(buffer.data().data())
                items.append((digest(data), f"Clipboard_{time.strftime('%Y-%m-%d_%H-%M-%S')}.png", data))
        else:
            total = 0
            for path in self._value:
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    continue
                if total + len(data) > FILES_BYTES:
                    break
                total += len(data)
                items.append((digest(data), Path(path).name, data))
        self._signals.ready.emit(self._number, items)


class Blobs:
    def __init__(self, folder: Path):
        self.folder = folder
        self._known: dict[str, tuple[str, float]] = {}

    def store(self, key: str, name: str, data: bytes) -> str | None:
        if not isinstance(key, str) or digest(data) != key:
            return None
        self.tidy()
        known = self.path(key)
        if known is not None:
            return known
        stem, suffix = _split(name)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            target = reserve_path(self.folder, stem, suffix)
            try:
                partial = target.with_name(target.name + ".part")
                partial.write_bytes(data)
                os.replace(partial, target)
            finally:
                release_path(target)
        except OSError:
            return None
        self._known[key] = (str(target), time.monotonic())
        return str(target)

    def path(self, key) -> str | None:
        entry = self._known.get(key) if isinstance(key, str) else None
        if entry is None:
            return None
        if not os.path.isfile(entry[0]):
            del self._known[key]
            return None
        self._known[key] = (entry[0], time.monotonic())
        return entry[0]

    def claim(self, key: str, folder: Path, name: str) -> str | None:
        source = self.path(key)
        if source is None:
            return None
        stem, suffix = _split(name or Path(source).name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = reserve_path(folder, stem, suffix)
            try:
                if inside(source, self.folder):
                    shutil.move(source, target)
                else:
                    shutil.copy2(source, target)
            finally:
                release_path(target)
        except OSError:
            return None
        self._known[key] = (str(target), time.monotonic())
        return str(target)

    def remember(self, key: str, path: str) -> None:
        if isinstance(key, str) and key:
            self._known[key] = (str(path), time.monotonic())

    def tidy(self) -> None:
        now = time.monotonic()
        for key in [key for key, (_path, when) in self._known.items() if now - when > BLOB_KEEP_SECONDS]:
            del self._known[key]
        tidy_folder(self.folder)


def tidy_folder(folder: Path, keep: float = FOLDER_KEEP_SECONDS) -> None:
    try:
        limit = time.time() - keep
        for entry in folder.iterdir():
            if entry.is_file() and entry.stat().st_mtime < limit:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _split(name: str) -> tuple[str, str]:
    base = Path(str(name or "image")).name
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, "png"
    suffix = "." + (safe_name(suffix).lower() or "png")
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".png"
    return safe_name(stem)[:120] or "image", suffix


def inside(path: str, folder: Path) -> bool:
    try:
        return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(folder))
    except (OSError, ValueError):
        return False
