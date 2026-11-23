from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QMenu, QSizePolicy, QWidget

from .adb import Device
from .icons import Glyph, dot_icon, glyph_icon, icon_font_family
from .widgets import separator, tool_button
from .theme import ACCENT, ACCENT_TINT, BACKGROUND, BORDER, IDLE, MUTED, SURFACE_HIGH, TEXT, WARNING

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
TONES = {"on": ACCENT, "warn": WARNING, "off": IDLE}
CHIP_HEIGHT = 30
ICON = 16
PAD_LEFT = 10
PAD_RIGHT = 12
GAP = 7
LEVELS = 4


def state_text(device: Device) -> str:
    return STATE_TEXT.get(device.state, device.state)


def screen_text(device: Device) -> str:
    if device.network or device.remote:
        parts = [device.transport]
    elif device.also_over:
        parts = [f"USB + {device.also_over}"]
    else:
        parts = []
    if not device.ready:
        return ", ".join(parts + [state_text(device)])
    if device.locked:
        parts.append("locked")
    if device.screen_on is False:
        parts.append("screen off")
    return ", ".join(parts)


def device_tone(device: Device) -> str:
    if not device.ready:
        return "warn" if device.state == "unauthorized" else "off"
    return "warn" if device.locked else "on"


class Chip(QAbstractButton):
    menu_requested = Signal(object)

    def __init__(self, glyph: str, parent=None, menu: bool = False):
        super().__init__(parent)
        self._glyph = glyph
        self._name = ""
        self._status = ""
        self._tone = "on"
        self._dot = not menu
        self._menu = menu
        self._lit = False
        self._accent_status = False
        self._level = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda position: self.menu_requested.emit(self.mapToGlobal(position)))
        self._name_font = QFont(self.font())
        self._name_font.setWeight(QFont.Weight.DemiBold)
        self._status_font = QFont(self.font())
        self._status_font.setPixelSize(12)
        self._icon_font = QFont(icon_font_family())
        self._icon_font.setPixelSize(14)
        self._chevron_font = QFont(icon_font_family())
        self._chevron_font.setPixelSize(9)

    def set_content(self, name: str, status: str = "", tone: str = "on", lit: bool = False, accent_status: bool = False, dot: bool | None = None) -> None:
        values = (name, status, tone, lit, accent_status, self._dot if dot is None else dot)
        if values == (self._name, self._status, self._tone, self._lit, self._accent_status, self._dot):
            return
        self._name, self._status, self._tone, self._lit, self._accent_status, self._dot = values
        self.updateGeometry()
        self.update()

    def name(self) -> str:
        return self._name

    def status(self) -> str:
        return self._status

    def width_at(self, level: int) -> int:
        chevron = 13 if self._menu else 0
        if level >= 2 or not self._name:
            return PAD_LEFT + ICON + PAD_LEFT + chevron
        width = PAD_LEFT + ICON + GAP + QFontMetrics(self._name_font).horizontalAdvance(self._name)
        if level == 0 and self._status:
            width += GAP + QFontMetrics(self._status_font).horizontalAdvance(self._status)
        return width + PAD_RIGHT + chevron

    def set_level(self, level: int) -> None:
        if level != self._level:
            self._level = level
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.width_at(self._level), CHIP_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        hovered = self.underMouse() and self.isEnabled()
        active = self.isChecked() or self._lit
        if active:
            fill, border = ("#20332a" if hovered else ACCENT_TINT), ACCENT
        elif self._menu:
            fill, border = (SURFACE_HIGH if hovered else BACKGROUND), ("#3a3d44" if hovered else BORDER)
        else:
            fill, border = BACKGROUND, ("#3a3d44" if hovered else BORDER)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, QColor(fill))
        painter.setPen(QPen(QColor(border), 1))
        painter.drawPath(path)
        height = self.height()
        icon_rect = QRectF(PAD_LEFT, (height - ICON) / 2, ICON, ICON)
        if self._tone == "off" and not active:
            icon_color = IDLE
        elif active:
            icon_color = ACCENT
        else:
            icon_color = TEXT if hovered else MUTED
        painter.setFont(self._icon_font)
        painter.setPen(QColor(icon_color))
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, self._glyph)
        if self._dot:
            center = QPointF(icon_rect.right() - 2, icon_rect.bottom() - 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawEllipse(center, 4.6, 4.6)
            painter.setBrush(QColor(TONES.get(self._tone, IDLE)))
            painter.drawEllipse(center, 3.1, 3.1)
        x = PAD_LEFT + ICON + GAP
        if self._level < 2 and self._name:
            name_width = QFontMetrics(self._name_font).horizontalAdvance(self._name)
            if self._tone == "off" and not active:
                name_color = MUTED
            else:
                name_color = TEXT if active or hovered else "#c3c6cc"
            painter.setFont(self._name_font)
            painter.setPen(QColor(name_color))
            painter.drawText(QRectF(x, 0, name_width + 2, height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._name)
            x += name_width
            if self._level == 0 and self._status:
                x += GAP
                status_width = QFontMetrics(self._status_font).horizontalAdvance(self._status)
                painter.setFont(self._status_font)
                painter.setPen(QColor(ACCENT if self._accent_status else MUTED))
                painter.drawText(QRectF(x, 0, status_width + 2, height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._status)
        if self._menu:
            painter.setFont(self._chevron_font)
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(self.width() - 22, 0, 12, height), Qt.AlignmentFlag.AlignCenter, Glyph.CHEVRON_DOWN)
        painter.end()


class DeviceBar(QWidget):
    selection_changed = Signal()
    capture_only = Signal(str)
    unlock_requested = Signal(str)
    lock_requested = Signal(str)
    forget_secret_requested = Signal(str)
    add_requested = Signal()
    forget_network_requested = Signal(str)
    blocked = Signal(object)
    pcs_changed = Signal()
    pc_menu_requested = Signal(str, object)
    this_pc_requested = Signal(object)

    def __init__(self, deselected: set[str], has_secret: Callable[[str], bool], deselected_pcs: set[str] | None = None, parent=None):
        super().__init__(parent)
        self._deselected = set(deselected)
        self._deselected_pcs = set(deselected_pcs or ())
        self._has_secret = has_secret
        self._devices: list[Device] = []
        self._chips: dict[str, Chip] = {}
        self._pcs: list[dict] = []
        self._pc_chips: dict[str, Chip] = {}
        self._level = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._empty_dot = QLabel()
        self._empty_dot.setPixmap(dot_icon(IDLE).pixmap(QSize(10, 10)))
        self._empty = QLabel("No phone connected")
        self._empty.setObjectName("muted")
        self._add_button = tool_button(Glyph.ADD, "Connect a phone over Tailscale", self.add_requested.emit, icon_size=16)
        self._divider = separator(20)
        self._this_pc = Chip(Glyph.PC, self, menu=True)
        self._this_pc.clicked.connect(lambda: self.this_pc_requested.emit(self._this_pc.mapToGlobal(QPoint(0, self._this_pc.height() + 4))))
        self._this_pc.menu_requested.connect(self.this_pc_requested.emit)
        self._pc_area = False
        self._divider.hide()
        self._this_pc.hide()
        self._rebuild()

    def devices(self) -> list[Device]:
        return list(self._devices)

    def selected(self) -> list[Device]:
        return [device for device in self._devices if device.ready and device.serial not in self._deselected]

    def deselected(self) -> set[str]:
        return set(self._deselected)

    def deselected_pcs(self) -> set[str]:
        return set(self._deselected_pcs)

    def selected_pcs(self) -> list[str]:
        return [pc["id"] for pc in self._pcs if pc["id"] not in self._deselected_pcs]

    def full_width(self) -> int:
        return self._total(0)

    def sizeHint(self) -> QSize:
        return QSize(self._total(0), CHIP_HEIGHT + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(min(self._total(LEVELS - 1), 220), CHIP_HEIGHT + 2)

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = list(devices)
        serials = {device.serial for device in devices}
        for serial in [serial for serial in self._chips if serial not in serials]:
            chip = self._chips.pop(serial)
            chip.deleteLater()
        for device in devices:
            chip = self._chips.get(device.serial) or self._create_chip(device.serial)
            self._update_chip(chip, device)
        self._rebuild()

    def set_pcs(self, visible: bool, this_pc: dict, pcs: list[dict]) -> None:
        self._pc_area = visible
        self._pcs = list(pcs) if visible else []
        ids = {pc["id"] for pc in self._pcs}
        for identity in [identity for identity in self._pc_chips if identity not in ids]:
            self._pc_chips.pop(identity).deleteLater()
        for pc in self._pcs:
            chip = self._pc_chips.get(pc["id"]) or self._create_pc_chip(pc["id"])
            self._update_pc_chip(chip, pc)
        host = bool(this_pc.get("host"))
        self._this_pc.set_content("This PC", this_pc.get("status", ""), lit=host, accent_status=host, dot=False)
        self._this_pc.setToolTip(this_pc.get("tooltip", ""))
        self._rebuild()

    def _rebuild(self) -> None:
        while self._layout.count():
            self._layout.takeAt(0)
        has_devices = bool(self._devices)
        self._empty.setVisible(not has_devices)
        self._empty_dot.setVisible(not has_devices)
        if not has_devices:
            self._layout.addWidget(self._empty_dot)
            self._layout.addWidget(self._empty)
        for device in self._devices:
            chip = self._chips[device.serial]
            chip.show()
            self._layout.addWidget(chip)
        self._layout.addWidget(self._add_button)
        self._divider.setVisible(self._pc_area)
        self._this_pc.setVisible(self._pc_area)
        if self._pc_area:
            self._layout.addSpacing(4)
            self._layout.addWidget(self._divider)
            self._layout.addSpacing(4)
            self._layout.addWidget(self._this_pc)
            for pc in self._pcs:
                chip = self._pc_chips[pc["id"]]
                chip.show()
                self._layout.addWidget(chip)
        self._layout.addStretch(1)
        self._fit()
        self.updateGeometry()

    def _chips_in_order(self) -> list[Chip]:
        chips = [self._chips[device.serial] for device in self._devices if device.serial in self._chips]
        if self._pc_area:
            chips.append(self._this_pc)
            chips += [self._pc_chips[pc["id"]] for pc in self._pcs if pc["id"] in self._pc_chips]
        return chips

    def _chip_level(self, chip: Chip, level: int) -> int:
        if chip in self._chips.values():
            return {0: 0, 1: 1, 2: 1}.get(level, 2)
        return min(level, 2)

    def _total(self, level: int) -> int:
        spacing = self._layout.spacing()
        widths = [chip.width_at(self._chip_level(chip, level)) for chip in self._chips_in_order()]
        if not self._devices:
            widths += [10, self._empty.sizeHint().width()]
        widths.append(self._add_button.sizeHint().width())
        if self._pc_area:
            widths.append(1 + 8)
        return sum(widths) + spacing * max(0, len(widths) - 1)

    def _fit(self) -> None:
        available = self.width()
        level = LEVELS - 1
        for candidate in range(LEVELS):
            if self._total(candidate) <= available:
                level = candidate
                break
        self._level = level
        for chip in self._chips_in_order():
            chip.set_level(self._chip_level(chip, level))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _create_chip(self, serial: str) -> Chip:
        chip = Chip(Glyph.PHONE, self)
        chip.clicked.connect(lambda checked=False, s=serial: self._on_clicked(s, checked))
        chip.menu_requested.connect(lambda position, s=serial: self._show_menu(s, position))
        self._chips[serial] = chip
        return chip

    def _update_chip(self, chip: Chip, device: Device) -> None:
        chip.blockSignals(True)
        chip.set_content(device.name, screen_text(device), device_tone(device))
        chip.setCheckable(device.ready)
        chip.setChecked(device.ready and device.serial not in self._deselected)
        details = [device.name]
        if device.remote:
            details.append(f"Lent by {device.via}, the host PC")
        if device.manufacturer or device.model:
            details.append(f"Model: {' '.join(part for part in (device.manufacturer, device.model) if part)}")
        if device.android:
            details.append(f"Android {device.android}")
        if not device.remote:
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

    def _create_pc_chip(self, identity: str) -> Chip:
        chip = Chip(Glyph.PC, self)
        chip.setCheckable(True)
        chip.clicked.connect(lambda checked=False, value=identity: self._on_pc_clicked(value, checked))
        chip.menu_requested.connect(lambda position, value=identity: self.pc_menu_requested.emit(value, position))
        self._pc_chips[identity] = chip
        return chip

    def _update_pc_chip(self, chip: Chip, pc: dict) -> None:
        chip.blockSignals(True)
        phones = int(pc.get("phones") or 0)
        if pc.get("host"):
            status = "Host" if not phones else f"Host  ·  {phones} {'phone' if phones == 1 else 'phones'}"
        else:
            status = ""
        chip.set_content(pc["name"], status, "on", accent_status=bool(pc.get("host")), dot=False)
        chip.setChecked(pc["id"] not in self._deselected_pcs)
        lines = [pc["name"], f"Spona {pc['version']}" if pc.get("version") else "Spona"]
        if pc.get("host"):
            lines.append(f"Host: lends {phones} {'phone' if phones == 1 else 'phones'} to your PCs")
        lines += ["", "Click: send what you copy and new screenshots there, or not", "Right-click: send images, pick only this one"]
        chip.setToolTip("\n".join(lines))
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

    def _on_pc_clicked(self, identity: str, checked: bool) -> None:
        if checked:
            self._deselected_pcs.discard(identity)
        else:
            self._deselected_pcs.add(identity)
        self.pcs_changed.emit()

    def select_only_pc(self, identity: str) -> None:
        others = {pc["id"] for pc in self._pcs if pc["id"] != identity}
        self._deselected_pcs = (self._deselected_pcs - {identity}) | others
        self._refresh_pc_checks()

    def select_all_pcs(self) -> None:
        self._deselected_pcs -= {pc["id"] for pc in self._pcs}
        self._refresh_pc_checks()

    def _refresh_pc_checks(self) -> None:
        for pc in self._pcs:
            chip = self._pc_chips.get(pc["id"])
            if chip is not None:
                chip.blockSignals(True)
                chip.setChecked(pc["id"] not in self._deselected_pcs)
                chip.blockSignals(False)
        self.pcs_changed.emit()

    def _show_menu(self, serial: str, position) -> None:
        device = self._device(serial)
        if device is None or serial not in self._chips:
            return
        menu = QMenu(self)
        if not device.ready:
            if device.network:
                menu.addAction("Forget this phone", lambda: self.forget_network_requested.emit(serial))
                menu.exec(position)
            return
        menu.addAction(glyph_icon(Glyph.CAMERA, size=16), "Capture only this phone", lambda: self.capture_only.emit(serial))
        menu.addAction(glyph_icon(Glyph.UNLOCK, size=16), "Unlock", lambda: self.unlock_requested.emit(serial))
        menu.addAction(glyph_icon(Glyph.LOCK, size=16), "Lock (screen off)", lambda: self.lock_requested.emit(serial))
        menu.addSeparator()
        menu.addAction("Select only this one", lambda: self._select_only(serial))
        menu.addAction("Select all phones", self._select_all)
        if self._has_secret(device.identity):
            menu.addSeparator()
            menu.addAction("Forget saved PIN", lambda: self.forget_secret_requested.emit(serial))
        if device.network:
            menu.addSeparator()
            menu.addAction("Forget this phone (Tailscale)", lambda: self.forget_network_requested.emit(serial))
        menu.exec(position)

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
