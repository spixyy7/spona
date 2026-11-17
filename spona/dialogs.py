from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from .theme import style_title_bar

DELETE_MODES = (
    ("ask", "Ask every time"),
    ("pc", "PC only"),
    ("both", "PC and phone"),
)
PHONE_SHOT_MODES = (
    ("phone", "Off  ·  keep on the phone only"),
    ("copy", "Copy to PC  ·  keep on the phone too"),
    ("move", "Move to PC  ·  delete from the phone"),
)


class StyledDialog(QDialog):
    def __init__(self, title: str, parent=None, width: int = 400):
        super().__init__(parent)
        self.setObjectName("dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(width)
        self._styled = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._styled:
            style_title_bar(self)
            self._styled = True


class DeleteDialog(StyledDialog):
    def __init__(self, count: int, on_phone: int, devices: list[str], parent=None):
        super().__init__("Delete", parent, 430)
        self.choice: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        noun = "screenshot" if count == 1 else f"{count} screenshots"
        title = QLabel(f"Delete {noun}?")
        title.setObjectName("dialogTitle")
        where = ", ".join(devices) or "the phone"
        if not on_phone:
            detail = "Only on this PC, so it goes to the Recycle Bin." if count == 1 else "Only on this PC, so they go to the Recycle Bin."
        elif count == 1:
            detail = f"Also on {where}.\nPC: Recycle Bin. Phone: deleted for good."
        elif on_phone == count:
            detail = f"All of them are also on {where}.\nPC: Recycle Bin. Phone: deleted for good."
        else:
            detail = f"{on_phone} of them are also on {where}.\nPC: Recycle Bin. Phone: deleted for good."
        hint = QLabel(detail)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self._remember = QCheckBox("Remember my choice (change it in Settings)")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        pc_only = QPushButton("PC only")
        pc_only.setObjectName("primary")
        pc_only.setDefault(True)
        pc_only.clicked.connect(lambda: self._choose("pc"))
        both = QPushButton("PC + phone")
        both.setObjectName("danger")
        both.clicked.connect(lambda: self._choose("both"))
        if not on_phone:
            both.setEnabled(False)
            both.setToolTip("Not on a phone: screenshots taken with Capture are saved only on this PC")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(pc_only)
        buttons.addWidget(both)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self._remember)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        pc_only.setFocus(Qt.FocusReason.OtherFocusReason)

    def remember(self) -> bool:
        return self._remember.isChecked()

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class SettingsDialog(StyledDialog):
    def __init__(self, values: dict, on_change, parent=None):
        super().__init__("Settings", parent, 390)
        self._on_change = on_change
        self._groups: dict[str, QButtonGroup] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self._section(
            layout,
            "phone_shots",
            "Screenshots taken on the phone",
            PHONE_SHOT_MODES,
            values.get("phone_shots", "phone"),
            None,
        )
        self._section(
            layout,
            "delete_mode",
            "When deleting",
            DELETE_MODES,
            values.get("delete_mode", "ask"),
            "Only screenshots copied with Auto-receive are also on the phone.",
        )
        close = QPushButton("Done")
        close.setObjectName("primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addSpacing(10)
        layout.addLayout(buttons)

    def _section(self, layout, key: str, heading: str, modes, current: str, note: str | None) -> None:
        layout.addSpacing(10)
        section = QLabel(heading)
        section.setObjectName("section")
        layout.addWidget(section)
        group = QButtonGroup(self)
        for mode, label in modes:
            option = QRadioButton(label)
            option.setChecked(mode == current)
            option.toggled.connect(lambda checked, value=mode: checked and self._on_change(key, value))
            group.addButton(option)
            layout.addWidget(option)
        if note:
            hint = QLabel(note)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self._groups[key] = group


class SecretDialog(QDialog):
    def __init__(self, device_name: str, can_remember: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("dialog")
        self.setWindowTitle("Unlock phone")
        self.setModal(True)
        self.setFixedWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel(f"Unlock  ·  {device_name}")
        title.setObjectName("dialogTitle")
        hint = QLabel("Enter the phone PIN or password. Patterns, fingerprint and face unlock can't be used over adb.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self._input = QLineEdit()
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setPlaceholderText("PIN or password")
        self._input.returnPressed.connect(self.accept)
        self._remember = QCheckBox("Remember on this computer (encrypted by Windows)")
        self._remember.setVisible(can_remember)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("Unlock")
        confirm.setObjectName("primary")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self._input)
        layout.addWidget(self._remember)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)
        self._styled = False

    def secret(self) -> str:
        return self._input.text()

    def remember(self) -> bool:
        return self._remember.isVisible() and self._remember.isChecked()

    def accept(self):
        if self._input.text():
            super().accept()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._styled:
            style_title_bar(self)
            self._styled = True

    def done(self, result):
        if not result:
            self._input.clear()
        super().done(result)
