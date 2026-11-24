import ctypes
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile, QObject, Signal

from .naming import release_path, reserve_path
from .settings import APP_NAME

UNDO_FOLDER = Path(tempfile.gettempdir()) / APP_NAME / "undo"
HISTORY_LIMIT = 200


def recycle(path: str) -> tuple[bool, str]:
    handle = QFile(path)
    if not handle.moveToTrash():
        return False, ""
    place = handle.fileName()
    if os.path.normcase(os.path.normpath(place)) == os.path.normcase(os.path.normpath(path)):
        return True, ""
    return True, place


def keep_copy(path: str) -> str:
    target = UNDO_FOLDER / f"{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    try:
        UNDO_FOLDER.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    except OSError:
        return ""
    return str(target)


def clear_copies() -> None:
    shutil.rmtree(UNDO_FOLDER, ignore_errors=True)


def put_back(place: str, original: str) -> str:
    if not place or not os.path.isfile(place):
        raise FileNotFoundError(original)
    folder = Path(original).parent
    folder.mkdir(parents=True, exist_ok=True)
    target = reserve_path(folder, Path(original).stem, Path(original).suffix)
    try:
        shutil.move(place, str(target))
    finally:
        release_path(target)
    _forget_recycled(place)
    return os.path.normpath(str(target))


def _forget_recycled(place: str) -> None:
    folder, name = os.path.split(place)
    if sys.platform != "win32" or not name.upper().startswith("$R"):
        return
    try:
        os.remove(os.path.join(folder, "$I" + name[2:]))
    except OSError:
        pass
    try:
        ctypes.windll.shell32.SHUpdateRecycleBinIcon()
    except (AttributeError, OSError):
        pass


@dataclass(eq=False)
class DeletedShot:
    path: str
    place: str = ""
    origin: dict | None = None
    on_phone: bool = False
    marks: tuple | None = None


@dataclass(eq=False)
class DeleteAction:
    shots: list[DeletedShot] = field(default_factory=list)
    report: object = None

    @property
    def label(self) -> str:
        count = len(self.shots)
        return "Undo delete" if count == 1 else f"Undo delete of {count} screenshots"


@dataclass(eq=False)
class MarkAction:
    key: str

    @property
    def label(self) -> str:
        return "Undo last mark"


@dataclass(eq=False)
class SaveAction:
    source: str
    target: str
    marks: tuple | None = None

    @property
    def label(self) -> str:
        return "Undo save of the marked image"


class History(QObject):
    changed = Signal()

    def __init__(self, limit: int = HISTORY_LIMIT, parent=None):
        super().__init__(parent)
        self._limit = limit
        self._actions: list = []

    def push(self, action) -> None:
        self._actions.append(action)
        del self._actions[: -self._limit]
        self.changed.emit()

    def peek(self):
        return self._actions[-1] if self._actions else None

    def take(self, action=None):
        if action is None:
            if not self._actions:
                return None
            action = self._actions.pop()
        elif action in self._actions:
            self._actions.remove(action)
        else:
            return None
        self.changed.emit()
        return action

    def drop_mark(self, key: str) -> None:
        for index in range(len(self._actions) - 1, -1, -1):
            action = self._actions[index]
            if isinstance(action, MarkAction) and action.key == key:
                del self._actions[index]
                self.changed.emit()
                return
