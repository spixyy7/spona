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
    def __init__(self, delete_mode: str, on_change, parent=None):
        super().__init__("Settings", parent, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        section = QLabel("When deleting")
        section.setObjectName("section")
        layout.addWidget(title)
        layout.addSpacing(4)
        layout.addWidget(section)
        group = QButtonGroup(self)
        for mode, label in DELETE_MODES:
            option = QRadioButton(label)
            option.setChecked(mode == delete_mode)
            option.toggled.connect(lambda checked, value=mode: checked and on_change("delete_mode", value))
            group.addButton(option)
            layout.addWidget(option)
        note = QLabel("Only screenshots that came in with Auto-receive are also on the phone.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addSpacing(2)
        layout.addWidget(note)
        close = QPushButton("Done")
        close.setObjectName("primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addSpacing(6)
        layout.addLayout(buttons)


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
