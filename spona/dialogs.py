from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .theme import style_title_bar


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
