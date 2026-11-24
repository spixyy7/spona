from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
)

from .collage import COPY_LAYOUTS
from .icons import Glyph
from .keybinds import ACTIONS, BY_ID, GROUPS, RESERVED, Keybinds, event_key, pretty
from .theme import style_title_bar
from .widgets import tool_button

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
PC_OPTIONS = (
    ("pc_sharing", "Share with my other PCs"),
    ("pc_host", "Host  ·  lend this PC's phones to them"),
    ("pc_send_clipboard", "Send what I copy"),
    ("pc_send_shots", "Send new screenshots"),
)
PC_DEFAULTS = {"pc_sharing": True, "pc_host": False, "pc_send_clipboard": True, "pc_send_shots": True}


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
    def __init__(
        self,
        values: dict,
        on_change,
        parent=None,
        network: dict | None = None,
        pcs: dict | None = None,
        on_shortcuts=None,
    ):
        super().__init__("Settings", parent, 980)
        self._on_change = on_change
        self._network = network or {}
        self._groups: dict[str, QButtonGroup] = {}
        self._rows: dict[str, QPushButton] = {}
        self._pc_boxes: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        columns = QHBoxLayout()
        columns.setSpacing(30)
        phones = QVBoxLayout()
        phones.setSpacing(6)
        gallery = QVBoxLayout()
        gallery.setSpacing(6)
        computers = QVBoxLayout()
        computers.setSpacing(6)
        columns.addLayout(phones, 1)
        columns.addLayout(gallery, 1)
        columns.addLayout(computers, 1)
        layout.addLayout(columns)
        self._section(
            phones,
            "phone_shots",
            "Screenshots taken on the phone",
            PHONE_SHOT_MODES,
            values.get("phone_shots", "phone"),
            None,
        )
        self._section(
            phones,
            "clipboard_sync",
            "Clipboard",
            CLIPBOARD_MODES,
            values.get("clipboard_sync", "off"),
            "Copied text moves between this PC and the selected phones, over USB or Tailscale.",
        )
        self._section(
            gallery,
            "copy_layout",
            "Copying several images",
            COPY_LAYOUTS,
            values.get("copy_layout", "grid"),
            "The grid keeps every shot whole and fits tall and wide ones into even rows.",
        )
        self._section(
            gallery,
            "delete_mode",
            "When deleting",
            DELETE_MODES,
            values.get("delete_mode", "ask"),
            "Only screenshots copied with Auto-receive are also on the phone.",
        )
        if pcs is not None:
            self._pc_section(computers, values, pcs)
        if network is not None:
            self._tailscale_section(computers)
        phones.addStretch(1)
        gallery.addStretch(1)
        computers.addStretch(1)
        close = QPushButton("Done")
        close.setObjectName("primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        if on_shortcuts is not None:
            shortcuts = QPushButton("Keyboard shortcuts…")
            shortcuts.setAutoDefault(False)
            shortcuts.clicked.connect(lambda: on_shortcuts(self))
            buttons.addWidget(shortcuts)
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

    def _pc_section(self, layout, values: dict, pcs: dict) -> None:
        layout.addSpacing(10)
        section = QLabel("Other PCs")
        section.setObjectName("section")
        layout.addWidget(section)
        for key, label in PC_OPTIONS:
            box = QCheckBox(label)
            box.setChecked(bool(values.get(key, PC_DEFAULTS[key])))
            box.toggled.connect(lambda checked, value=key: self._on_pc_option(value, checked))
            self._pc_boxes[key] = box
            layout.addWidget(box)
        hint = QLabel(
            "PCs that run Spona on your Tailscale account show up next to the phones by themselves. "
            "Click one to choose if things go there. A host's phones work on every PC, and the host is used first."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addSpacing(2)
        layout.addWidget(hint)
        self._pc_status = QLabel(pcs.get("status", ""))
        self._pc_status.setObjectName("status")
        self._pc_status.setWordWrap(True)
        self._pc_status.setVisible(bool(pcs.get("status")))
        layout.addSpacing(4)
        layout.addWidget(self._pc_status)
        self._sync_pc_boxes()

    def _on_pc_option(self, key: str, checked: bool) -> None:
        self._on_change(key, checked)
        self._sync_pc_boxes()

    def _sync_pc_boxes(self) -> None:
        sharing = self._pc_boxes["pc_sharing"].isChecked()
        for key, box in self._pc_boxes.items():
            if key != "pc_sharing":
                box.setEnabled(sharing)

    def set_pc_status(self, text: str) -> None:
        if hasattr(self, "_pc_status"):
            self._pc_status.setText(text)
            self._pc_status.setVisible(bool(text))

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


class KeyChip(QPushButton):
    recorded = Signal(str)
    cancelled = Signal()
    removed = Signal()

    def __init__(self, key: str, parent=None, temporary: bool = False):
        super().__init__(parent)
        self.key = key
        self.temporary = temporary
        self._recording = False
        self.setObjectName("key")
        self.setAutoDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click and press the new keys\nRight-click removes it")
        self.clicked.connect(self.record)
        self._show()

    def record(self) -> None:
        self._recording = True
        self._show()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def recording(self) -> bool:
        return self._recording

    def _show(self) -> None:
        self.setText("Press keys…" if self._recording else pretty(self.key) or "None")
        self.setProperty("recording", self._recording)
        self.setProperty("empty", not self.key)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    def _cancel(self) -> None:
        if self._recording:
            self._recording = False
            self._show()
            self.cancelled.emit()

    def event(self, event):
        if self._recording and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def focusNextPrevChild(self, forward):
        return False if self._recording else super().focusNextPrevChild(forward)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._cancel()

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return
        event.accept()
        key = event_key(event)
        if key in RESERVED:
            self._cancel()
        elif key and not event.isAutoRepeat():
            self._recording = False
            self.key = key
            self._show()
            self.recorded.emit(key)

    def keyReleaseEvent(self, event):
        if self._recording:
            event.accept()
            return
        super().keyReleaseEvent(event)

    def contextMenuEvent(self, event):
        event.accept()
        self.removed.emit()


class ShortcutsDialog(StyledDialog):
    def __init__(self, keys: Keybinds, parent=None):
        super().__init__("Keyboard shortcuts", parent, 980)
        self._keys = keys
        self._chips: dict[str, QHBoxLayout] = {}
        self._resets: dict[str, QToolButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        title = QLabel("Keyboard shortcuts")
        title.setObjectName("dialogTitle")
        hint = QLabel("Click a key and press the new one. + adds another key, right-click removes one. Esc stays fixed.")
        hint.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(hint)
        columns = QHBoxLayout()
        columns.setSpacing(30)
        for group, heading in GROUPS:
            column = QVBoxLayout()
            column.setSpacing(3)
            section = QLabel(heading)
            section.setObjectName("section")
            column.addWidget(section)
            column.addSpacing(2)
            for action in ACTIONS:
                if action.group == group:
                    column.addLayout(self._row(action))
            column.addStretch(1)
            columns.addLayout(column, 1)
        layout.addSpacing(8)
        layout.addLayout(columns)
        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        policy = self._status.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._status.setSizePolicy(policy)
        self._status.hide()
        self._reset_all = QPushButton("Reset all")
        self._reset_all.setAutoDefault(False)
        self._reset_all.clicked.connect(self._on_reset_all)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.setAutoDefault(False)
        done.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addWidget(self._reset_all)
        buttons.addWidget(self._status, 1)
        buttons.addWidget(done)
        layout.addSpacing(8)
        layout.addLayout(buttons)
        self._fill()

    def _row(self, action) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(action.label)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        add = tool_button(Glyph.ADD, "Add another key", lambda: self._add(action.id), icon_size=12)
        reset = tool_button(Glyph.RESET, "", lambda: self._reset(action.id), icon_size=12)
        defaults = " or ".join(pretty(key) for key in action.defaults)
        reset.setToolTip(f"Back to {defaults}")
        policy = reset.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        reset.setSizePolicy(policy)
        row.addWidget(label)
        row.addStretch(1)
        row.addLayout(chips)
        row.addWidget(add)
        row.addWidget(reset)
        self._chips[action.id] = chips
        self._resets[action.id] = reset
        return row

    def _fill(self) -> None:
        for identity in self._chips:
            self._fill_row(identity)
        self._reset_all.setEnabled(self._keys.any_customized())

    def _fill_row(self, identity: str) -> None:
        chips = self._chips[identity]
        while chips.count():
            widget = chips.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for key in self._keys.keys(identity) or [""]:
            chips.addWidget(self._chip(identity, key))
        self._resets[identity].setVisible(self._keys.customized(identity))

    def _chip(self, identity: str, key: str, temporary: bool = False) -> KeyChip:
        chip = KeyChip(key, self, temporary)
        chip.recorded.connect(lambda new, old=key: self._record(identity, old, new))
        chip.cancelled.connect(lambda: self._cancelled(identity, chip))
        chip.removed.connect(lambda: self._remove(identity, key))
        return chip

    def _cancelled(self, identity: str, chip: KeyChip) -> None:
        if chip.temporary:
            chip.hide()
            self._chips[identity].removeWidget(chip)
            chip.deleteLater()

    def _add(self, identity: str) -> None:
        if not self._keys.keys(identity):
            self._chips[identity].itemAt(0).widget().record()
            return
        chip = self._chip(identity, "", temporary=True)
        self._chips[identity].addWidget(chip)
        chip.record()

    def _record(self, identity: str, old: str, new: str) -> None:
        keys = self._keys.keys(identity)
        if old in keys:
            keys[keys.index(old)] = new
        else:
            keys.append(new)
        taken = self._keys.set_keys(identity, keys)
        text = f"{BY_ID[identity].label}  ·  {pretty(new)}"
        if taken:
            text += "  ·  taken from " + ", ".join(label for _key, label in taken)
        self._say(text, "ok")
        QTimer.singleShot(0, self._fill)

    def _remove(self, identity: str, key: str) -> None:
        if not key:
            return
        self._keys.set_keys(identity, [existing for existing in self._keys.keys(identity) if existing != key])
        self._say(f"{BY_ID[identity].label}  ·  {pretty(key)} removed")
        QTimer.singleShot(0, self._fill)

    def _reset(self, identity: str) -> None:
        taken = self._keys.reset(identity)
        text = f"{BY_ID[identity].label}  ·  back to {' or '.join(pretty(key) for key in self._keys.keys(identity))}"
        if taken:
            text += "  ·  taken from " + ", ".join(label for _key, label in taken)
        self._say(text, "ok")
        self._fill()

    def _on_reset_all(self) -> None:
        self._keys.reset_all()
        self._say("All shortcuts are back to default", "ok")
        self._fill()

    def _say(self, text: str, kind: str = "info") -> None:
        self._status.setText(text)
        self._status.setProperty("kind", kind)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._status.show()


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
