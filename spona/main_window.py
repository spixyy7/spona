import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QFile, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QActionGroup, QDesktopServices, QDrag, QGuiApplication, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from . import __version__
from .adb import Adb, Device, UnlockOutcome, find_adb, normalize_address
from .devices import DeviceBar, state_text
from .dialogs import CLIPBOARD_MODES, PC_DEFAULTS, PHONE_SHOT_MODES, DeleteDialog, NetworkDialog, SecretDialog, SettingsDialog
from .link import MAX_PAYLOAD, Link
from .sharing import FILE_LIMIT, TEXT_LIMIT, Blobs, PrepareSignals, PrepareTask, clipboard_payload, digest, inside, tidy_folder
from .origins import PhoneOrigins
from .gallery import Gallery
from .icons import Glyph, glyph_icon
from .collage import COPY_LAYOUTS, LAYOUT_NAMES
from .images import ImageLoader, file_mime
from .settings import APP_NAME, Settings
from .theme import ACCENT, ACCENT_DARK, style_title_bar
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
    CopySignals,
    CopyTask,
    DeviceMonitor,
    LockTask,
    NetworkReconnector,
    NetworkSignals,
    NetworkTask,
    DeleteSignals,
    JobSignals,
    JobTask,
    PhoneDeleteTask,
    PhoneScreenshotWatcher,
    ScreenSignals,
    ShotSignals,
    ShotTask,
    UnlockTask,
    grab_screen,
    lock_screen,
    unlock_screen,
)

MAX_SIDE_BY_SIDE = 8
TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
CLIP_HELPER = Path(__file__).with_name("clip_helper.dex")
CLIP_LIMIT = 200_000
CLIP_MODE_KEYS = tuple(mode for mode, _label in CLIPBOARD_MODES)
COPY_LAYOUT_KEYS = tuple(mode for mode, _label in COPY_LAYOUTS)
COPY_FOLDER = Path(tempfile.gettempdir()) / APP_NAME / "copies"
COPY_GLYPHS = {"grid": Glyph.GRID, "row": Glyph.COLUMNS, "files": Glyph.PICTURES}
COPY_AS = {"grid": "Copy as a grid", "row": "Copy side by side", "files": "Copy as separate images"}
RECEIVED_FOLDER = Path(tempfile.gettempdir()) / APP_NAME / "received"
RELAY_FOLDER = Path(tempfile.gettempdir()) / APP_NAME / "relay"
REQUEST_SECONDS = 45.0
SHOT_MEMORY_SECONDS = 1800.0
CLIP_REPEAT_SECONDS = 3.0
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


def file_time(path: str) -> tuple[float, str]:
    try:
        return os.path.getmtime(path), path
    except OSError:
        return 0.0, path


def copied_text(count: int, layout: str, marked: int, rows) -> str:
    if layout == "files":
        text = f"Copied {count} separate images  ·  paste them into a chat or a folder"
    elif layout == "grid" and rows and len(rows) > 1:
        text = f"Copied {count} images as one grid  ·  {len(rows)} rows"
    else:
        text = f"Copied {count} images as one image, side by side"
    return text + ("  ·  with your marks" if marked else "")


def phone_entry(device: Device) -> dict:
    return {
        "id": device.identity,
        "name": device.name,
        "model": device.model,
        "maker": device.manufacturer,
        "android": device.android,
        "screen": device.screen_on,
        "locked": device.locked,
    }


def remote_device(peer, phone: dict) -> Device:
    identity = str(phone.get("id"))[:96]
    screen = phone.get("screen")
    locked = phone.get("locked")
    return Device(
        serial="@" + identity,
        state="device",
        model=str(phone.get("model") or "")[:64],
        manufacturer=str(phone.get("maker") or "")[:64],
        market_name=str(phone.get("name") or "")[:64] or "Phone",
        android=str(phone.get("android") or "")[:16],
        screen_on=screen if isinstance(screen, bool) else None,
        locked=locked if isinstance(locked, bool) else None,
        hardware_serial=identity,
        via=peer.name,
        owner=peer.id,
    )


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
    def __init__(self, settings: Settings, link: Link | None = None):
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
        self._copy_layout = self._settings.get("copy_layout", "grid")
        if self._copy_layout not in COPY_LAYOUT_KEYS:
            self._copy_layout = "grid"
        self._copy_pool = QThreadPool(self)
        self._copy_pool.setMaxThreadCount(1)
        self._copy_signals = CopySignals(self)
        self._copy_signals.finished.connect(self._on_copy_ready)
        self._copy_signals.failed.connect(self._on_copy_failed)
        self._copy_number = 0
        self._copy_jobs: dict[int, tuple[int, str, int, bool, str]] = {}
        self._local_devices: list[Device] = []
        self._clip_source = "user"
        self._clip_timer = QTimer(self)
        self._clip_timer.setSingleShot(True)
        self._clip_timer.setInterval(120)
        self._clip_timer.timeout.connect(self._process_clipboard)
        pc_id = settings.get("pc_id")
        if not isinstance(pc_id, str) or not pc_id:
            pc_id = uuid.uuid4().hex
            settings.set("pc_id", pc_id)
        self._pc_id = pc_id
        self._pc = {key: bool(settings.get(key, default)) for key, default in PC_DEFAULTS.items()}
        try:
            self._pc_since = float(settings.get("pc_host_since") or 0.0)
        except (TypeError, ValueError):
            self._pc_since = 0.0
        self._link = link or Link(pc_id, __version__)
        self._link.setParent(self)
        self._link.peers_changed.connect(self._on_peers_changed)
        self._link.received.connect(self._on_link_message)
        self._link.neighborhood_changed.connect(self._update_pcs)
        self._known_pcs: set[str] = set()
        self._settings_dialog: SettingsDialog | None = None
        self._blobs = Blobs(RECEIVED_FOLDER)
        self._prepare_pool = QThreadPool(self)
        self._prepare_pool.setMaxThreadCount(1)
        self._prepare_signals = PrepareSignals(self)
        self._prepare_signals.ready.connect(self._on_prepared)
        self._prepare_number = 0
        self._prepare_jobs: dict[int, dict] = {}
        self._job_pool = QThreadPool(self)
        self._job_pool.setMaxThreadCount(3)
        self._job_signals = JobSignals(self)
        self._job_signals.finished.connect(self._on_job_done)
        self._job_signals.failed.connect(self._on_job_failed)
        self._job_number = 0
        self._jobs: dict[int, dict] = {}
        self._requests: dict[str, dict] = {}
        self._request_timer = QTimer(self)
        self._request_timer.setInterval(2000)
        self._request_timer.timeout.connect(self._expire_requests)
        self._remote_wants: dict[str, dict] = {}
        self._wants_sent: dict[str, tuple] = {}
        self._seen_shots: dict[str, float] = {}
        self._shot_signals = ShotSignals(self)
        self._shot_signals.ready.connect(self._on_shot_ready)
        self._shot_signals.failed.connect(self._on_shot_failed)
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
        tidy_folder(RELAY_FOLDER)
        if self._pc["pc_sharing"]:
            self._link.start()
        self._refresh_devices()
        self._update_pcs()

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
        self._device_bar = DeviceBar(
            self._settings.deselected_devices, self._vault.has, set(self._settings.get("deselected_pcs") or [])
        )
        self._device_bar.selection_changed.connect(self._on_device_selection)
        self._device_bar.pcs_changed.connect(self._on_pcs_selection)
        self._device_bar.pc_menu_requested.connect(self._show_pc_menu)
        self._device_bar.this_pc_requested.connect(self._show_this_pc_menu)
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
        self._copy_button = tool_button(Glyph.COPY, "", self.copy_selected)
        self._copy_mode_button = tool_button(Glyph.CHEVRON_DOWN, "How several images are copied", lambda: None, icon_size=12)
        self._copy_mode_button.setObjectName("chevron")
        self._copy_mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._copy_menu = QMenu(self)
        self._copy_actions = {}
        copy_group = QActionGroup(self)
        for mode, label in COPY_LAYOUTS:
            action = self._copy_menu.addAction(glyph_icon(COPY_GLYPHS[mode], size=16), label)
            action.triggered.connect(lambda _checked=False, value=mode: self._set_copy_layout(value, copy_now=True))
            action.setCheckable(True)
            copy_group.addAction(action)
            self._copy_actions[mode] = action
        self._copy_mode_button.setMenu(self._copy_menu)
        copy_box = QWidget()
        copy_layout = QHBoxLayout(copy_box)
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(0)
        copy_layout.addWidget(self._copy_button)
        copy_layout.addWidget(self._copy_mode_button)
        self._show_copy_layout()
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
        bottom_layout.addWidget(copy_box)
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
        self._top_fit = ResponsiveBar(top, [[self._path_button, self._adb_button], [self._change_button]])

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
        else:
            self._first_scan = False
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
        folder = self._settings.save_dir
        local_on = self._phone_mode != "phone" and folder is not None
        chosen = {device.serial for device in self._device_bar.selected() if not device.remote}
        targets = {}
        for device in self._devices:
            if device.remote or not device.ready:
                continue
            mine = local_on and device.serial in chosen
            modes = [want.get("shots") for want in self._wants_for(device.identity)]
            modes = [mode for mode in modes if mode in ("copy", "move")]
            if not mine and not modes:
                continue
            everyone = modes + ([self._phone_mode] if mine else [])
            targets[device.serial] = (folder if mine else RELAY_FOLDER, all(mode == "move" for mode in everyone))
        self._receiver.configure(targets)

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
        self._sync_wants()
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
        shot_id = uuid.uuid4().hex
        self._forward_phone_shot(device.identity if device is not None else serial, name, path, shot_id, moved)
        if inside(path, RELAY_FOLDER):
            try:
                os.remove(path)
            except OSError:
                pass
            return
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
        self._share_shot(path, shot_id, phone=name)

    def _toggle_auto_copy(self, checked: bool = False) -> None:
        enabled = self._auto_copy_button.isChecked()
        self._settings.set("auto_copy", enabled)
        if enabled:
            self._notify("Auto-copy on  ·  new screenshots go to the clipboard", "ok", 2600)
        else:
            self._notify("Auto-copy off")

    def _auto_copy(self, paths: list[str], source: str = "user") -> str:
        if not paths or not self._auto_copy_button.isChecked():
            return ""
        return "  ·  copied" if self.copy_paths(paths, quiet=True, source=source) else ""

    def _on_receive_failed(self, serial: str, message: str) -> None:
        device = self._device(serial)
        self._notify(f"Auto-receive failed  ·  {device.name if device is not None else serial}: {message}", "error", 4000)

    def _sync_clipboard(self) -> None:
        if self._clip_bridge is None:
            return
        serials = set()
        if self._clip_mode != "off":
            serials |= {device.serial for device in self._device_bar.selected() if not device.remote}
        for device in self._devices:
            if not device.remote and device.ready and any(want.get("clip") for want in self._wants_for(device.identity)):
                serials.add(device.serial)
        self._clip_bridge.configure(sorted(serials))

    def _set_clipboard_mode(self, mode: str) -> None:
        if mode not in CLIP_MODE_KEYS:
            mode = "off"
        self._clip_mode = mode
        self._settings.set("clipboard_sync", mode)
        self._clip_errors.clear()
        self._clip_announce = mode != "off"
        self._sync_clipboard()
        self._sync_wants()
        messages = {
            "off": ("Clipboard sync off", "info"),
            "both": ("Clipboard sync on  ·  PC ↔ phone", "ok"),
            "to_phone": ("Clipboard sync on  ·  PC → phone", "ok"),
            "to_pc": ("Clipboard sync on  ·  phone → PC", "ok"),
        }
        text, kind = messages[mode]
        if mode != "off" and self._clip_bridge is None and not any(device.remote for device in self._device_bar.selected()):
            text, kind = "Clipboard sync needs adb", "error"
        elif mode != "off" and not self._device_bar.selected():
            text += "  ·  select a phone"
        self._notify(text, kind, 2600)

    def _clipboard(self):
        return QGuiApplication.clipboard()

    def _set_clipboard(self, mime, source: str = "user") -> None:
        self._clip_source = source
        self._clipboard().setMimeData(mime)

    def _set_clipboard_text(self, text: str, source: str = "user") -> None:
        self._clip_source = source
        self._clipboard().setText(text)

    def _set_clipboard_image(self, image, source: str = "user") -> None:
        self._clip_source = source
        self._clipboard().setImage(image)

    def _on_pc_clipboard(self) -> None:
        self._clip_timer.start()

    def _process_clipboard(self) -> None:
        clipboard = self._clipboard()
        source = self._clip_source if clipboard.ownsClipboard() else "user"
        mime = clipboard.mimeData()
        if source != "phone":
            self._clipboard_to_phones(mime)
        if source not in ("peer", "phone"):
            self._clipboard_to_pcs(mime)

    def _clipboard_to_phones(self, mime) -> None:
        if self._clip_mode not in ("both", "to_phone"):
            return
        selected = self._device_bar.selected()
        local = [device.serial for device in selected if not device.remote] if self._clip_bridge is not None else []
        remote = [device for device in selected if device.remote]
        if not local and not remote:
            return
        text = clipboard_text(mime)
        if not text:
            return
        if len(text) > CLIP_LIMIT:
            if not self._clip_too_long:
                self._clip_too_long = True
                self._notify("Clipboard text is too long to send to the phone", "error", 3000)
            return
        self._clip_too_long = False
        if local:
            self._clip_bridge.send(text, local)
        for device in remote:
            self._link.send(device.owner, {"t": "to_phone", "phone": device.identity}, text.encode("utf-8"))

    def _on_phone_clipboard(self, serial: str, text: str) -> None:
        device = self._device(serial)
        if device is None:
            return
        chosen = any(item.serial == serial for item in self._device_bar.selected())
        if chosen and self._clip_mode in ("both", "to_pc"):
            if text != self._clipboard().text():
                self._set_clipboard_text(text, "phone")
            self._notify(f"Clipboard  ·  from {device.name}", "ok", 1600)
        if not self._hosting():
            return
        for peer_id, wants in list(self._remote_wants.items()):
            want = wants.get(device.identity)
            if want and want.get("clip") in ("both", "to_pc"):
                self._link.send(peer_id, {"t": "from_phone", "phone": device.identity}, text.encode("utf-8"))

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
        self._local_devices = devices
        self._refresh_devices()
        self._first_scan = False

    def _refresh_devices(self) -> None:
        devices = self._merged_devices()
        arrived = [device for device in devices if device.ready and device.serial not in self._ready_serials]
        self._ready_serials = {device.serial for device in devices if device.ready}
        self._devices = devices
        self._device_bar.set_devices(devices)
        self._update_status()
        self._sync_receiver()
        self._sync_clipboard()
        self._advertise()
        self._sync_wants()
        if arrived and not self._first_scan:
            names = [device.name + (f" via {device.via}" if device.remote else "") for device in arrived]
            self._notify("Connected  ·  " + ", ".join(names), "ok")

    def _merged_devices(self) -> list[Device]:
        local = self._local_devices
        ready = {device.identity for device in local if device.ready}
        mine = self._my_rank()
        claimed = set()
        remote = []
        for peer in self._hosts():
            for phone in peer.phones:
                identity = str(phone.get("id") or "")[:96]
                if not identity or identity in claimed:
                    continue
                if identity in ready and mine is not None and mine < peer.rank:
                    continue
                claimed.add(identity)
                remote.append(remote_device(peer, phone))
        return [device for device in local if not (device.ready and device.identity in claimed)] + remote

    def _on_adb_failed(self, message: str) -> None:
        self._adb_error = message
        self._local_devices = []
        self._refresh_devices()

    def _on_device_selection(self) -> None:
        self._settings.deselected_devices = self._device_bar.deselected()
        self._update_status()
        self._sync_receiver()
        self._sync_clipboard()
        self._sync_wants()

    def _on_blocked_device(self, device: Device) -> None:
        if device.state == "unauthorized":
            self._notify(f"{device.name}: tap “Allow USB debugging” on the phone", "error", 3500)
        else:
            self._notify(f"{device.name}: {state_text(device)}, unplug and replug the cable", "error", 3500)

    def _placeholder_text(self) -> tuple[str, str]:
        remote = any(device.remote for device in self._devices)
        if self._adb is None and not remote:
            return "adb not found", "Install Android SDK Platform-Tools or click “Find adb.exe” above."
        if self._adb_error and not remote:
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
            detail = f"Press Capture or F5  ·  {device.name}" + (f" via {device.via}" if device.remote else "")
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
        usable = [device for device in selected if device.remote or self._adb is not None]
        self._capture_button.setEnabled(bool(usable) and not busy)
        self._unlock_button.setEnabled(bool(usable))
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
        devices = [device for device in devices if device.remote or self._adb is not None]
        if not devices:
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
        number = self._batch.number
        lost = []
        for device in devices:
            if device.remote:
                if not self._request(device, "capture", batch=number, target=targets[device.serial]):
                    lost.append(device.serial)
                continue
            self._capture_pool.start(
                CaptureTask(
                    number,
                    self._adb,
                    device.serial,
                    targets[device.serial],
                    bound,
                    self._capture_signals,
                )
            )
        for serial in lost:
            QTimer.singleShot(0, lambda value=serial: self._on_capture_failed(number, value, "the host PC is offline"))


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
        for target in batch.targets.values():
            release_path(target)
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
        for path, _thumb in entries:
            self._share_shot(path, uuid.uuid4().hex)
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
        found = next((device for device in self._devices if device.serial == serial), None)
        if found is None:
            found = next((device for device in self._local_devices if device.serial == serial), None)
        return found

    def unlock_selected(self) -> None:
        devices = [device for device in self._device_bar.selected() if device.remote or self._adb is not None]
        if not devices:
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
        if device.serial in self._screen_busy or (self._adb is None and not device.remote):
            return
        from_vault = False
        if secret is None and self._vault.has(device.identity):
            secret = self._vault.get(device.identity)
            from_vault = secret is not None
        self._screen_busy[device.serial] = (secret, remember, from_vault)
        if device.remote:
            if not self._request(device, "unlock", secret=secret):
                self._screen_busy.pop(device.serial, None)
                self._notify(f"{device.via} is offline  ·  {device.name} can't be unlocked", "error", 3000)
                return
        else:
            self._screen_pool.start(UnlockTask(self._adb, device.serial, secret, self._screen_signals))
        self._notify(f"Unlocking  ·  {device.name}")

    def _lock_serial(self, serial: str) -> None:
        device = self._device(serial)
        if device is None or serial in self._screen_busy:
            return
        if device.remote:
            self._screen_busy[serial] = (None, False, False)
            if not self._request(device, "lock"):
                self._screen_busy.pop(serial, None)
                self._notify(f"{device.via} is offline  ·  {device.name} can't be locked", "error", 3000)
            return
        if self._adb is None:
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
        if count == 1:
            menu.addAction(glyph_icon(Glyph.COPY, size=16), "Copy image  (Ctrl+C)", lambda: self.copy_paths(paths))
        else:
            menu.addAction(
                glyph_icon(COPY_GLYPHS[self._copy_layout], size=16),
                f"Copy {count} images  ·  {LAYOUT_NAMES[self._copy_layout]}  (Ctrl+C)",
                lambda: self.copy_paths(paths),
            )
            for mode in COPY_LAYOUT_KEYS:
                if mode != self._copy_layout:
                    menu.addAction(
                        glyph_icon(COPY_GLYPHS[mode], size=16),
                        COPY_AS[mode],
                        lambda _checked=False, value=mode: self.copy_paths(paths, layout=value),
                    )
        menu.addAction(
            glyph_icon(Glyph.LINK, size=16),
            "Copy path" if count == 1 else "Copy paths",
            lambda: self._copy_text("\n".join(paths)),
        )
        peers = self._link.peers() if self._pc["pc_sharing"] else []
        if peers:
            send_menu = menu.addMenu(glyph_icon(Glyph.SEND, size=16), "Send to PC" if count == 1 else f"Send {count} to PC")
            for peer in peers:
                send_menu.addAction(
                    glyph_icon(Glyph.PC, size=16),
                    peer.name,
                    lambda _checked=False, value=peer.id: self._send_images(paths, [value]),
                )
            if len(peers) > 1:
                send_menu.addSeparator()
                send_menu.addAction("All my PCs", lambda: self._send_images(paths, [peer.id for peer in peers]))
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
        self._set_clipboard_text(text, "user")
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
        for device in self._local_devices:
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
            "copy_layout": self._copy_layout,
        }
        values.update(self._pc)
        network = {
            "enabled": self._settings.get("tailscale_enabled", True),
            "saved": self._network_devices(),
            "on_forget": self._forget_network_address,
            "on_setup": self.open_network_dialog,
        }
        pcs = {"status": self._pc_status_text()}
        dialog = SettingsDialog(values, self._on_setting_changed, self._fullscreen or self, network, pcs)
        self._settings_dialog = dialog
        dialog.exec()
        self._settings_dialog = None
        dialog.deleteLater()

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "phone_shots":
            self._set_phone_mode(value)
        elif key == "delete_mode":
            self._settings.set(key, value)
            self._show_delete_mode()
        elif key == "clipboard_sync":
            self._set_clipboard_mode(value)
        elif key == "copy_layout":
            self._set_copy_layout(value)
        elif key in PC_DEFAULTS:
            self._set_pc_option(key, value)
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
        usb = [(device.serial, device.name) for device in self._local_devices if device.ready and not device.network]
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

    def _hosting(self) -> bool:
        return self._pc["pc_sharing"] and self._pc["pc_host"] and self._link.running

    def _my_rank(self):
        return (self._pc_since, self._pc_id) if self._hosting() else None

    def _hosts(self) -> list:
        return sorted((peer for peer in self._link.peers() if peer.host), key=lambda peer: peer.rank)

    def _wants_for(self, identity: str) -> list[dict]:
        if not self._hosting():
            return []
        return [wants[identity] for wants in self._remote_wants.values() if identity in wants]

    def _served(self, identity: str) -> Device | None:
        if not identity or not self._hosting():
            return None
        return next(
            (device for device in self._devices if not device.remote and device.ready and device.identity == identity),
            None,
        )

    def _pc_targets(self) -> list:
        if not self._pc["pc_sharing"]:
            return []
        chosen = set(self._device_bar.selected_pcs())
        return [peer for peer in self._link.peers() if peer.id in chosen]

    def _advertise(self) -> None:
        hosting = self._hosting()
        phones = [phone_entry(device) for device in self._devices if hosting and not device.remote and device.ready]
        self._link.set_state(hosting, self._pc_since, phones)

    def _sync_wants(self) -> None:
        if not self._link.running:
            return
        chosen = {device.serial for device in self._device_bar.selected()}
        shots = self._phone_mode if self._phone_mode != "phone" else ""
        clip = self._clip_mode if self._clip_mode != "off" else ""
        by_host: dict[str, dict] = {}
        if shots or clip:
            for device in self._devices:
                if device.remote and device.serial in chosen:
                    by_host.setdefault(device.owner, {})[device.identity] = {"shots": shots, "clip": clip}
        for peer in self._link.peers():
            wants = by_host.get(peer.id, {})
            last = self._wants_sent.get(peer.id)
            if last is not None and last[0] is peer and last[1] == wants:
                continue
            if self._link.send(peer.id, {"t": "want", "phones": wants}):
                self._wants_sent[peer.id] = (peer, wants)

    def _update_pcs(self) -> None:
        hood = self._link.neighborhood
        visible = self._pc["pc_sharing"] and not (hood is not None and hood.reason == "missing")
        hosting = self._hosting()
        pcs = [
            {"id": peer.id, "name": peer.name, "host": peer.host, "phones": len(peer.phones), "version": peer.version}
            for peer in self._link.peers()
        ]
        if hosting:
            status = "Host"
        elif hood is not None and hood.reason == "stopped":
            status = "Tailscale off"
        else:
            status = ""
        lines = [f"This PC  ·  {self._link.name}"]
        lines.append("Host: your other PCs can use this PC's phones" if hosting else "Not a host: this PC's phones stay here")
        if not pcs:
            lines.append("Your other PCs show up here when they run Spona on the same Tailscale account")
        lines += ["", "Click: host, what to send, settings"]
        self._device_bar.set_pcs(visible, {"host": hosting, "status": status, "tooltip": "\n".join(lines)}, pcs)
        if self._settings_dialog is not None:
            self._settings_dialog.set_pc_status(self._pc_status_text())

    def _pc_status_text(self) -> str:
        if not self._pc["pc_sharing"]:
            return ""
        hood = self._link.neighborhood
        if hood is None:
            return "Looking for your other PCs…"
        if hood.reason == "missing":
            return "Tailscale isn't installed on this PC."
        if hood.reason == "stopped":
            return "Tailscale is off on this PC."
        if self._link.listen_error:
            return f"Other PCs can't reach this one: {self._link.listen_error}"
        peers = self._link.peers()
        if not peers:
            return "No other PCs with Spona found yet."
        return "Linked  ·  " + ", ".join(peer.name + ("  (host)" if peer.host else "") for peer in peers)

    def _on_peers_changed(self) -> None:
        peers = {peer.id: peer for peer in self._link.peers()}
        for identity in [identity for identity in self._remote_wants if identity not in peers]:
            del self._remote_wants[identity]
        for identity in [identity for identity in self._wants_sent if identity not in peers]:
            del self._wants_sent[identity]
        for key, request in list(self._requests.items()):
            if request["peer"] not in peers:
                del self._requests[key]
                self._finish_request(request, {"ok": False, "error": f"{request['host']} went offline"})
        new = [peer for identity, peer in peers.items() if identity not in self._known_pcs]
        self._known_pcs = set(peers)
        self._refresh_devices()
        self._update_pcs()
        if new:
            self._notify("Linked  ·  " + ", ".join(peer.name for peer in new), "ok", 2400)

    def _on_pcs_selection(self) -> None:
        self._settings.set("deselected_pcs", sorted(self._device_bar.deselected_pcs()))
        if not self._link.peers():
            return
        chosen = self._pc_targets()
        if chosen:
            self._notify("Sending to  ·  " + ", ".join(peer.name for peer in chosen), "info", 2200)
        else:
            self._notify("Not sending to any PC", "info", 2200)

    def _set_pc_option(self, key: str, value) -> None:
        value = bool(value)
        self._pc[key] = value
        self._settings.set(key, value)
        if key == "pc_host" and value:
            self._pc_since = time.time()
            self._settings.set("pc_host_since", self._pc_since)
        if key == "pc_sharing":
            if value:
                self._link.start()
            else:
                self._link.stop()
        self._refresh_devices()
        self._update_pcs()
        messages = {
            ("pc_sharing", True): ("Sharing with your PCs on  ·  PCs with Spona show up by themselves", "ok"),
            ("pc_sharing", False): ("Sharing with PCs off", "info"),
            ("pc_host", True): ("Host on  ·  your other PCs can use this PC's phones", "ok"),
            ("pc_host", False): ("Host off  ·  this PC's phones stay here", "info"),
            ("pc_send_clipboard", True): ("What you copy goes to the selected PCs", "ok"),
            ("pc_send_clipboard", False): ("What you copy stays on this PC", "info"),
            ("pc_send_shots", True): ("New screenshots go to the selected PCs", "ok"),
            ("pc_send_shots", False): ("New screenshots stay on this PC", "info"),
        }
        text, kind = messages[(key, value)]
        if key != "pc_sharing" and value and not self._pc["pc_sharing"]:
            text += "  ·  turn on sharing with PCs first"
        self._notify(text, kind, 2600)

    def _show_this_pc_menu(self, position) -> None:
        menu = QMenu(self)
        options = (
            ("pc_host", "Host  ·  lend my phones to my other PCs"),
            ("pc_send_clipboard", "Send what I copy"),
            ("pc_send_shots", "Send new screenshots"),
        )
        on_icon = glyph_icon(Glyph.ACCEPT, ACCENT, 16)
        off_icon = glyph_icon(Glyph.ACCEPT, "#00000000", 16)
        for index, (key, label) in enumerate(options):
            action = menu.addAction(on_icon if self._pc[key] else off_icon, label)
            action.setCheckable(True)
            action.setChecked(self._pc[key])
            action.triggered.connect(lambda checked=False, value=key: self._set_pc_option(value, checked))
            if index == 0:
                menu.addSeparator()
        menu.addSeparator()
        menu.addAction(glyph_icon(Glyph.SETTINGS, size=16), "PC settings…", self.open_settings)
        menu.addAction(off_icon, "Stop sharing with PCs", lambda: self._set_pc_option("pc_sharing", False))
        menu.exec(position)

    def _show_pc_menu(self, identity: str, position) -> None:
        peer = self._link.peer(identity)
        if peer is None:
            return
        menu = QMenu(self)
        paths = list(self._selected_paths)
        if paths:
            label = (
                f"Send the selected image to {peer.name}"
                if len(paths) == 1
                else f"Send {len(paths)} selected images to {peer.name}"
            )
            menu.addAction(glyph_icon(Glyph.SEND, size=16), label, lambda: self._send_images(paths, [identity]))
            menu.addSeparator()
        menu.addAction(glyph_icon(Glyph.PC, size=16), "Send only to this PC", lambda: self._device_bar.select_only_pc(identity))
        menu.addAction("Send to all my PCs", self._device_bar.select_all_pcs)
        menu.exec(position)

    def _send_images(self, paths: list[str], identities: list[str]) -> None:
        paths = sorted((path for path in paths if os.path.isfile(path)), key=file_time)
        peers = [peer for peer in (self._link.peer(identity) for identity in identities) if peer is not None]
        if not paths:
            self._notify("Nothing to send", "error")
            return
        if not peers:
            self._notify("That PC isn't linked any more", "error")
            return
        paths = paths[:FILE_LIMIT]
        self._prepare_number += 1
        self._prepare_jobs[self._prepare_number] = {"purpose": "send", "peers": [peer.id for peer in peers], "count": len(paths)}
        self._prepare_pool.start(PrepareTask(self._prepare_number, "files", paths, self._prepare_signals))
        names = ", ".join(peer.name for peer in peers)
        self._notify(f"Sending {len(paths)} {images_word(len(paths))} to {names}…", "info", 8000)

    def _on_prepared(self, number: int, items) -> None:
        job = self._prepare_jobs.pop(number, None)
        if job is None:
            return
        peers = [peer for peer in (self._link.peer(identity) for identity in job["peers"]) if peer is not None]
        if job["purpose"] == "clip":
            if not items:
                return
            signature = ("files", tuple(key for key, _name, _data in items))
            listing = [{"sha1": key, "name": name} for key, name, _data in items]
            for peer in peers:
                if not self._fresh_clip(peer, signature):
                    continue
                for key, name, data in items:
                    self._send_blob(peer, key, name, data)
                self._link.send(peer.id, {"t": "clip", "kind": "files", "items": listing})
            return
        if not items or not peers:
            self._notify("Nothing was sent", "error", 2600)
            return
        shots = [(uuid.uuid4().hex, key, name, data) for key, name, data in items]
        for peer in peers:
            for shot_id, key, name, data in shots:
                self._send_blob(peer, key, name, data)
                self._link.send(peer.id, {"t": "shot", "id": shot_id, "sha1": key, "name": name, "kind": "share"})
        count = len(items)
        extra = "" if count == job["count"] else f"  ·  {job['count'] - count} too big"
        names = ", ".join(peer.name for peer in peers)
        self._notify(f"Sent {count} {images_word(count)} to {names}" + extra, "ok", 2600)

    def _fresh_clip(self, peer, signature) -> bool:
        now = time.monotonic()
        if peer.last_clip and peer.last_clip[0] == signature and now - peer.last_clip[1] < CLIP_REPEAT_SECONDS:
            return False
        peer.last_clip = (signature, now)
        return True

    def _clipboard_to_pcs(self, mime) -> None:
        if not self._pc["pc_send_clipboard"]:
            return
        targets = self._pc_targets()
        if not targets:
            return
        payload = clipboard_payload(mime)
        if payload is None:
            return
        kind, value = payload
        if kind == "text":
            data = value.encode("utf-8")
            if len(data) > TEXT_LIMIT:
                self._notify("Clipboard text is too long to send to your PCs", "error", 3000)
                return
            signature = ("text", digest(data))
            for peer in targets:
                if self._fresh_clip(peer, signature):
                    self._link.send(peer.id, {"t": "clip", "kind": "text"}, data)
            return
        self._prepare_number += 1
        self._prepare_jobs[self._prepare_number] = {"purpose": "clip", "peers": [peer.id for peer in targets]}
        self._prepare_pool.start(PrepareTask(self._prepare_number, kind, value, self._prepare_signals))

    def _send_blob(self, peer, key: str, name: str, data: bytes) -> None:
        if len(data) <= MAX_PAYLOAD and self._link.mark_sent(peer, key):
            self._link.send(peer.id, {"t": "blob", "sha1": key, "name": name}, data)

    def _send_shot(self, peer, path: str, data: bytes, key: str, shot_id: str, kind: str, phone: str = "", moved: bool = False) -> None:
        name = os.path.basename(path)
        self._send_blob(peer, key, name, data)
        self._link.send(
            peer.id, {"t": "shot", "id": shot_id, "sha1": key, "name": name, "kind": kind, "phone": phone, "moved": moved}
        )

    @staticmethod
    def _read_shot(path: str) -> bytes | None:
        try:
            data = Path(path).read_bytes()
        except OSError:
            return None
        return data if 0 < len(data) <= MAX_PAYLOAD else None

    def _share_shot(self, path: str, shot_id: str, phone: str = "") -> None:
        if not self._pc["pc_send_shots"]:
            return
        targets = self._pc_targets()
        if not targets:
            return
        data = self._read_shot(path)
        if data is None:
            return
        key = digest(data)
        for peer in targets:
            self._send_shot(peer, path, data, key, shot_id, "share", phone)

    def _forward_phone_shot(self, identity: str, name: str, path: str, shot_id: str, moved: bool) -> None:
        if not self._hosting():
            return
        peers = [
            self._link.peer(peer_id)
            for peer_id, wants in self._remote_wants.items()
            if wants.get(identity, {}).get("shots") in ("copy", "move")
        ]
        peers = [peer for peer in peers if peer is not None]
        if not peers:
            return
        data = self._read_shot(path)
        if data is None:
            return
        key = digest(data)
        for peer in peers:
            self._send_shot(peer, path, data, key, shot_id, "phone", name, moved)

    def _on_link_message(self, peer, header: dict, payload: bytes) -> None:
        handlers = {
            "blob": self._on_blob,
            "shot": self._on_shot,
            "clip": self._on_clip,
            "want": self._on_want,
            "do": self._on_do,
            "done": self._on_done,
            "to_phone": self._on_to_phone,
            "from_phone": self._on_from_phone,
        }
        handler = handlers.get(header.get("t"))
        if handler is not None:
            handler(peer, header, payload)

    def _on_blob(self, _peer, header: dict, payload: bytes) -> None:
        self._blobs.store(header.get("sha1"), str(header.get("name") or ""), payload)

    def _on_shot(self, peer, header: dict, _payload: bytes) -> None:
        shot_id = str(header.get("id") or "")[:64]
        now = time.monotonic()
        self._seen_shots = {key: when for key, when in self._seen_shots.items() if now - when < SHOT_MEMORY_SECONDS}
        if not shot_id or shot_id in self._seen_shots:
            return
        path = self._blobs.claim(header.get("sha1"), self._folder(), str(header.get("name") or ""))
        if path is None:
            return
        self._seen_shots[shot_id] = now
        ticket = {
            "peer": peer.name,
            "kind": header.get("kind"),
            "phone": str(header.get("phone") or "")[:64],
            "moved": bool(header.get("moved")),
        }
        self._capture_pool.start(ShotTask(ticket, path, self._gallery.thumb_bound(), self._shot_signals))

    def _on_shot_ready(self, ticket: dict, path: str, image, thumb) -> None:
        if ticket.get("capture"):
            release_path(Path(ticket["target"]))
            self._blobs.remember(ticket.get("sha1"), path)
            self._on_captured(ticket["batch"], ticket["serial"], path, image, thumb)
            return
        self._loader.remember(path, QPixmap.fromImage(image))
        busy = self._fullscreen is not None and self._fullscreen.marker_active()
        if self._gallery.folder is not None and inside(path, self._gallery.folder):
            self._gallery.add_batch([(path, QPixmap.fromImage(thumb))], select=not busy)
            if not busy:
                self._show_paths([path], flash=True)
        phone = ticket.get("phone")
        if phone:
            prefix = f"{'Moved from' if ticket.get('moved') else 'From'} {phone} via {ticket['peer']}"
        else:
            prefix = f"From {ticket['peer']}"
        copied = self._auto_copy([path], source="user" if ticket.get("kind") == "phone" else "peer")
        self._notify(f"{prefix}  ·  {os.path.basename(path)}" + copied, "ok")

    def _on_shot_failed(self, ticket: dict, message: str) -> None:
        if ticket.get("capture"):
            release_path(Path(ticket["target"]))
            self._on_capture_failed(ticket["batch"], ticket["serial"], message)

    def _on_clip(self, peer, header: dict, payload: bytes) -> None:
        kind = header.get("kind")
        if kind == "text":
            text = payload.decode("utf-8", "replace")
            if not text:
                return
            if text != self._clipboard().text():
                self._set_clipboard_text(text, "peer")
            self._notify(f"Clipboard  ·  from {peer.name}", "ok", 1800)
            return
        if kind != "files":
            return
        items = header.get("items") if isinstance(header.get("items"), list) else []
        paths = [self._blobs.path(item.get("sha1")) for item in items[:FILE_LIMIT] if isinstance(item, dict)]
        paths = [path for path in paths if path]
        if not paths:
            return
        image = None
        if len(paths) == 1:
            reader = QImageReader(paths[0])
            reader.setAutoTransform(True)
            image = reader.read()
        self._set_clipboard(file_mime(paths, image), "peer")
        what = "an image" if len(paths) == 1 else f"{len(paths)} images"
        self._notify(f"Clipboard  ·  {what} from {peer.name}", "ok", 2200)

    def _on_want(self, peer, header: dict, _payload: bytes) -> None:
        phones = header.get("phones")
        wants = {}
        if isinstance(phones, dict):
            for identity, want in list(phones.items())[:32]:
                if not isinstance(identity, str) or not isinstance(want, dict):
                    continue
                shots = want.get("shots") if want.get("shots") in ("copy", "move") else ""
                clip = want.get("clip") if want.get("clip") in ("both", "to_phone", "to_pc") else ""
                if shots or clip:
                    wants[identity] = {"shots": shots, "clip": clip}
        if wants:
            self._remote_wants[peer.id] = wants
        else:
            self._remote_wants.pop(peer.id, None)
        self._sync_receiver()
        self._sync_clipboard()

    def _on_do(self, peer, header: dict, _payload: bytes) -> None:
        key = str(header.get("req") or "")[:40]
        action = header.get("action")
        if not key or action not in ("capture", "unlock", "lock"):
            return
        device = self._served(str(header.get("phone") or ""))
        if device is None or self._adb is None:
            self._link.send(peer.id, {"t": "done", "req": key, "ok": False, "error": f"the phone isn't on {self._link.name} any more"})
            return
        self._job_number += 1
        number = self._job_number
        job = {"peer": peer.id, "req": key, "action": action, "identity": device.identity, "from_vault": False}
        if action == "capture":
            task = JobTask(number, self._job_signals, grab_screen, self._adb, device.serial)
        elif action == "lock":
            task = JobTask(number, self._job_signals, lock_screen, self._adb, device.serial)
        else:
            secret = header.get("secret") if isinstance(header.get("secret"), str) and header.get("secret") else None
            if secret is None and self._vault.has(device.identity):
                secret = self._vault.get(device.identity)
                job["from_vault"] = secret is not None
            task = JobTask(number, self._job_signals, unlock_screen, self._adb, device.serial, secret)
        self._jobs[number] = job
        self._job_pool.start(task)
        verbs = {"capture": "takes a screenshot of", "unlock": "unlocks", "lock": "locks"}
        self._notify(f"{peer.name} {verbs[action]} {device.name}", "info", 1800)

    def _on_job_done(self, number: int, result) -> None:
        job = self._jobs.pop(number, None)
        if job is None:
            return
        if self._monitor is not None:
            self._monitor.poke()
        if job["action"] == "unlock" and result == UnlockOutcome.REJECTED.value and job["from_vault"]:
            self._vault.forget(job["identity"])
        peer = self._link.peer(job["peer"])
        if peer is None:
            return
        if job["action"] == "capture":
            data = bytes(result)
            key = digest(data)
            self._send_blob(peer, key, f"Screenshot_{datetime.now().strftime(TIMESTAMP_FORMAT)}.png", data)
            self._link.send(peer.id, {"t": "done", "req": job["req"], "ok": True, "sha1": key})
            return
        self._link.send(peer.id, {"t": "done", "req": job["req"], "ok": True, "outcome": str(result)})

    def _on_job_failed(self, number: int, message: str) -> None:
        job = self._jobs.pop(number, None)
        if job is not None:
            self._link.send(job["peer"], {"t": "done", "req": job["req"], "ok": False, "error": message[:200]})

    def _request(self, device: Device, action: str, secret: str | None = None, batch: int | None = None, target=None) -> bool:
        peer = self._link.peer(device.owner)
        if peer is None:
            return False
        key = uuid.uuid4().hex[:16]
        header = {"t": "do", "req": key, "action": action, "phone": device.identity}
        if secret:
            header["secret"] = secret
        if not self._link.send(peer.id, header):
            return False
        self._requests[key] = {
            "peer": peer.id,
            "host": peer.name,
            "action": action,
            "serial": device.serial,
            "batch": batch,
            "target": target,
            "time": time.monotonic(),
        }
        if not self._request_timer.isActive():
            self._request_timer.start()
        return True

    def _on_done(self, peer, header: dict, _payload: bytes) -> None:
        key = str(header.get("req") or "")
        request = self._requests.get(key)
        if request is None or request["peer"] != peer.id:
            return
        del self._requests[key]
        self._finish_request(request, header)

    def _finish_request(self, request: dict, header: dict) -> None:
        ok = bool(header.get("ok"))
        error = str(header.get("error") or "the host PC didn't answer")[:200]
        serial = request["serial"]
        if request["action"] == "capture":
            source = self._blobs.path(header.get("sha1")) if ok else None
            if source is None:
                self._on_capture_failed(request["batch"], serial, error if not ok else "the screenshot didn't arrive")
                return
            ticket = {
                "capture": True,
                "batch": request["batch"],
                "serial": serial,
                "target": str(request["target"]),
                "sha1": header.get("sha1"),
            }
            self._capture_pool.start(
                ShotTask(ticket, str(request["target"]), self._gallery.thumb_bound(), self._shot_signals, source=source)
            )
            return
        if ok:
            self._on_screen_action_finished(serial, str(header.get("outcome") or ""))
        else:
            self._on_screen_action_failed(serial, error)

    def _expire_requests(self) -> None:
        now = time.monotonic()
        for key, request in list(self._requests.items()):
            if now - request["time"] > REQUEST_SECONDS:
                del self._requests[key]
                self._finish_request(request, {"ok": False, "error": f"{request['host']} didn't answer"})
        if not self._requests:
            self._request_timer.stop()

    def _on_to_phone(self, _peer, header: dict, payload: bytes) -> None:
        device = self._served(str(header.get("phone") or ""))
        if device is None or self._clip_bridge is None:
            return
        text = payload.decode("utf-8", "replace")
        if text and len(text) <= CLIP_LIMIT:
            self._clip_bridge.send(text, [device.serial])

    def _on_from_phone(self, peer, header: dict, payload: bytes) -> None:
        if self._clip_mode not in ("both", "to_pc"):
            return
        identity = str(header.get("phone") or "")
        device = next(
            (item for item in self._device_bar.selected() if item.remote and item.owner == peer.id and item.identity == identity),
            None,
        )
        text = payload.decode("utf-8", "replace")
        if device is None or not text:
            return
        if text != self._clipboard().text():
            self._set_clipboard_text(text, "phone")
        self._notify(f"Clipboard  ·  from {device.name}", "ok", 1600)

    def copy_selected(self) -> None:
        self.copy_paths(self._selected_paths)

    def copy_paths(self, paths: list[str], quiet: bool = False, layout: str | None = None, source: str = "user") -> bool:
        paths = [path for path in paths if os.path.isfile(path)]
        if not paths:
            return False
        if len(paths) == 1:
            return self._copy_one(paths[0], quiet, source)
        layout = layout if layout in COPY_LAYOUT_KEYS else self._copy_layout
        paths.sort(key=file_time)
        items = [(path, tuple(self._annotations.strokes(path))) for path in paths]
        marked = sum(1 for _path, strokes in items if strokes)
        self._copy_number += 1
        self._copy_jobs.clear()
        if layout == "files" and not marked:
            self._set_clipboard(file_mime(paths), source)
            if not quiet:
                self._notify(copied_text(len(paths), layout, 0, None), "ok", 2600)
            return True
        self._copy_jobs[self._copy_number] = (len(paths), layout, marked, quiet, source)
        self._copy_pool.start(CopyTask(self._copy_number, items, layout, COPY_FOLDER, self._copy_signals))
        if not quiet:
            self._notify(f"Copying {len(paths)} images…", "info", 10000)
        return True

    def _copy_one(self, path: str, quiet: bool, source: str = "user") -> bool:
        pixmap = self._full_pixmap(path)
        if pixmap is None:
            if not quiet:
                self._notify("The image can't be opened", "error")
            return False
        self._copy_number += 1
        self._copy_jobs.clear()
        if self._annotations.has(path):
            self._set_clipboard_image(render_annotated(pixmap, self._annotations.strokes(path)), source)
            if not quiet:
                self._notify("Copied with your marks", "ok")
            return True
        self._set_clipboard(file_mime([path], pixmap.toImage()), source)
        if not quiet:
            self._notify("Copied  ·  paste it as an image or as a file", "ok")
        return True

    def _on_copy_ready(self, number: int, image, paths, rows) -> None:
        job = self._copy_jobs.pop(number, None)
        if job is None:
            return
        count, layout, marked, quiet, source = job
        self._set_clipboard(file_mime(list(paths), image), source)
        if not quiet:
            self._notify(copied_text(count, layout, marked, rows), "ok", 3000)

    def _on_copy_failed(self, number: int, message: str) -> None:
        job = self._copy_jobs.pop(number, None)
        if job is not None and not job[3]:
            self._notify(message, "error", 3000)

    def _set_copy_layout(self, mode: str, copy_now: bool = False) -> None:
        if mode not in COPY_LAYOUT_KEYS:
            mode = "grid"
        self._copy_layout = mode
        self._settings.set("copy_layout", mode)
        self._show_copy_layout()
        if copy_now and len(self._selected_paths) > 1:
            self.copy_paths(self._selected_paths)
            return
        messages = {
            "grid": "Several images now copy as one grid",
            "row": "Several images now copy as one image, side by side",
            "files": "Several images now copy as separate images",
        }
        self._notify(messages[mode], "ok", 2600)

    def _show_copy_layout(self) -> None:
        tips = {
            "grid": "one image, laid out as a grid",
            "row": "one image, side by side",
            "files": "separate images",
        }
        self._copy_button.setToolTip(f"Copy (Ctrl+C)\nSeveral images: {tips[self._copy_layout]}\nUse the arrow to choose")
        for key, action in self._copy_actions.items():
            action.setChecked(key == self._copy_layout)

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
        self._link.stop()
        self._stop_workers()
        super().closeEvent(event)
