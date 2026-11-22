import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QFile, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QActionGroup, QDesktopServices, QDrag, QGuiApplication, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from .adb import Adb, Device, UnlockOutcome, find_adb, normalize_address
from .devices import DeviceBar, state_text
from .dialogs import CLIPBOARD_MODES, PHONE_SHOT_MODES, DeleteDialog, NetworkDialog, SecretDialog, SettingsDialog
from .origins import PhoneOrigins
from .gallery import Gallery
from .icons import Glyph, glyph_icon
from .images import ImageLoader, file_mime, side_by_side
from .settings import APP_NAME, Settings
from .theme import ACCENT_DARK, style_title_bar
from .annotations import AnnotationStore, Brush, render_annotated
from .naming import release_path, reserve_path, safe_name
from .vault import Vault
from .viewer import FullscreenViewer, ImageView
from .widgets import ElidedLabel, ResponsiveBar, Toast, separator, tool_button
from .filelocks import locking_processes, release_file
from .tailscale import find_tailscale, tailscale_peers
from .workers import (
    CallTask,
    CaptureSignals,
    ClipboardBridge,
    CaptureTask,
    DeviceMonitor,
    LockTask,
    NetworkReconnector,
    NetworkSignals,
    NetworkTask,
    DeleteSignals,
    PhoneDeleteTask,
    PhoneScreenshotWatcher,
    ScreenSignals,
    UnlockTask,
)

MAX_SIDE_BY_SIDE = 8
TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
CLIP_HELPER = Path(__file__).with_name("clip_helper.dex")
CLIP_LIMIT = 200_000
CLIP_MODE_KEYS = tuple(mode for mode, _label in CLIPBOARD_MODES)
FILE_PATTERN = re.compile(r"^Screenshot_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(?P<device>.+?)(?:_\d+)?$")


def default_folder() -> Path:
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return Path(pictures or Path.home() / "Pictures") / APP_NAME


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def images_word(count: int) -> str:
    return "image" if count == 1 else "images"


def clipboard_text(mime) -> str:
    if mime is None or not mime.hasText():
        return ""
    if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()):
        return ""
    text = mime.text()
    return text if text.strip() else ""


def move_to_trash(path: str) -> bool:
    result = QFile.moveToTrash(path)
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def remove_for_good(path: str) -> bool:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.remove(path)
    except OSError:
        return not os.path.exists(path)
    return True


class TrashOutcome:
    def __init__(self):
        self.deleted: list[str] = []
        self.forever: list[str] = []
        self.stuck: list[str] = []
        self.released: list[str] = []
        self.holders: list[str] = []


class DeleteReport:
    def __init__(self, number: int, count: int, outcome: TrashOutcome, also_phone: bool, on_phone: int):
        self.number = number
        self.count = count
        self.outcome = outcome
        self.also_phone = also_phone
        self.on_phone = on_phone
        self.waiting: set[str] = set()
        self.offline = 0
        self.phone_removed = 0
        self.phone_missed = 0
        self.phone_names: list[str] = []
        self.phone_errors: list[str] = []


def delete_summary(report: DeleteReport) -> tuple[str, str]:
    outcome = report.outcome
    count = report.count
    on_pc = len(outcome.deleted) + len(outcome.forever)
    names = ", ".join(report.phone_names) or "the phone"
    phone_clean = not (report.offline or report.phone_missed or report.phone_errors)
    everywhere = (
        report.also_phone and on_pc == count and report.on_phone == count
        and report.phone_removed == count and phone_clean
    )
    parts = []
    kind = "info"
    if everywhere:
        parts.append(f"Deleted on PC and {names}")
        kind = "ok"
    elif on_pc == count and not report.also_phone:
        parts.append(
            "Deleted  ·  moved to the Recycle Bin" if count == 1
            else f"Deleted {count} {images_word(count)}  ·  moved to the Recycle Bin"
        )
    elif on_pc == count:
        parts.append("Deleted on PC" if count == 1 else f"Deleted {count} on PC")
    elif on_pc:
        parts.append(f"Deleted {on_pc} on PC")
    if outcome.stuck:
        holder = ", ".join(outcome.holders) or "another app"
        stuck = "" if count == 1 else f"{len(outcome.stuck)} "
        parts.append(f"{stuck}in use by {holder}, deleted on PC once it's free")
    if outcome.forever:
        parts.append("no Recycle Bin there, deleted for good")
    if report.also_phone and not everywhere:
        if not report.on_phone:
            parts.append("wasn't on the phone" if count == 1 else "weren't on the phone")
        else:
            if report.phone_removed:
                there = report.phone_removed
                parts.append(f"deleted on {names}" if there == count else f"{there} also deleted on {names}")
            if report.offline:
                parts.append("phone not connected, kept there")
                kind = "error"
            if report.phone_missed:
                parts.append(f"{report.phone_missed} not removed from the phone")
                kind = "error"
            if report.phone_errors:
                parts.append(f"phone delete failed: {report.phone_errors[0]}")
                kind = "error"
    if outcome.released:
        parts.append(f"freed from {', '.join(outcome.released)}")
    if not parts:
        parts.append("Nothing deleted")
        kind = "error"
    text = "  ·  ".join(parts)
    return text[:1].upper() + text[1:], kind


class CaptureBatch:
    def __init__(self, number: int, devices: list[Device], targets: dict[str, Path]):
        self.number = number
        self.devices = devices
        self.targets = targets
        self.results: dict[str, tuple] = {}
        self.errors: dict[str, str] = {}

    @property
    def finished(self) -> int:
        return len(self.results) + len(self.errors)

    @property
    def done(self) -> bool:
        return self.finished >= len(self.devices)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(600, 620)
        self._settings = settings
        self._adb: Adb | None = None
        self._adb_error: str | None = None
        self._monitor: DeviceMonitor | None = None
        self._devices: list[Device] = []
        self._ready_serials: set[str] = set()
        self._first_scan = True
        self._batch: CaptureBatch | None = None
        self._batch_counter = 0
        self._selected_paths: list[str] = []
        self._displayed_paths: list[str] = []
        self._broken: set[str] = set()
        self._fullscreen: FullscreenViewer | None = None
        self._title_bar_styled = False
        self._capture_pool = QThreadPool(self)
        self._capture_pool.setMaxThreadCount(6)
        self._capture_signals = CaptureSignals(self)
        self._capture_signals.captured.connect(self._on_captured)
        self._capture_signals.failed.connect(self._on_capture_failed)
        self._loader = ImageLoader(self)
        self._loader.full_ready.connect(self._on_full_ready)
        self._loader.full_failed.connect(self._on_full_failed)
        self._vault = Vault()
        self._screen_pool = QThreadPool(self)
        self._screen_pool.setMaxThreadCount(4)
        self._screen_signals = ScreenSignals(self)
        self._screen_signals.finished.connect(self._on_screen_action_finished)
        self._screen_signals.failed.connect(self._on_screen_action_failed)
        self._screen_busy: dict[str, tuple[str | None, bool, bool]] = {}
        self._secret_queue: list[str] = []
        self._asking_secret = False
        self._annotations = AnnotationStore()
        self._brush = Brush.from_dict(settings.get("marker"))
        self._receiver: PhoneScreenshotWatcher | None = None
        self._origins = PhoneOrigins()
        self._reconnector: NetworkReconnector | None = None
        self._network_dialog: NetworkDialog | None = None
        self._network_signals = NetworkSignals(self)
        self._network_signals.finished.connect(self._on_network_finished)
        self._network_signals.failed.connect(self._on_network_failed)
        self._delete_signals = DeleteSignals(self)
        self._delete_signals.finished.connect(self._on_phone_deleted)
        self._delete_signals.failed.connect(self._on_phone_delete_failed)
        self._delete_counter = 0
        self._delete_reports: dict[int, DeleteReport] = {}
        self._pending_trash = [
            path for path in self._settings.get("pending_trash") or [] if isinstance(path, str) and os.path.exists(path)
        ]
        self._pending_timer = QTimer(self)
        self._pending_timer.setInterval(4000)
        self._pending_timer.timeout.connect(self._retry_pending_trash)
        self._clip_bridge: ClipboardBridge | None = None
        self._clip_mode = self._settings.get("clipboard_sync", "off")
        if self._clip_mode not in CLIP_MODE_KEYS:
            self._clip_mode = "off"
        self._clip_errors: dict[str, str] = {}
        self._clip_announce = False
        self._clip_too_long = False
        QGuiApplication.clipboard().dataChanged.connect(self._on_pc_clipboard)
        self._build_ui()
        self._bind_shortcuts()
        self._restore_geometry()
        self._update_folder_button()
        self._gallery.hide_paths(self._pending_trash)
        self._gallery.load_folder(self._folder())
        if self._pending_trash:
            self._settings.set("pending_trash", list(self._pending_trash))
            self._pending_timer.start()
        self._show_delete_mode()
        self._start_adb()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QFrame()
        top.setObjectName("topbar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 8, 8, 8)
        top_layout.setSpacing(6)
        self._device_bar = DeviceBar(self._settings.deselected_devices, self._vault.has)
        self._device_bar.selection_changed.connect(self._on_device_selection)
        self._device_bar.capture_only.connect(self._capture_only)
        self._device_bar.unlock_requested.connect(self._unlock_serial)
        self._device_bar.lock_requested.connect(self._lock_serial)
        self._device_bar.forget_secret_requested.connect(self._forget_secret)
        self._device_bar.add_requested.connect(self.open_network_dialog)
        self._device_bar.forget_network_requested.connect(self._forget_network_address)
        self._device_bar.blocked.connect(self._on_blocked_device)
        self._adb_button = tool_button(Glyph.SEARCH, "Pick adb.exe manually", self._pick_adb, text="Find adb.exe")
        self._adb_button.hide()
        self._path_button = tool_button(Glyph.FOLDER, "", self.open_folder, text=" ")
        self._path_button.setObjectName("path")
        self._change_button = tool_button(
            Glyph.MOVE_TO_FOLDER, "Change where screenshots are saved (Ctrl+L)", self.change_folder, text="Change"
        )
        top_layout.addWidget(self._device_bar, 1)
        top_layout.addWidget(self._adb_button)
        top_layout.addWidget(self._path_button)
        top_layout.addWidget(self._change_button)
        top_layout.addWidget(tool_button(Glyph.SETTINGS, "Settings", self.open_settings))

        self._viewer = ImageView(framed=True)
        self._viewer.store = self._annotations
        self._connect_view(self._viewer)
        self._toast = Toast(self._viewer)

        self._gallery = Gallery(self._loader)
        self._gallery.selection_changed.connect(self._show_paths)
        self._gallery.contents_changed.connect(self._on_gallery_changed)
        self._gallery.context_requested.connect(
            lambda position: self._show_image_menu(self._gallery.selected_paths(), position)
        )
        self._gallery.activated_path.connect(lambda _path: self.toggle_fullscreen())
        self._gallery.selection_mode_changed.connect(self._on_selection_mode_changed)

        bottom = QFrame()
        bottom.setObjectName("bottombar")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 10, 12, 10)
        bottom_layout.setSpacing(6)
        self._capture_button = QPushButton("Capture")
        self._capture_button.setObjectName("capture")
        self._capture_button.setIcon(glyph_icon(Glyph.CAMERA, ACCENT_DARK, 18, disabled="#5d7a69"))
        self._capture_button.setIconSize(QSize(18, 18))
        self._capture_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._capture_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._capture_button.setToolTip("Take a screenshot of the selected phones (F5 or Space)")
        self._capture_button.clicked.connect(self.capture)
        self._unlock_button = tool_button(
            Glyph.UNLOCK,
            "Unlock the selected phones (Ctrl+U)\nWakes the screen, opens the PIN pad and types the saved PIN",
            self.unlock_selected,
            text="Unlock",
        )
        self._receive_button = tool_button(Glyph.DOWNLOAD, "", self._toggle_auto_receive, text="Auto-receive")
        self._receive_button.setCheckable(True)
        self._receive_mode_button = tool_button(
            Glyph.CHEVRON_DOWN, "What happens to screenshots taken on the phone", lambda: None, icon_size=12
        )
        self._receive_mode_button.setObjectName("chevron")
        self._receive_mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._receive_menu = QMenu(self)
        self._receive_actions = {}
        receive_group = QActionGroup(self)
        for mode, label in PHONE_SHOT_MODES:
            action = self._receive_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, value=mode: self._set_phone_mode(value))
            action.setCheckable(True)
            receive_group.addAction(action)
            self._receive_actions[mode] = action
        self._receive_mode_button.setMenu(self._receive_menu)
        receive_box = QWidget()
        receive_layout = QHBoxLayout(receive_box)
        receive_layout.setContentsMargins(0, 0, 0, 0)
        receive_layout.setSpacing(0)
        receive_layout.addWidget(self._receive_button)
        receive_layout.addWidget(self._receive_mode_button)
        self._phone_mode = self._settings.get("phone_shots") or (
            "copy" if self._settings.get("auto_receive") else "phone"
        )
        self._show_phone_mode()
        self._auto_copy_button = tool_button(
            Glyph.PASTE,
            "Auto-copy\nEvery new screenshot goes straight to the clipboard",
            self._toggle_auto_copy,
            text="Auto-copy",
        )
        self._auto_copy_button.setCheckable(True)
        self._auto_copy_button.setChecked(bool(self._settings.get("auto_copy", False)))
        self._info_label = ElidedLabel()
        self._info_label.setObjectName("muted")
        self._select_button = tool_button(
            Glyph.MULTISELECT,
            "\n".join(
                (
                    "Select several screenshots",
                    "Click or swipe over thumbnails, then press Delete",
                    "Ctrl+A selects all, Esc stops",
                )
            ),
            self._toggle_select_mode,
            text="Select",
        )
        self._select_button.setCheckable(True)
        self._select_all_button = tool_button(
            Glyph.SELECT_ALL, "Select every screenshot in the folder (Ctrl+A)", self.select_all_or_clear, text="Select all"
        )
        self._select_all_button.hide()
        self._copy_button = tool_button(Glyph.COPY, "Copy image (Ctrl+C)", self.copy_selected)
        self._delete_button = tool_button(
            Glyph.DELETE, "Delete selected images (Delete), they go to the Recycle Bin", self.delete_selected
        )
        self._marker_button = tool_button(
            Glyph.PEN, "Marker: draw on the image in fullscreen (M)", self.open_marker
        )
        self._fullscreen_button = tool_button(Glyph.FULLSCREEN, "Fullscreen (F11 or double-click)", self.toggle_fullscreen)
        self._open_button = tool_button(Glyph.FOLDER_OPEN, "Open the screenshots folder (Ctrl+O)", self.open_folder)
        bottom_layout.addWidget(self._capture_button)
        bottom_layout.addWidget(self._unlock_button)
        bottom_layout.addWidget(receive_box)
        bottom_layout.addWidget(self._auto_copy_button)
        bottom_layout.addSpacing(8)
        bottom_layout.addWidget(self._info_label, 1)
        select_box = QWidget()
        select_layout = QHBoxLayout(select_box)
        select_layout.setContentsMargins(0, 0, 0, 0)
        select_layout.setSpacing(2)
        select_layout.addWidget(self._select_button)
        select_layout.addWidget(self._select_all_button)
        bottom_layout.addWidget(select_box)
        bottom_layout.addWidget(self._copy_button)
        bottom_layout.addWidget(self._delete_button)
        bottom_layout.addWidget(self._marker_button)
        bottom_layout.addWidget(self._fullscreen_button)
        bottom_layout.addWidget(separator())
        bottom_layout.addWidget(self._open_button)

        layout.addWidget(top)
        layout.addWidget(self._viewer, 1)
        layout.addWidget(self._gallery)
        layout.addWidget(bottom)
        self.setCentralWidget(root)
        self._bottom_fit = ResponsiveBar(
            bottom,
            [
                [self._select_all_button, self._select_button, self._auto_copy_button],
                [self._receive_button, self._unlock_button],
                [self._capture_button],
            ],
            {self._info_label: [150, 110, 80, 0]},
        )
        self._top_fit = ResponsiveBar(top, [[self._path_button], [self._change_button]])

    def _bind_shortcuts(self) -> None:
        bindings = [
            (("F5", "Space"), self.capture),
            (("F11", "F", "Return", "Enter"), self.toggle_fullscreen),
            (("Delete",), self.delete_selected),
            (("Ctrl+C",), self.copy_selected),
            (("Ctrl+O",), self.open_folder),
            (("Ctrl+L",), self.change_folder),
            (("Ctrl+U",), self.unlock_selected),
            (("M",), self.open_marker),
            (("Ctrl+A",), self.select_all_or_clear),
            (("Escape",), self._leave_selection),
            (("Left", "Up"), lambda: self._step(-1)),
            (("Right", "Down"), lambda: self._step(1)),
            (("Home",), lambda: self._gallery.go(0)),
            (("End",), lambda: self._gallery.go(-1)),
        ]
        for keys, handler in bindings:
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(handler)

    def _start_adb(self, preferred: str | None = None) -> None:
        self._stop_workers()
        path = find_adb(preferred or self._settings.get("adb_path"))
        self._adb = Adb(path) if path else None
        self._adb_error = None
        if path and preferred:
            self._settings.set("adb_path", path)
        if self._adb is not None:
            self._monitor = DeviceMonitor(self._adb, parent=self)
            self._monitor.devices_changed.connect(self._on_devices)
            self._monitor.failed.connect(self._on_adb_failed)
            self._monitor.start()
            self._receiver = PhoneScreenshotWatcher(self._adb, self._gallery.thumb_bound(), parent=self)
            self._receiver.received.connect(self._on_received)
            self._receiver.failed.connect(self._on_receive_failed)
            self._receiver.start()
            self._reconnector = NetworkReconnector(self._adb, parent=self)
            self._reconnector.start()
            if CLIP_HELPER.is_file():
                self._clip_bridge = ClipboardBridge(self._adb, CLIP_HELPER, self)
                self._clip_bridge.received.connect(self._on_phone_clipboard)
                self._clip_bridge.ready.connect(self._on_clipboard_ready)
                self._clip_bridge.failed.connect(self._on_clipboard_failed)
        self._update_status()
        self._sync_receiver()
        self._sync_network()
        self._sync_clipboard()

    def _stop_workers(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        if self._receiver is not None:
            self._receiver.stop()
            self._receiver = None
        if self._reconnector is not None:
            self._reconnector.stop()
            self._reconnector = None
        if self._clip_bridge is not None:
            self._clip_bridge.stop()
            self._clip_bridge.deleteLater()
            self._clip_bridge = None

    def _sync_receiver(self) -> None:
        if self._receiver is None:
            return
        enabled = self._phone_mode != "phone" and self._settings.save_dir is not None
        targets = {device.serial: device.name for device in self._device_bar.selected()} if enabled else {}
        self._receiver.configure(targets, self._settings.save_dir, move=self._phone_mode == "move")

    def _toggle_auto_receive(self, checked: bool = False) -> None:
        if self._receive_button.isChecked():
            self._set_phone_mode(self._settings.get("last_send_mode", "copy"))
        else:
            self._set_phone_mode("phone")

    def _set_phone_mode(self, mode: str) -> None:
        if mode not in ("phone", "copy", "move"):
            mode = "phone"
        if mode != "phone":
            self._ensure_folder()
            self._settings.set("last_send_mode", mode)
        self._phone_mode = mode
        self._settings.set("phone_shots", mode)
        self._show_phone_mode()
        self._sync_receiver()
        messages = {
            "phone": ("Phone screenshots stay on the phone", "info"),
            "copy": ("Copy to PC  ·  phone screenshots show up here and stay on the phone", "ok"),
            "move": ("Move to PC  ·  phone screenshots come here and are deleted from the phone", "ok"),
        }
        self._notify(*messages[mode], 3000)

    def _show_phone_mode(self) -> None:
        mode = self._phone_mode
        self._receive_button.blockSignals(True)
        self._receive_button.setChecked(mode != "phone")
        self._receive_button.blockSignals(False)
        self._receive_button.setText("Auto-move" if mode == "move" else "Auto-receive")
        tips = {
            "phone": "Off: screenshots taken on the phone stay there\nClick to start receiving them on this PC",
            "copy": "Copy to PC: every screenshot taken on the phone shows up here\nand also stays on the phone",
            "move": "Move to PC: every screenshot taken on the phone comes here\nand is then deleted from the phone",
        }
        self._receive_button.setToolTip(tips[mode] + "\nUse the arrow to choose")
        for key, action in self._receive_actions.items():
            action.setChecked(key == mode)

    def _on_received(self, serial: str, path: str, remote: str, moved: bool, image, thumb) -> None:
        device = self._device(serial)
        name = device.name if device is not None else serial
        if not moved:
            self._origins.remember(path, device.identity if device is not None else serial, remote, name)
        self._loader.remember(path, QPixmap.fromImage(image))
        busy_drawing = self._fullscreen is not None and self._fullscreen.marker_active()
        self._gallery.add_batch([(path, QPixmap.fromImage(thumb))], select=not busy_drawing)
        if not busy_drawing:
            self._show_paths([path], flash=True)
        if moved:
            prefix = f"Moved from {name}"
        elif self._phone_mode == "move":
            prefix = f"From {name}  ·  couldn't delete it on the phone"
        else:
            prefix = f"From {name}"
        self._notify(f"{prefix}  ·  {os.path.basename(path)}" + self._auto_copy([path]), "ok")

    def _toggle_auto_copy(self, checked: bool = False) -> None:
        enabled = self._auto_copy_button.isChecked()
        self._settings.set("auto_copy", enabled)
        if enabled:
            self._notify("Auto-copy on  ·  new screenshots go to the clipboard", "ok", 2600)
        else:
            self._notify("Auto-copy off")

    def _auto_copy(self, paths: list[str]) -> str:
        if not paths or not self._auto_copy_button.isChecked():
            return ""
        return "  ·  copied" if self.copy_paths(paths, quiet=True) else ""

    def _on_receive_failed(self, serial: str, message: str) -> None:
        device = self._device(serial)
        self._notify(f"Auto-receive failed  ·  {device.name if device is not None else serial}: {message}", "error", 4000)

    def _sync_clipboard(self) -> None:
        if self._clip_bridge is None:
            return
        on = self._clip_mode != "off"
        self._clip_bridge.configure([device.serial for device in self._device_bar.selected()] if on else [])

    def _set_clipboard_mode(self, mode: str) -> None:
        if mode not in CLIP_MODE_KEYS:
            mode = "off"
        self._clip_mode = mode
        self._settings.set("clipboard_sync", mode)
        self._clip_errors.clear()
        self._clip_announce = mode != "off"
        self._sync_clipboard()
        messages = {
            "off": ("Clipboard sync off", "info"),
            "both": ("Clipboard sync on  ·  PC ↔ phone", "ok"),
            "to_phone": ("Clipboard sync on  ·  PC → phone", "ok"),
            "to_pc": ("Clipboard sync on  ·  phone → PC", "ok"),
        }
        text, kind = messages[mode]
        if mode != "off" and self._clip_bridge is None:
            text, kind = "Clipboard sync needs adb", "error"
        elif mode != "off" and not self._device_bar.selected():
            text += "  ·  select a phone"
        self._notify(text, kind, 2600)

    def _on_pc_clipboard(self) -> None:
        if self._clip_bridge is None or self._clip_mode not in ("both", "to_phone"):
            return
        text = clipboard_text(QGuiApplication.clipboard().mimeData())
        if not text:
            return
        if len(text) > CLIP_LIMIT:
            if not self._clip_too_long:
                self._clip_too_long = True
                self._notify("Clipboard text is too long to send to the phone", "error", 3000)
            return
        self._clip_too_long = False
        self._clip_bridge.send(text)

    def _on_phone_clipboard(self, serial: str, text: str) -> None:
        if self._clip_mode not in ("both", "to_pc"):
            return
        if text != QGuiApplication.clipboard().text():
            QGuiApplication.clipboard().setText(text)
        device = self._device(serial)
        self._notify(f"Clipboard  ·  from {device.name if device is not None else serial}", "ok", 1600)

    def _on_clipboard_ready(self, serial: str) -> None:
        self._clip_errors.pop(serial, None)
        if self._clip_announce:
            self._clip_announce = False
            device = self._device(serial)
            self._notify(f"Clipboard linked  ·  {device.name if device is not None else serial}", "ok", 2200)

    def _on_clipboard_failed(self, serial: str, message: str) -> None:
        device = self._device(serial)
        if device is None or self._clip_errors.get(serial) == message:
            return
        self._clip_errors[serial] = message
        self._notify(f"Clipboard sync  ·  {device.name}: {message}", "error", 4000)

    def _pick_adb(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Select adb.exe", str(Path.home()), "adb (adb.exe adb);;All files (*)"
        )
        if chosen:
            self._start_adb(chosen)

    def _on_devices(self, devices: list[Device]) -> None:
        self._adb_error = None
        usb_identities = {device.identity for device in devices if not device.network and device.hardware_serial}
        doubled = {
            device.identity: device.transport
            for device in devices
            if device.network and device.ready and device.identity in usb_identities
        }
        devices = [
            replace(device, also_over=doubled[device.identity])
            if not device.network and device.identity in doubled
            else device
            for device in devices
        ]
        saved = {entry["address"] for entry in self._network_devices()}
        devices = [
            device
            for device in devices
            if not device.network
            or (device.identity not in usb_identities and (device.ready or device.serial in saved))
        ]
        names = {entry["address"]: entry.get("name") for entry in self._network_devices()}
        devices = [
            replace(device, market_name=names[device.serial])
            if device.network and not device.ready and names.get(device.serial) and not device.market_name
            else device
            for device in devices
        ]
        self._remember_network_names(devices)
        arrived = [device for device in devices if device.ready and device.serial not in self._ready_serials]
        self._ready_serials = {device.serial for device in devices if device.ready}
        self._devices = devices
        self._device_bar.set_devices(devices)
        self._update_status()
        self._sync_receiver()
        self._sync_clipboard()
        if arrived and not self._first_scan:
            self._notify("Connected  ·  " + ", ".join(device.name for device in arrived), "ok")
        self._first_scan = False

    def _on_adb_failed(self, message: str) -> None:
        self._adb_error = message
        self._devices = []
        self._ready_serials = set()
        self._device_bar.set_devices([])
        self._update_status()
        self._sync_clipboard()

    def _on_device_selection(self) -> None:
        self._settings.deselected_devices = self._device_bar.deselected()
        self._update_status()
        self._sync_receiver()
        self._sync_clipboard()

    def _on_blocked_device(self, device: Device) -> None:
        if device.state == "unauthorized":
            self._notify(f"{device.name}: tap “Allow USB debugging” on the phone", "error", 3500)
        else:
            self._notify(f"{device.name}: {state_text(device)}, unplug and replug the cable", "error", 3500)

    def _placeholder_text(self) -> tuple[str, str]:
        if self._adb is None:
            return "adb not found", "Install Android SDK Platform-Tools or click “Find adb.exe” above."
        if self._adb_error:
            return "adb is not working", self._adb_error
        if not self._devices:
            return (
                "Connect a phone over USB",
                "Turn on USB debugging in Developer options and accept the prompt on the phone.",
            )
        if not any(device.ready for device in self._devices):
            if any(device.state == "unauthorized" for device in self._devices):
                return "Accept the prompt on the phone", "Tap “Allow USB debugging” on the phone screen."
            return "Phone is not ready", "Unplug and replug the cable."
        selected = self._device_bar.selected()
        if not selected:
            return "Select a phone", "Click a phone above to include it in captures."
        if len(selected) == 1:
            device = selected[0]
            detail = f"Press Capture or F5  ·  {device.name}"
            if device.locked:
                detail += "  ·  locked, the lock screen will be captured"
            return "Ready", detail
        return "Ready", f"Press Capture or F5  ·  captures {len(selected)} phones at once"

    def _update_status(self) -> None:
        title, detail = self._placeholder_text()
        for view in self._views():
            view.set_placeholder(title, detail)
        selected = self._device_bar.selected()
        busy = self._batch is not None
        self._capture_button.setEnabled(bool(selected) and not busy and self._adb is not None)
        self._unlock_button.setEnabled(bool(selected) and self._adb is not None)
        if not busy:
            self._bottom_fit.set_text(self._capture_button, "Capture" if len(selected) <= 1 else f"Capture  ×{len(selected)}")
        has_images = bool(self._selected_paths)
        self._copy_button.setEnabled(has_images)
        self._delete_button.setEnabled(has_images)
        self._adb_button.setVisible(self._adb is None)

    def capture(self) -> None:
        self._start_capture(self._device_bar.selected())

    def _capture_only(self, serial: str) -> None:
        device = next((device for device in self._devices if device.serial == serial and device.ready), None)
        if device is not None:
            self._start_capture([device])

    def _start_capture(self, devices: list[Device]) -> None:
        if self._batch is not None:
            return
        if self._adb is None or not devices:
            title, _detail = self._placeholder_text()
            self._notify(title if title != "Ready" else "No phone selected", "error")
            return
        folder = self._ensure_folder()
        stamp = datetime.now().strftime(TIMESTAMP_FORMAT)
        targets = {
            device.serial: reserve_path(folder, f"Screenshot_{stamp}_{safe_name(device.name)}", ".png")
            for device in devices
        }
        self._batch_counter += 1
        self._batch = CaptureBatch(self._batch_counter, devices, targets)
        self._update_status()
        self._show_capture_progress()
        bound = self._gallery.thumb_bound()
        for device in devices:
            self._capture_pool.start(
                CaptureTask(
                    self._batch.number,
                    self._adb,
                    device.serial,
                    targets[device.serial],
                    bound,
                    self._capture_signals,
                )
            )


    def _show_capture_progress(self) -> None:
        batch = self._batch
        total = len(batch.devices)
        self._bottom_fit.set_text(self._capture_button, "Capturing…" if total == 1 else f"Capturing {batch.finished}/{total}…")

    def _on_captured(self, number: int, serial: str, path: str, image, thumb) -> None:
        if self._batch is None or number != self._batch.number:
            return
        self._batch.results[serial] = (path, image, thumb)
        self._advance_batch()

    def _on_capture_failed(self, number: int, serial: str, message: str) -> None:
        if self._batch is None or number != self._batch.number:
            return
        self._batch.errors[serial] = message
        self._advance_batch()

    def _advance_batch(self) -> None:
        batch = self._batch
        if not batch.done:
            self._show_capture_progress()
            return
        self._batch = None
        saved = [(device, *batch.results[device.serial]) for device in batch.devices if device.serial in batch.results]
        failed = [(device, batch.errors[device.serial]) for device in batch.devices if device.serial in batch.errors]
        entries = []
        for _device, path, image, thumb in saved:
            self._loader.remember(path, QPixmap.fromImage(image))
            entries.append((path, QPixmap.fromImage(thumb)))
        if entries:
            self._gallery.add_batch(entries)
            self._show_paths([path for path, _thumb in entries], flash=True)
        self._update_status()
        copied = self._auto_copy([path for path, _thumb in entries])
        if failed:
            problems = ", ".join(f"{device.name}: {error}" for device, error in failed)
            prefix = f"Saved {len(saved)}, failed  ·  " if saved else "Failed  ·  "
            self._notify(prefix + problems, "error", 5000)
        elif len(saved) == 1:
            self._notify(f"Saved  ·  {os.path.basename(saved[0][1])}" + copied, "ok")
        else:
            self._notify(f"Saved {len(saved)} {images_word(len(saved))}" + copied, "ok")

    def _show_paths(self, paths: list[str], flash: bool = False) -> None:
        self._selected_paths = list(paths)
        shown = [path for path in self._selected_paths if path not in self._broken][:MAX_SIDE_BY_SIDE]
        pixmaps = [self._loader.cached(path) for path in shown]
        missing = [path for path, pixmap in zip(shown, pixmaps) if pixmap is None]
        for path in missing:
            self._loader.request_full(path)
        if not missing:
            labels = [self._label_for(path) for path in shown]
            self._displayed_paths = shown
            for view in self._views():
                view.set_images(pixmaps, labels, shown)
                if flash:
                    view.flash()
        self._update_info()
        self._update_status()
        self._sync_fullscreen_counter()
        self._update_select_all_button()

    def _on_full_ready(self, path: str, _pixmap) -> None:
        if path in self._selected_paths:
            self._show_paths(self._selected_paths)

    def _on_full_failed(self, path: str) -> None:
        self._broken.add(path)
        if path in self._selected_paths:
            self._notify(f"Can't open  ·  {os.path.basename(path)}", "error")
            self._show_paths(self._selected_paths)

    @staticmethod
    def _label_for(path: str) -> str:
        stem = Path(path).stem
        match = FILE_PATTERN.match(stem)
        return match["device"].replace("-", " ") if match else stem

    def _on_gallery_changed(self) -> None:
        self._update_info()
        self._sync_fullscreen_counter()
        self._update_select_all_button()

    def _update_info(self) -> None:
        paths = self._selected_paths
        count = self._gallery.count()
        if not paths:
            text = f"{count} {images_word(count)} in folder" if count else "Folder is empty"
        elif len(paths) == 1:
            path = paths[0]
            parts = [os.path.basename(path)]
            size = QImageReader(path).size()
            if size.isValid():
                parts.append(f"{size.width()}×{size.height()}")
            try:
                parts.append(human_size(os.path.getsize(path)))
            except OSError:
                pass
            position, _count = self._gallery.position()
            parts.append(f"{position}/{count}")
            if self._annotations.has(path):
                parts.append("marked, not saved")
            text = "   ·   ".join(parts)
        else:
            total = 0
            for path in paths:
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
            text = f"{len(paths)} {images_word(len(paths))} selected   ·   {human_size(total)}"
            if len(paths) > MAX_SIDE_BY_SIDE:
                text += f"   ·   showing the first {MAX_SIDE_BY_SIDE}"
        self._info_label.set_full_text(text)

    def _step(self, delta: int) -> None:
        self._gallery.step(delta)

    def _device(self, serial: str) -> Device | None:
        return next((device for device in self._devices if device.serial == serial), None)

    def unlock_selected(self) -> None:
        devices = self._device_bar.selected()
        if self._adb is None or not devices:
            self._notify("No phone selected", "error")
            return
        for device in devices:
            self._start_unlock(device)

    def _unlock_serial(self, serial: str) -> None:
        device = self._device(serial)
        if device is not None and device.ready:
            self._start_unlock(device)

    def _vault_key(self, serial: str) -> str:
        device = self._device(serial)
        return device.identity if device is not None else serial

    def _start_unlock(self, device: Device, secret: str | None = None, remember: bool = False) -> None:
        if self._adb is None or device.serial in self._screen_busy:
            return
        from_vault = False
        if secret is None and self._vault.has(device.identity):
            secret = self._vault.get(device.identity)
            from_vault = secret is not None
        self._screen_busy[device.serial] = (secret, remember, from_vault)
        self._screen_pool.start(UnlockTask(self._adb, device.serial, secret, self._screen_signals))
        self._notify(f"Unlocking  ·  {device.name}")

    def _lock_serial(self, serial: str) -> None:
        if self._adb is None or self._device(serial) is None or serial in self._screen_busy:
            return
        self._screen_busy[serial] = (None, False, False)
        self._screen_pool.start(LockTask(self._adb, serial, self._screen_signals))

    def _forget_secret(self, serial: str) -> None:
        self._vault.forget(self._vault_key(serial))
        self._notify("Saved PIN removed", "ok")

    def _on_screen_action_finished(self, serial: str, outcome: str) -> None:
        secret, remember, from_vault = self._screen_busy.pop(serial, (None, False, False))
        device = self._device(serial)
        name = device.name if device is not None else serial
        if self._monitor is not None:
            self._monitor.poke()
        if outcome == "locked":
            self._notify(f"Locked  ·  {name}")
        elif outcome == UnlockOutcome.ALREADY.value:
            self._notify(f"{name} is already unlocked", "ok")
        elif outcome == UnlockOutcome.UNLOCKED.value:
            stored = bool(remember and secret and self._vault.set(self._vault_key(serial), secret))
            self._notify(f"Unlocked  ·  {name}" + ("  ·  PIN saved" if stored else ""), "ok")
        elif outcome == UnlockOutcome.NEEDS_SECRET.value:
            self._queue_secret_prompt(serial)
        elif outcome == UnlockOutcome.REJECTED.value:
            if from_vault:
                self._vault.forget(self._vault_key(serial))
                self._notify(f"Saved PIN was rejected  ·  {name}  ·  removed, enter it again", "error", 4500)
            else:
                self._notify(f"PIN rejected  ·  {name}", "error", 3500)

    def _on_screen_action_failed(self, serial: str, message: str) -> None:
        self._screen_busy.pop(serial, None)
        device = self._device(serial)
        self._notify(f"{device.name if device is not None else serial}: {message}", "error", 4000)

    def _queue_secret_prompt(self, serial: str) -> None:
        if serial not in self._secret_queue:
            self._secret_queue.append(serial)
        if not self._asking_secret:
            self._next_secret_prompt()

    def _next_secret_prompt(self) -> None:
        while self._secret_queue:
            device = self._device(self._secret_queue.pop(0))
            if device is None or not device.ready:
                continue
            self._asking_secret = True
            dialog = SecretDialog(device.name, self._vault.persistent, self._fullscreen or self)
            accepted = bool(dialog.exec())
            secret, remember = dialog.secret(), dialog.remember()
            dialog.deleteLater()
            self._asking_secret = False
            if accepted and secret:
                self._start_unlock(device, secret, remember)

    def _connect_view(self, view: ImageView) -> None:
        view.double_clicked.connect(self.toggle_fullscreen)
        view.navigate.connect(self._step)
        view.context_requested.connect(self._on_view_context)
        view.drag_requested.connect(lambda index, source=view: self._drag_from_view(source, index))

    def _on_view_context(self, index: int, position) -> None:
        if 0 <= index < len(self._displayed_paths):
            self._show_image_menu([self._displayed_paths[index]], position)

    def _drag_from_view(self, view: ImageView, index: int) -> None:
        pixmaps = view.pixmaps()
        if not (0 <= index < len(self._displayed_paths) and index < len(pixmaps)):
            return
        ratio = view.devicePixelRatioF()
        preview = pixmaps[index].scaledToHeight(round(160 * ratio), Qt.TransformationMode.SmoothTransformation)
        preview.setDevicePixelRatio(ratio)
        drag = QDrag(view)
        drag.setMimeData(file_mime([self._displayed_paths[index]]))
        drag.setPixmap(preview)
        drag.exec(Qt.DropAction.CopyAction)

    def _show_image_menu(self, paths: list[str], position) -> None:
        if not paths:
            return
        count = len(paths)
        menu = QMenu(self._fullscreen or self)
        menu.addAction(
            glyph_icon(Glyph.COPY, size=16),
            "Copy image  (Ctrl+C)" if count == 1 else f"Copy {count} {images_word(count)}",
            lambda: self.copy_paths(paths),
        )
        menu.addAction(
            glyph_icon(Glyph.LINK, size=16),
            "Copy path" if count == 1 else "Copy paths",
            lambda: self._copy_text("\n".join(paths)),
        )
        menu.addAction(glyph_icon(Glyph.FOLDER_OPEN, size=16), "Show in folder", lambda: self._reveal(paths[0]))
        if self._gallery.count() > 1 and not self._all_selected():
            menu.addAction(glyph_icon(Glyph.SELECT_ALL, size=16), "Select all  (Ctrl+A)", self.select_all_or_clear)
        if self._fullscreen is None:
            menu.addAction(glyph_icon(Glyph.FULLSCREEN, size=16), "Fullscreen  (F11)", self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction(
            glyph_icon(Glyph.DELETE, size=16),
            "Delete" if count == 1 else f"Delete {count} {images_word(count)}",
            lambda: self.delete_paths(paths),
        )
        menu.exec(position)

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self._notify("Path copied", "ok")

    def _reveal(self, path: str) -> None:
        if sys.platform == "win32" and os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _toggle_select_mode(self, checked: bool = False) -> None:
        enabled = self._select_button.isChecked()
        self._gallery.set_selection_mode(enabled)
        if enabled:
            self._notify("Click or swipe over thumbnails to select  ·  Delete removes them all", "info", 2600)

    def _on_selection_mode_changed(self, enabled: bool) -> None:
        self._select_button.blockSignals(True)
        self._select_button.setChecked(enabled)
        self._select_button.blockSignals(False)
        self._select_all_button.setVisible(enabled)
        self._update_select_all_button()

    def _all_selected(self) -> bool:
        count = self._gallery.count()
        return count > 0 and len(self._selected_paths) == count

    def _update_select_all_button(self) -> None:
        if self._all_selected():
            self._select_all_button.setText("Clear")
            self._select_all_button.setToolTip("Deselect all screenshots")
        else:
            self._select_all_button.setText("Select all")
            self._select_all_button.setToolTip("Select every screenshot in the folder (Ctrl+A)")

    def select_all_or_clear(self) -> None:
        if self._all_selected() and self._gallery.selection_mode:
            self._gallery.clearSelection()
        else:
            self._gallery.select_all()
            self._notify(f"Selected all {self._gallery.count()}  ·  Delete removes them, Esc stops", "info", 2400)

    def _leave_selection(self) -> None:
        if self._gallery.selection_mode:
            self._gallery.set_selection_mode(False)
        elif len(self._selected_paths) > 1:
            self._gallery.go(max(0, self._gallery.currentRow()))

    def _trash(self, paths: list[str]) -> TrashOutcome:
        self._loader.wait_idle(1500)
        outcome = TrashOutcome()
        retry = []
        for path in paths:
            if not os.path.exists(path) or move_to_trash(path):
                outcome.deleted.append(path)
            else:
                retry.append(path)
        if retry:
            time.sleep(0.3)
        for path in retry:
            if not os.path.exists(path) or move_to_trash(path):
                outcome.deleted.append(path)
                continue
            freed = False
            for _attempt in range(3):
                apps = release_file(path)
                for app in apps:
                    if app not in outcome.released:
                        outcome.released.append(app)
                if apps and (move_to_trash(path) or not os.path.exists(path)):
                    freed = True
                    break
                if not apps:
                    break
                time.sleep(0.15)
            if freed:
                outcome.deleted.append(path)
            elif not locking_processes(path) and remove_for_good(path):
                outcome.forever.append(path)
            else:
                outcome.stuck.append(path)
                for _pid, app in locking_processes(path):
                    if app and app not in outcome.holders:
                        outcome.holders.append(app)
        return outcome

    def _show_delete_mode(self) -> None:
        descriptions = {
            "ask": "asks PC only or PC + phone",
            "pc": "PC only, set in Settings",
            "both": "PC and phone, set in Settings",
        }
        mode = self._settings.get("delete_mode", "ask")
        self._delete_button.setToolTip(f"Delete selected (Delete)  ·  {descriptions.get(mode, descriptions['ask'])}")

    def delete_selected(self) -> None:
        self.delete_paths(self._selected_paths)

    def delete_paths(self, paths: list[str]) -> None:
        paths = list(paths)
        if not paths:
            return
        on_phone = {path: self._origins.get(path) for path in paths if self._origins.get(path)}
        mode = self._settings.get("delete_mode", "ask")
        if mode == "ask":
            devices = sorted({origin.get("device") or origin["serial"] for origin in on_phone.values()})
            dialog = DeleteDialog(len(paths), len(on_phone), devices, self._fullscreen or self)
            accepted = bool(dialog.exec())
            choice, remember = dialog.choice, dialog.remember()
            dialog.deleteLater()
            if not accepted or choice is None:
                return
            if remember:
                self._settings.set("delete_mode", choice)
                self._show_delete_mode()
            also_phone = choice == "both"
        else:
            also_phone = mode == "both"
        outcome = self._trash(paths)
        gone = outcome.deleted + outcome.forever + outcome.stuck
        for path in gone:
            self._loader.forget(path)
            self._annotations.forget(path)
        if outcome.stuck:
            self._queue_pending_trash(outcome.stuck)
        self._gallery.remove_paths(gone)
        self._gallery.set_selection_mode(False)
        phone_jobs = {path: on_phone[path] for path in paths if path in on_phone} if also_phone else {}
        self._origins.forget(gone + list(phone_jobs))
        self._delete_counter += 1
        report = DeleteReport(self._delete_counter, len(paths), outcome, also_phone, len(phone_jobs))
        if phone_jobs:
            self._delete_on_phones(report, phone_jobs)
        if not report.waiting:
            self._finish_delete(report)

    def _delete_on_phones(self, report: DeleteReport, jobs: dict[str, dict]) -> None:
        by_serial: dict[str, list[str]] = {}
        for origin in jobs.values():
            by_serial.setdefault(origin["serial"], []).append(origin["remote"])
        live = {}
        for device in self._devices:
            if device.ready:
                live.setdefault(device.serial, device.serial)
                live.setdefault(device.identity, device.serial)
        for key, remotes in by_serial.items():
            if self._adb is None or key not in live:
                report.offline += len(remotes)
                continue
            serial = live[key]
            report.waiting.add(serial)
            self._screen_pool.start(PhoneDeleteTask(report.number, self._adb, serial, remotes, self._delete_signals))
        if report.waiting:
            self._delete_reports[report.number] = report

    def _on_phone_deleted(self, number: int, serial: str, removed: int, total: int) -> None:
        report = self._delete_reports.get(number)
        if report is None:
            return
        device = self._device(serial)
        name = device.name if device is not None else "the phone"
        if removed and name not in report.phone_names:
            report.phone_names.append(name)
        report.phone_removed += removed
        report.phone_missed += total - removed
        self._phone_part_done(report, serial)

    def _on_phone_delete_failed(self, number: int, serial: str, message: str) -> None:
        report = self._delete_reports.get(number)
        if report is None:
            return
        report.phone_errors.append(message)
        self._phone_part_done(report, serial)

    def _phone_part_done(self, report: DeleteReport, serial: str) -> None:
        report.waiting.discard(serial)
        if not report.waiting:
            self._delete_reports.pop(report.number, None)
            self._finish_delete(report)

    def _finish_delete(self, report: DeleteReport) -> None:
        text, kind = delete_summary(report)
        self._notify(text, kind, 2200 if kind != "error" and len(text) < 60 else 4200)

    def _queue_pending_trash(self, paths: list[str]) -> None:
        for path in paths:
            if path not in self._pending_trash:
                self._pending_trash.append(path)
        self._settings.set("pending_trash", list(self._pending_trash))
        self._gallery.hide_paths(paths)
        if not self._pending_timer.isActive():
            self._pending_timer.start()

    def _retry_pending_trash(self) -> None:
        freed = []
        for path in list(self._pending_trash):
            if not os.path.exists(path) or move_to_trash(path):
                freed.append(path)
            elif release_file(path) and (move_to_trash(path) or not os.path.exists(path)):
                freed.append(path)
        if freed:
            self._pending_trash = [path for path in self._pending_trash if path not in freed]
            self._settings.set("pending_trash", list(self._pending_trash))
            self._gallery.unhide_paths(freed)
            count = len(freed)
            self._notify(
                "Deleted on PC now  ·  the file is free" if count == 1 else f"Deleted {count} on PC now  ·  the files are free",
                "ok",
                2600,
            )
        if not self._pending_trash:
            self._pending_timer.stop()

    def open_settings(self) -> None:
        values = {
            "phone_shots": self._phone_mode,
            "delete_mode": self._settings.get("delete_mode", "ask"),
            "clipboard_sync": self._clip_mode,
        }
        network = {
            "enabled": self._settings.get("tailscale_enabled", True),
            "saved": self._network_devices(),
            "on_forget": self._forget_network_address,
            "on_setup": self.open_network_dialog,
        }
        dialog = SettingsDialog(values, self._on_setting_changed, self._fullscreen or self, network)
        dialog.exec()
        dialog.deleteLater()

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "phone_shots":
            self._set_phone_mode(value)
        elif key == "delete_mode":
            self._settings.set(key, value)
            self._show_delete_mode()
        elif key == "clipboard_sync":
            self._set_clipboard_mode(value)
        elif key == "tailscale_enabled":
            self._settings.set(key, bool(value))
            self._sync_network()
            if not value and self._adb is not None:
                for entry in self._network_devices():
                    self._screen_pool.start(CallTask(self._adb.disconnect, entry["address"]))
            self._notify("Tailscale on  ·  saved phones reconnect by themselves" if value else "Tailscale off", "ok" if value else "info")
        else:
            self._settings.set(key, value)

    def _network_devices(self) -> list[dict]:
        entries = self._settings.get("network_devices") or []
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("address")]

    def _save_network_device(self, address: str, identity: str, name: str) -> None:
        entries = [entry for entry in self._network_devices() if entry["address"] != address]
        if identity:
            entries = [entry for entry in entries if entry.get("identity") != identity]
        entries.append({"address": address, "identity": identity, "name": name})
        self._settings.set("network_devices", entries)
        self._settings.set("tailscale_enabled", True)
        self._sync_network()

    def _remember_network_names(self, devices: list[Device]) -> None:
        entries = self._network_devices()
        changed = False
        for device in devices:
            if not device.network or not device.ready:
                continue
            for entry in entries:
                if entry["address"] == device.serial and (
                    entry.get("name") != device.name or entry.get("identity") != device.identity
                ):
                    entry["name"] = device.name
                    entry["identity"] = device.identity
                    changed = True
        if changed:
            self._settings.set("network_devices", entries)

    def _forget_network_address(self, address: str) -> None:
        entries = [entry for entry in self._network_devices() if entry["address"] != address]
        self._settings.set("network_devices", entries)
        self._sync_network()
        if self._adb is not None:
            self._screen_pool.start(CallTask(self._adb.disconnect, address))
        if self._monitor is not None:
            self._monitor.poke()
        self._notify(f"Forgot {address}")

    def _sync_network(self) -> None:
        if self._reconnector is None:
            return
        enabled = self._settings.get("tailscale_enabled", True)
        self._reconnector.set_addresses([entry["address"] for entry in self._network_devices()] if enabled else [])

    def open_network_dialog(self) -> None:
        if self._network_dialog is not None:
            self._network_dialog.raise_()
            return
        usb = [(device.serial, device.name) for device in self._devices if device.ready and not device.network]
        dialog = NetworkDialog(usb, tailscale_peers(), find_tailscale() is not None, self._fullscreen or self)
        dialog.go_wireless_requested.connect(self._go_wireless)
        dialog.connect_requested.connect(self._connect_address)
        self._network_dialog = dialog
        dialog.exec()
        self._network_dialog = None
        dialog.deleteLater()

    def _network_status(self, text: str, kind: str) -> None:
        if self._network_dialog is not None:
            self._network_dialog.set_status(text, kind)
        else:
            self._notify(text, "error" if kind == "error" else "ok", 3500)

    def _go_wireless(self, serial: str) -> None:
        if self._adb is None:
            self._network_status("adb not found", "error")
            return
        hosts = [peer.ip for peer in tailscale_peers() if peer.online and peer.os == "android"]
        self._screen_pool.start(NetworkTask(self._adb, "wireless", serial, self._network_signals, hosts))

    def _connect_address(self, text: str) -> None:
        address = normalize_address(text)
        if self._adb is None or not address:
            self._network_status("Type an address like 100.101.102.103", "error")
            return
        self._screen_pool.start(NetworkTask(self._adb, "connect", address, self._network_signals))

    def _on_network_finished(self, action: str, key: str, address: str) -> None:
        source = self._device(key) if action == "wireless" else None
        name = source.name if source is not None else address
        identity = source.identity if source is not None else ""
        self._save_network_device(address, identity, name)
        if self._monitor is not None:
            self._monitor.poke()
        if action == "wireless":
            self._network_status(f"Ready  ·  {name} works over Tailscale at {address}. You can unplug the cable.", "ok")
        else:
            self._network_status(f"Connected to {address}", "ok")

    def _on_network_failed(self, action: str, key: str, message: str) -> None:
        self._network_status(message[:1].upper() + message[1:], "error")

    def copy_selected(self) -> None:
        self.copy_paths(self._selected_paths)

    def copy_paths(self, paths: list[str], quiet: bool = False) -> bool:
        paths = [path for path in paths if os.path.isfile(path)]
        if not paths:
            return False
        pixmaps = []
        marked = False
        for path in paths[:MAX_SIDE_BY_SIDE]:
            pixmap = self._full_pixmap(path)
            if pixmap is None:
                continue
            if self._annotations.has(path):
                pixmap = QPixmap.fromImage(render_annotated(pixmap, self._annotations.strokes(path)))
                marked = True
            pixmaps.append(pixmap)
        if not pixmaps:
            if not quiet:
                self._notify("The image can't be opened", "error")
            return False
        image = pixmaps[0].toImage() if len(pixmaps) == 1 else side_by_side(pixmaps)
        if marked:
            QGuiApplication.clipboard().setImage(image)
        else:
            QGuiApplication.clipboard().setMimeData(file_mime(paths, image))
        if quiet:
            return True
        if marked:
            self._notify("Copied with your marks", "ok")
        elif len(paths) == 1:
            self._notify("Copied  ·  paste it as an image or as a file", "ok")
        else:
            self._notify(f"Copied {len(paths)} {images_word(len(paths))}  ·  paste as files or as one image", "ok")
        return True

    def _full_pixmap(self, path: str) -> QPixmap | None:
        pixmap = self._loader.cached(path)
        if pixmap is not None:
            return pixmap
        image = QImageReader(path).read()
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        self._loader.remember(path, pixmap)
        return pixmap

    def open_marker(self) -> None:
        if self._fullscreen is None:
            self.toggle_fullscreen(marker=True)
        elif not self._fullscreen.marker_active():
            self._fullscreen.toggle_marker()

    def save_marks(self) -> None:
        if len(self._displayed_paths) != 1:
            self._notify("Select a single image to save its marks", "error")
            return
        source = self._displayed_paths[0]
        if not self._annotations.has(source):
            self._notify("Nothing to save yet  ·  draw on the image first", "error")
            return
        pixmap = self._full_pixmap(source)
        if pixmap is None:
            self._notify("The image can't be opened", "error")
            return
        image = render_annotated(pixmap, self._annotations.strokes(source))
        original = Path(source)
        target = reserve_path(original.parent, f"{original.stem}_marked", ".png")
        try:
            saved = image.save(str(target), "PNG")
        finally:
            release_path(target)
        if not saved:
            self._notify("Saving failed", "error")
            return
        self._annotations.forget(source)
        self._loader.remember(str(target), QPixmap.fromImage(image))
        thumb = image.scaled(
            self._gallery.thumb_bound(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        if self._gallery.folder is not None and Path(self._gallery.folder) == original.parent:
            self._gallery.add_batch([(str(target), QPixmap.fromImage(thumb))])
        self._show_paths([str(target)], flash=True)
        if self._fullscreen is not None:
            self._fullscreen.refresh()
        self._notify(f"Saved  ·  {target.name}", "ok")

    def _on_annotation_changed(self, _key: str) -> None:
        self._viewer.update()
        self._update_info()

    def _folder(self) -> Path:
        return self._settings.save_dir or default_folder()

    def _ensure_folder(self) -> Path:
        saved = self._settings.save_dir
        if saved is not None:
            return saved
        folder = self._ask_folder("Where should screenshots be saved? (remembered)") or default_folder()
        self._set_folder(folder)
        return folder

    def _ask_folder(self, title: str) -> Path | None:
        current = self._folder()
        start = current if current.is_dir() else current.parent
        chosen = QFileDialog.getExistingDirectory(self._fullscreen or self, title, str(start))
        return Path(chosen) if chosen else None

    def change_folder(self) -> None:
        chosen = self._ask_folder("Choose a new folder for screenshots")
        if chosen is None:
            return
        self._set_folder(chosen)
        self._notify(f"New location  ·  {chosen}", "ok", 2600)

    def _set_folder(self, folder: Path) -> None:
        self._settings.save_dir = folder
        self._update_folder_button()
        if self._gallery.folder != folder:
            self._gallery.load_folder(folder)

    def _update_folder_button(self) -> None:
        folder = self._folder()
        text = self._path_button.fontMetrics().elidedText(str(folder), Qt.TextElideMode.ElideMiddle, 300)
        self._path_button.setText(text)
        self._path_button.setToolTip(f"{folder}\nClick to open the folder (Ctrl+O)")

    def open_folder(self) -> None:
        folder = self._folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._notify(f"Folder is not available: {error}", "error", 3500)
            return
        current = self._selected_paths[0] if self._selected_paths else None
        if current and os.path.isfile(current):
            self._reveal(current)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def toggle_fullscreen(self, marker: bool = False) -> None:
        if self._fullscreen is not None:
            self._fullscreen.close_animated()
            return
        viewer = FullscreenViewer(self._brush, self._annotations, self)
        viewer.setWindowIcon(self.windowIcon())
        viewer.view.set_placeholder(*self._placeholder_text())
        viewer.view.set_images(self._viewer.pixmaps(), self._viewer.labels(), self._viewer.keys(), animate=False)
        viewer.closed.connect(self._on_fullscreen_closed)
        viewer.navigate.connect(self._step)
        viewer.view.context_requested.connect(self._on_view_context)
        viewer.view.drag_requested.connect(lambda index, source=viewer.view: self._drag_from_view(source, index))
        viewer.capture_requested.connect(self.capture)
        viewer.delete_requested.connect(self.delete_selected)
        viewer.copy_requested.connect(self.copy_selected)
        viewer.save_marks_requested.connect(self.save_marks)
        viewer.annotation_changed.connect(self._on_annotation_changed)
        viewer.brush_changed.connect(lambda data: self._settings.set("marker", data))
        self._fullscreen = viewer
        self._sync_fullscreen_counter()
        viewer.open_on(self.screen(), marker=bool(marker))

    def _on_fullscreen_closed(self) -> None:
        self._fullscreen = None
        self.activateWindow()

    def _sync_fullscreen_counter(self) -> None:
        if self._fullscreen is not None:
            position, count = self._gallery.position()
            self._fullscreen.set_counter(position, count, len(self._selected_paths))

    def _views(self) -> list[ImageView]:
        views = [self._viewer]
        if self._fullscreen is not None:
            views.append(self._fullscreen.view)
        return views

    def _notify(self, text: str, kind: str = "info", duration: int = 2000) -> None:
        toast = self._fullscreen.toast if self._fullscreen is not None else self._toast
        toast.show_message(text, kind, duration)

    def _restore_geometry(self) -> None:
        stored = self._settings.get("geometry")
        if stored and self.restoreGeometry(QByteArray.fromBase64(stored.encode("ascii"))):
            return
        available = self.screen().availableGeometry()
        width = min(1040, int(available.width() * 0.85))
        height = min(940, int(available.height() * 0.9))
        self.resize(width, height)
        self.move(available.center().x() - width // 2, available.center().y() - height // 2)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._title_bar_styled:
            style_title_bar(self)
            self._title_bar_styled = True

    def closeEvent(self, event):
        self._settings.set("geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        if self._fullscreen is not None:
            self._fullscreen.close()
        self._stop_workers()
        super().closeEvent(event)
