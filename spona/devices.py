from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from .adb import Device
from .icons import Glyph, dot_icon, glyph_icon
from .theme import ACCENT, IDLE, WARNING

STATE_TEXT = {
    "device": "connected",
    "unauthorized": "waiting for approval on the phone",
    "offline": "offline",
    "recovery": "recovery mode",
    "sideload": "sideload mode",
    "bootloader": "bootloader",
    "authorizing": "authorizing",
    "connecting": "connecting",
}
CHIP_ICON_SIZE = 12


def state_text(device: Device) -> str:
    return STATE_TEXT.get(device.state, device.state)


def screen_text(device: Device) -> str:
    if not device.ready:
        return state_text(device)
    parts = []
    if device.locked:
        parts.append("locked")
    if device.screen_on is False:
        parts.append("screen off")
    return ", ".join(parts)


class DeviceBar(QWidget):
    selection_changed = Signal()
    capture_only = Signal(str)
    unlock_requested = Signal(str)
    lock_requested = Signal(str)
    forget_secret_requested = Signal(str)
    blocked = Signal(object)

    def __init__(self, deselected: set[str], has_secret: Callable[[str], bool], parent=None):
        super().__init__(parent)
        self._deselected = set(deselected)
        self._has_secret = has_secret
        self._devices: list[Device] = []
        self._chips: dict[str, QToolButton] = {}
        self._dot_icons = {color: dot_icon(color, CHIP_ICON_SIZE) for color in (ACCENT, WARNING, IDLE)}
        self._lock_icon = glyph_icon(Glyph.LOCK, WARNING, CHIP_ICON_SIZE)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._empty_dot = QLabel()
        self._empty_dot.setPixmap(dot_icon(IDLE).pixmap(QSize(10, 10)))
        self._empty = QLabel("No phone connected")
        self._empty.setObjectName("muted")
        self._layout.addWidget(self._empty_dot)
        self._layout.addWidget(self._empty)
        self._layout.addStretch(1)

    def devices(self) -> list[Device]:
        return list(self._devices)

    def selected(self) -> list[Device]:
        return [device for device in self._devices if device.ready and device.serial not in self._deselected]

    def deselected(self) -> set[str]:
        return set(self._deselected)

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = list(devices)
        serials = {device.serial for device in devices}
        for serial in [serial for serial in self._chips if serial not in serials]:
            chip = self._chips.pop(serial)
            self._layout.removeWidget(chip)
            chip.deleteLater()
        for chip in self._chips.values():
            self._layout.removeWidget(chip)
        for index, device in enumerate(devices):
            chip = self._chips.get(device.serial) or self._create_chip(device.serial)
            self._update_chip(chip, device)
            self._layout.insertWidget(index, chip)
        has_devices = bool(devices)
        self._empty.setVisible(not has_devices)
        self._empty_dot.setVisible(not has_devices)

    def _create_chip(self, serial: str) -> QToolButton:
        chip = QToolButton(self)
        chip.setObjectName("chip")
        chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        chip.setIconSize(QSize(CHIP_ICON_SIZE, CHIP_ICON_SIZE))
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        chip.clicked.connect(lambda checked=False, s=serial: self._on_clicked(s, checked))
        chip.customContextMenuRequested.connect(lambda position, s=serial: self._show_menu(s, position))
        self._chips[serial] = chip
        return chip

    def _update_chip(self, chip: QToolButton, device: Device) -> None:
        chip.blockSignals(True)
        status = screen_text(device)
        chip.setText(f"{device.name}  ·  {status}" if status else device.name)
        if device.ready and device.locked:
            chip.setIcon(self._lock_icon)
        elif device.ready:
            chip.setIcon(self._dot_icons[ACCENT])
        elif device.state == "unauthorized":
            chip.setIcon(self._dot_icons[WARNING])
        else:
            chip.setIcon(self._dot_icons[IDLE])
        chip.setCheckable(device.ready)
        chip.setChecked(device.ready and device.serial not in self._deselected)
        details = [device.name]
        if device.manufacturer or device.model:
            details.append(f"Model: {' '.join(part for part in (device.manufacturer, device.model) if part)}")
        if device.android:
            details.append(f"Android {device.android}")
        details.append(f"Serial: {device.serial}")
        details.append(f"Status: {state_text(device)}")
        if device.ready and device.screen_on is not None:
            details.append(f"Screen: {'on' if device.screen_on else 'off'}")
        if device.ready and device.locked is not None:
            details.append(f"Locked: {'yes' if device.locked else 'no'}")
        if device.ready:
            details.append("")
            details.append("Click: include or exclude from captures")
            details.append("Right-click: unlock, lock, capture only this one")
        chip.setToolTip("\n".join(details))
        chip.blockSignals(False)

    def _device(self, serial: str) -> Device | None:
        return next((device for device in self._devices if device.serial == serial), None)

    def _on_clicked(self, serial: str, checked: bool) -> None:
        device = self._device(serial)
        if device is None:
            return
        if not device.ready:
            self.blocked.emit(device)
            return
        if checked:
            self._deselected.discard(serial)
        else:
            self._deselected.add(serial)
        self.selection_changed.emit()

    def _show_menu(self, serial: str, position) -> None:
        device = self._device(serial)
        chip = self._chips.get(serial)
        if device is None or chip is None or not device.ready:
            return
        menu = QMenu(self)
        menu.addAction(glyph_icon(Glyph.CAMERA, size=16), "Capture only this phone", lambda: self.capture_only.emit(serial))
        menu.addAction(glyph_icon(Glyph.UNLOCK, size=16), "Unlock", lambda: self.unlock_requested.emit(serial))
        menu.addAction(glyph_icon(Glyph.LOCK, size=16), "Lock (screen off)", lambda: self.lock_requested.emit(serial))
        menu.addSeparator()
        menu.addAction("Select only this one", lambda: self._select_only(serial))
        menu.addAction("Select all phones", self._select_all)
        if self._has_secret(serial):
            menu.addSeparator()
            menu.addAction("Forget saved PIN", lambda: self.forget_secret_requested.emit(serial))
        menu.exec(chip.mapToGlobal(position))

    def _select_only(self, serial: str) -> None:
        connected = {device.serial for device in self._devices}
        self._deselected = (self._deselected - connected) | (connected - {serial})
        self._refresh_checks()

    def _select_all(self) -> None:
        self._deselected -= {device.serial for device in self._devices}
        self._refresh_checks()

    def _refresh_checks(self) -> None:
        for device in self._devices:
            chip = self._chips.get(device.serial)
            if chip is not None:
                chip.blockSignals(True)
                chip.setChecked(device.ready and device.serial not in self._deselected)
                chip.blockSignals(False)
        self.selection_changed.emit()
