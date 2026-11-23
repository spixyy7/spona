from PySide6.QtCore import Qt, QTimer, Signal
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

from .collage import COPY_LAYOUTS
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
CLIPBOARD_MODES = (
    ("off", "Off"),
    ("both", "Both ways  ·  PC ↔ phone"),
    ("to_phone", "PC → phone only"),
    ("to_pc", "Phone → PC only"),
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
    def __init__(self, values: dict, on_change, parent=None, network: dict | None = None):
        super().__init__("Settings", parent, 760)
        self._on_change = on_change
        self._network = network or {}
        self._groups: dict[str, QButtonGroup] = {}
        self._rows: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        columns = QHBoxLayout()
        columns.setSpacing(32)
        left = QVBoxLayout()
        left.setSpacing(6)
        right = QVBoxLayout()
        right.setSpacing(6)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        layout.addLayout(columns)
        self._section(
            left,
            "phone_shots",
            "Screenshots taken on the phone",
            PHONE_SHOT_MODES,
            values.get("phone_shots", "phone"),
            None,
        )
        self._section(
            left,
            "delete_mode",
            "When deleting",
            DELETE_MODES,
            values.get("delete_mode", "ask"),
            "Only screenshots copied with Auto-receive are also on the phone.",
        )
        self._section(
            right,
            "clipboard_sync",
            "Clipboard",
            CLIPBOARD_MODES,
            values.get("clipboard_sync", "off"),
            "Copied text moves between this PC and the selected phones, over USB or Tailscale.",
        )
        self._section(
            right,
            "copy_layout",
            "Copying several images",
            COPY_LAYOUTS,
            values.get("copy_layout", "grid"),
            "The grid keeps every shot whole and fits tall and wide ones into even rows.",
        )
        if network is not None:
            self._tailscale_section(left)
        left.addStretch(1)
        right.addStretch(1)
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

    def _tailscale_section(self, layout) -> None:
        layout.addSpacing(10)
        section = QLabel("Tailscale")
        section.setObjectName("section")
        layout.addWidget(section)
        enabled = QCheckBox("Use phones over Tailscale  ·  auto-reconnect")
        enabled.setChecked(bool(self._network.get("enabled", True)))
        enabled.toggled.connect(lambda checked: self._on_change("tailscale_enabled", checked))
        layout.addWidget(enabled)
        saved = self._network.get("saved") or []
        if not saved:
            empty = QLabel("No phones set up yet.")
            empty.setObjectName("muted")
            layout.addWidget(empty)
        for entry in saved:
            row = QHBoxLayout()
            row.setSpacing(8)
            name = QLabel(entry.get("name") or entry["address"])
            address = QLabel(entry["address"])
            address.setObjectName("muted")
            forget = QPushButton("Forget")
            forget.clicked.connect(
                lambda _checked=False, value=entry["address"], widgets=(name, address): self._forget(value, widgets)
            )
            row.addWidget(name)
            row.addWidget(address)
            row.addStretch(1)
            row.addWidget(forget)
            layout.addLayout(row)
            self._rows[entry["address"]] = forget
        setup = QPushButton("Connect a phone…")
        setup.clicked.connect(self._open_setup)
        row = QHBoxLayout()
        row.addWidget(setup)
        row.addStretch(1)
        layout.addSpacing(2)
        layout.addLayout(row)

    def _forget(self, address: str, widgets) -> None:
        callback = self._network.get("on_forget")
        if callback is not None:
            callback(address)
        for widget in widgets:
            widget.setEnabled(False)
        button = self._rows.get(address)
        if button is not None:
            button.setText("Forgotten")
            button.setEnabled(False)

    def _open_setup(self) -> None:
        callback = self._network.get("on_setup")
        self.accept()
        if callback is not None:
            QTimer.singleShot(0, callback)


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


class NetworkDialog(StyledDialog):
    go_wireless_requested = Signal(str)
    connect_requested = Signal(str)

    def __init__(self, usb_devices, peers, tailscale_found: bool, parent=None):
        super().__init__("Connect over Tailscale", parent, 470)
        self._buttons: list[QPushButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        title = QLabel("Connect over Tailscale")
        title.setObjectName("dialogTitle")
        intro = QLabel("Use your phones without a cable, from anywhere on your tailnet.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        layout.addSpacing(10)
        layout.addWidget(self._heading("Set up once with USB"))
        if usb_devices:
            for serial, name in usb_devices:
                layout.addLayout(
                    self._row(name, "", "Go wireless", lambda _checked=False, value=serial: self._request_wireless(value))
                )
        else:
            layout.addWidget(self._note("Plug a phone in with USB, then come back here."))

        layout.addSpacing(10)
        layout.addWidget(self._heading("Or connect directly"))
        if not tailscale_found:
            layout.addWidget(self._note("Tailscale isn't installed on this PC."))
        phones = [peer for peer in peers if peer.os == "android"]
        if tailscale_found and not phones:
            layout.addWidget(self._note("No Android phones on your tailnet yet. Turn Tailscale on on the phone."))
        for peer in phones[:6]:
            button_text = "Connect" if peer.online else "Offline"
            row = self._row(
                peer.name,
                peer.ip,
                button_text,
                lambda _checked=False, value=peer.ip: self._request_connect(value),
                enabled=peer.online,
            )
            layout.addLayout(row)
        address_row = QHBoxLayout()
        address_row.setSpacing(8)
        self._address = QLineEdit()
        self._address.setPlaceholderText("Address, e.g. 100.101.102.103")
        self._address.returnPressed.connect(lambda: self._request_connect(self._address.text()))
        connect = QPushButton("Connect")
        connect.clicked.connect(lambda: self._request_connect(self._address.text()))
        self._buttons.append(connect)
        address_row.addWidget(self._address, 1)
        address_row.addWidget(connect)
        layout.addSpacing(4)
        layout.addLayout(address_row)

        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        self._status.hide()
        layout.addSpacing(6)
        layout.addWidget(self._status)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(done)
        layout.addSpacing(8)
        layout.addLayout(footer)

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _note(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    def _row(self, name: str, detail: str, action: str, handler, enabled: bool = True) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(name)
        row.addWidget(label)
        if detail:
            extra = QLabel(detail)
            extra.setObjectName("muted")
            row.addWidget(extra)
        row.addStretch(1)
        button = QPushButton(action)
        button.setEnabled(enabled)
        button.clicked.connect(handler)
        self._buttons.append(button)
        row.addWidget(button)
        return row

    def _request_wireless(self, serial: str) -> None:
        self.set_status("Switching the phone to wireless and looking for it on Tailscale…", "busy")
        self.go_wireless_requested.emit(serial)

    def _request_connect(self, text: str) -> None:
        if not text.strip():
            self.set_status("Type the phone's Tailscale address first", "error")
            return
        self.set_status(f"Connecting to {text.strip()}…", "busy")
        self.connect_requested.emit(text.strip())

    def set_status(self, text: str, kind: str = "info") -> None:
        self._status.setText(text)
        self._status.setProperty("kind", kind)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._status.show()
        for button in self._buttons:
            button.setEnabled(kind != "busy" and button.text() != "Offline")
