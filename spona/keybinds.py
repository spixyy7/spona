from dataclasses import dataclass

from PySide6.QtCore import QKeyCombination, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

MAIN = "main"
DRAW = "draw"
PORTABLE = QKeySequence.SequenceFormat.PortableText
KEY_NAMES = {
    "Del": "Delete",
    "Ins": "Insert",
    "Return": "Enter",
    "PgUp": "Page Up",
    "PgDown": "Page Down",
    "Left": "←",
    "Right": "→",
    "Up": "↑",
    "Down": "↓",
}
MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_Meta,
    Qt.Key.Key_Super_L,
    Qt.Key.Key_Super_R,
    Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock,
    Qt.Key.Key_ScrollLock,
    Qt.Key.Key_unknown,
}
RESERVED = ("Esc",)


@dataclass(frozen=True)
class KeyAction:
    id: str
    label: str
    group: str
    defaults: tuple[str, ...]
    scopes: frozenset = frozenset({MAIN})


def _action(identity: str, label: str, group: str, *defaults: str, scopes=(MAIN,)) -> KeyAction:
    return KeyAction(identity, label, group, tuple(normal(key) for key in defaults), frozenset(scopes))


def normal(key: str) -> str:
    text = QKeySequence(key, PORTABLE).toString(PORTABLE)
    return text[: -len("Enter")] + "Return" if text == "Enter" or text.endswith("+Enter") else text


GROUPS = (("shots", "Screenshots"), ("browse", "Browse and folders"), ("draw", "While drawing"))
ACTIONS = (
    _action("capture", "Capture", "shots", "F5", "Space"),
    _action("undo", "Undo", "shots", "Ctrl+Z", scopes=(MAIN, DRAW)),
    _action("copy", "Copy", "shots", "Ctrl+C"),
    _action("delete", "Delete", "shots", "Delete"),
    _action("select_all", "Select all", "shots", "Ctrl+A"),
    _action("marker", "Marker on / off", "shots", "M"),
    _action("fullscreen", "Fullscreen", "shots", "F11", "F", "Return"),
    _action("unlock", "Unlock phones", "shots", "Ctrl+U"),
    _action("previous", "Previous", "browse", "Left", "Up", "PgUp"),
    _action("next", "Next", "browse", "Right", "Down", "PgDown"),
    _action("first", "First", "browse", "Home"),
    _action("last", "Last", "browse", "End"),
    _action("open_folder", "Open the folder", "browse", "Ctrl+O"),
    _action("change_folder", "Change the folder", "browse", "Ctrl+L"),
    _action("redo", "Redo", "draw", "Ctrl+Y", "Ctrl+Shift+Z", scopes=(DRAW,)),
    _action("save_marks", "Save as a new image", "draw", "Ctrl+S", scopes=(DRAW,)),
    _action("clear_marks", "Clear marks", "draw", "Delete", scopes=(DRAW,)),
    _action("pen", "Marker", "draw", "P", scopes=(DRAW,)),
    _action("eraser", "Eraser", "draw", "E", scopes=(DRAW,)),
    _action("smaller", "Smaller brush", "draw", "[", scopes=(DRAW,)),
    _action("bigger", "Bigger brush", "draw", "]", scopes=(DRAW,)),
    _action("color1", "Red", "draw", "1", scopes=(DRAW,)),
    _action("color2", "Yellow", "draw", "2", scopes=(DRAW,)),
    _action("color3", "Green", "draw", "3", scopes=(DRAW,)),
    _action("color4", "Blue", "draw", "4", scopes=(DRAW,)),
    _action("color5", "White", "draw", "5", scopes=(DRAW,)),
    _action("color6", "Black", "draw", "6", scopes=(DRAW,)),
)
BY_ID = {action.id: action for action in ACTIONS}
NOT_WHILE_DRAWING = ("delete",)


def pretty(key: str) -> str:
    if key.endswith("+"):
        head, last = key[:-1], "+"
    else:
        head, separator, last = key.rpartition("+")
        head += separator
    return head + KEY_NAMES.get(last, last)


def event_key(event) -> str:
    key = Qt.Key(event.key())
    if key in MODIFIER_KEYS or int(key) == 0:
        return ""
    modifiers = event.modifiers() & ~(
        Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.GroupSwitchModifier
    )
    return normal(QKeySequence(QKeyCombination(modifiers, key)).toString(PORTABLE))


class Keybinds(QObject):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._custom: dict[str, list[str]] = {}
        stored = settings.get("keybinds")
        if isinstance(stored, dict):
            for identity, keys in stored.items():
                if identity in BY_ID and isinstance(keys, list):
                    cleaned = [normal(key) for key in keys if isinstance(key, str)]
                    self._custom[identity] = [key for key in dict.fromkeys(cleaned) if key and key not in RESERVED]

    def keys(self, identity: str) -> list[str]:
        custom = self._custom.get(identity)
        if custom is not None:
            return list(custom)
        action = BY_ID[identity]
        claimed = {
            key for other, keys in self._custom.items() if BY_ID[other].scopes & action.scopes for key in keys
        }
        return [key for key in action.defaults if key not in claimed]

    def customized(self, identity: str) -> bool:
        return identity in self._custom

    def any_customized(self) -> bool:
        return bool(self._custom)

    def set_keys(self, identity: str, keys: list[str]) -> list[tuple[str, str]]:
        keys = [key for key in dict.fromkeys(normal(key) for key in keys) if key and key not in RESERVED]
        taken = []
        scopes = BY_ID[identity].scopes
        for other in ACTIONS:
            if other.id == identity or not (other.scopes & scopes):
                continue
            current = self.keys(other.id)
            lost = [key for key in current if key in keys]
            if lost:
                self._store(other.id, [key for key in current if key not in keys])
                taken += [(key, other.label) for key in lost]
        self._store(identity, keys)
        self._save()
        return taken

    def reset(self, identity: str) -> list[tuple[str, str]]:
        return self.set_keys(identity, list(BY_ID[identity].defaults))

    def reset_all(self) -> None:
        self._custom.clear()
        self._save()

    def _store(self, identity: str, keys: list[str]) -> None:
        if keys == list(BY_ID[identity].defaults):
            self._custom.pop(identity, None)
        else:
            self._custom[identity] = list(keys)

    def _save(self) -> None:
        self._settings.set("keybinds", {identity: list(keys) for identity, keys in self._custom.items()})
        self.changed.emit()

    def text(self, identity: str, every: bool = False) -> str:
        keys = self.keys(identity)
        if not every:
            keys = keys[:1]
        return " or ".join(pretty(key) for key in keys[:3])

    def hint(self, identity: str, gap: str = " ", every: bool = False) -> str:
        text = self.text(identity, every)
        return f"{gap}({text})" if text else ""

    def match(self, event, scope: str) -> str | None:
        key = event_key(event)
        if not key:
            return None
        for action in ACTIONS:
            if scope in action.scopes and key in self.keys(action.id):
                return action.id
        return None

    def match_drawing(self, event) -> str | None:
        found = self.match(event, DRAW)
        if found is None:
            found = self.match(event, MAIN)
        return None if found in NOT_WHILE_DRAWING else found
