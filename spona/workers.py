import base64
import binascii
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage, QImageReader

from .adb import PHONE_SCREENSHOT_FOLDERS, Adb, AdbError, Device, clip_helper_command
from .annotations import annotate_image
from .collage import draw, oriented_size, plan
from .naming import release_path, reserve_path, safe_name


class DeviceMonitor(QThread):
    devices_changed = Signal(object)
    failed = Signal(str)

    def __init__(self, adb: Adb, interval: float = 1.2, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._interval = interval
        self._stopping = threading.Event()
        self._wakeup = threading.Event()
        self._described: dict[str, Device] = {}

    def run(self):
        try:
            self._adb.start_server()
        except AdbError as error:
            self.failed.emit(str(error))
        last = None
        last_error = None
        while not self._stopping.is_set():
            try:
                devices = [self._with_screen_state(self._describe(device)) for device in self._adb.devices()]
            except AdbError as error:
                message = str(error)
                if message != last_error:
                    last_error = message
                    self.failed.emit(message)
                last = None
            else:
                last_error = None
                if devices != last:
                    last = devices
                    self.devices_changed.emit(devices)
            self._wakeup.wait(self._interval)
            self._wakeup.clear()

    def _describe(self, device: Device) -> Device:
        if not device.ready:
            return device
        known = self._described.get(device.serial)
        if known is None:
            try:
                known = self._adb.describe(device)
            except AdbError:
                return device
            self._described[device.serial] = known
        return known

    def _with_screen_state(self, device: Device) -> Device:
        if not device.ready:
            return device
        try:
            state = self._adb.screen_state(device.serial)
        except AdbError:
            return device
        return replace(device, screen_on=state.awake, locked=state.locked)

    def poke(self):
        self._wakeup.set()

    def stop(self):
        self._stopping.set()
        self._wakeup.set()
        self.wait(4000)


class CaptureSignals(QObject):
    captured = Signal(int, str, str, QImage, QImage)
    failed = Signal(int, str, str)


class CaptureTask(QRunnable):
    def __init__(
        self,
        batch: int,
        adb: Adb,
        serial: str,
        target: Path,
        thumb_bound: QSize,
        signals: CaptureSignals,
        wake_screen: bool = True,
    ):
        super().__init__()
        self._batch = batch
        self._adb = adb
        self._serial = serial
        self._target = target
        self._thumb_bound = thumb_bound
        self._signals = signals
        self._wake_screen = wake_screen

    def _grab(self) -> bytes:
        woke = False
        if self._wake_screen:
            try:
                if not self._adb.screen_state(self._serial).awake:
                    self._adb.wake(self._serial)
                    woke = True
                    time.sleep(0.8)
            except AdbError:
                woke = False
        try:
            return self._adb.screencap(self._serial)
        finally:
            if woke:
                try:
                    self._adb.sleep(self._serial)
                except AdbError:
                    pass

    def run(self):
        try:
            image, thumbnail = save_image(self._grab(), self._target, self._thumb_bound)
        except (AdbError, OSError) as error:
            self._signals.failed.emit(self._batch, self._serial, str(error))
            return
        finally:
            release_path(self._target)
        self._signals.captured.emit(self._batch, self._serial, str(self._target), image, thumbnail)


def save_image(data: bytes, target: Path, thumb_bound: QSize) -> tuple[QImage, QImage]:
    image = QImage.fromData(data)
    if image.isNull():
        raise AdbError("the phone returned an invalid image")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    os.replace(partial, target)
    thumbnail = image.scaled(thumb_bound, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return image, thumbnail


class PhoneScreenshotWatcher(QThread):
    received = Signal(str, str, str, bool, QImage, QImage)
    failed = Signal(str, str)

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, adb: Adb, thumb_bound: QSize, interval: float = 0.33, parent=None, folders=None):
        super().__init__(parent)
        self._adb = adb
        self._thumb_bound = thumb_bound
        self._interval = interval
        self._folders = tuple(folders) if folders else PHONE_SCREENSHOT_FOLDERS
        self._lock = threading.Lock()
        self._targets: dict[str, str] = {}
        self._folder: Path | None = None
        self._move = False
        self._stopping = threading.Event()
        self._wakeup = threading.Event()
        self._since: dict[str, int] = {}
        self._seen: dict[str, set[str]] = {}
        self._pending: dict[str, dict[str, int]] = {}

    def configure(self, targets: dict[str, str], folder: Path | None, move: bool = False) -> None:
        with self._lock:
            self._targets = dict(targets)
            self._folder = folder
            self._move = move
        self._wakeup.set()

    def run(self):
        while not self._stopping.is_set():
            with self._lock:
                targets = dict(self._targets)
                folder = self._folder
                move = self._move
            for serial in [serial for serial in self._since if serial not in targets]:
                self._since.pop(serial, None)
                self._seen.pop(serial, None)
                self._pending.pop(serial, None)
            for serial in targets:
                if self._stopping.is_set():
                    break
                try:
                    self._poll(serial, folder, move)
                except AdbError:
                    continue
            self._wakeup.wait(self._interval)
            self._wakeup.clear()

    def _poll(self, serial: str, folder: Path | None, move: bool) -> None:
        entries = [
            entry for entry in self._adb.recent_screenshots(serial, self._folders)
            if entry[0].lower().endswith(self.IMAGE_SUFFIXES)
        ]
        if serial not in self._since:
            self._since[serial] = max((modified for _path, _size, modified in entries), default=0)
            self._seen[serial] = {path for path, _size, _modified in entries}
            self._pending[serial] = {}
            return
        since = self._since[serial]
        seen = self._seen[serial]
        pending = self._pending[serial]
        for path, size, modified in sorted(entries, key=lambda entry: entry[2]):
            if path in seen or modified < since or size <= 0:
                continue
            if pending.get(path) != size:
                time.sleep(0.25)
                settled = self._adb.file_size(serial, path)
                if settled != size:
                    pending[path] = settled
                    continue
            seen.add(path)
            pending.pop(path, None)
            self._receive(serial, path, folder, move)

    def _receive(self, serial: str, remote_path: str, folder: Path | None, move: bool) -> None:
        if folder is None:
            return
        name = remote_path.rsplit("/", 1)[-1]
        stem, dot, suffix = name.rpartition(".")
        target = reserve_path(folder, stem or name, f".{suffix}" if dot else ".png")
        try:
            image, thumbnail = save_image(self._adb.read_file(serial, remote_path), target, self._thumb_bound)
        except (AdbError, OSError) as error:
            self.failed.emit(serial, str(error))
            return
        finally:
            release_path(target)
        moved = False
        if move:
            try:
                moved = self._adb.delete_files(serial, [remote_path]) == 1
            except AdbError:
                moved = False
        self.received.emit(serial, str(target), remote_path, moved, image, thumbnail)

    def stop(self):
        self._stopping.set()
        self._wakeup.set()
        self.wait(4000)


class ScreenSignals(QObject):
    finished = Signal(str, str)
    failed = Signal(str, str)


class UnlockTask(QRunnable):
    def __init__(self, adb: Adb, serial: str, secret: str | None, signals: ScreenSignals):
        super().__init__()
        self._adb = adb
        self._serial = serial
        self._secret = secret
        self._signals = signals

    def run(self):
        try:
            outcome = self._adb.unlock(self._serial, self._secret)
        except AdbError as error:
            self._signals.failed.emit(self._serial, str(error))
            return
        finally:
            self._secret = None
        self._signals.finished.emit(self._serial, outcome.value)


class LockTask(QRunnable):
    def __init__(self, adb: Adb, serial: str, signals: ScreenSignals):
        super().__init__()
        self._adb = adb
        self._serial = serial
        self._signals = signals

    def run(self):
        try:
            self._adb.sleep(self._serial)
        except AdbError as error:
            self._signals.failed.emit(self._serial, str(error))
            return
        self._signals.finished.emit(self._serial, "locked")


class DeleteSignals(QObject):
    finished = Signal(int, str, int, int)
    failed = Signal(int, str, str)


class PhoneDeleteTask(QRunnable):
    def __init__(self, batch: int, adb: Adb, serial: str, remote_paths: list[str], signals: DeleteSignals):
        super().__init__()
        self._batch = batch
        self._adb = adb
        self._serial = serial
        self._remote_paths = list(remote_paths)
        self._signals = signals

    def run(self):
        try:
            removed = self._adb.delete_files(self._serial, self._remote_paths)
        except AdbError as error:
            self._signals.failed.emit(self._batch, self._serial, str(error))
            return
        self._signals.finished.emit(self._batch, self._serial, removed, len(self._remote_paths))


class NetworkSignals(QObject):
    finished = Signal(str, str, str)
    failed = Signal(str, str, str)


class NetworkTask(QRunnable):
    def __init__(self, adb: Adb, action: str, key: str, signals: NetworkSignals, hosts: list[str] | None = None):
        super().__init__()
        self._adb = adb
        self._action = action
        self._key = key
        self._signals = signals
        self._hosts = list(hosts or [])

    def run(self):
        try:
            if self._action == "wireless":
                address = self._adb.go_wireless(self._key, self._hosts)
            else:
                if not self._adb.connect(self._key):
                    raise AdbError(
                        f"couldn't connect to {self._key}. Plug the phone in once and use Go wireless first"
                    )
                address = self._key
        except AdbError as error:
            self._signals.failed.emit(self._action, self._key, str(error))
            return
        self._signals.finished.emit(self._action, self._key, address)


COPY_KEEP_SECONDS = 6 * 3600


class CopySignals(QObject):
    finished = Signal(int, object, object, object)
    failed = Signal(int, str)


class CopyTask(QRunnable):
    def __init__(self, number: int, items, layout: str, folder: Path, signals: CopySignals):
        super().__init__()
        self._number = number
        self._items = items
        self._layout = layout
        self._folder = folder
        self._signals = signals

    def run(self):
        self._tidy()
        if self._layout == "files":
            self._separate()
        else:
            self._merge()

    def _tidy(self) -> None:
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            limit = time.time() - COPY_KEEP_SECONDS
            for entry in self._folder.iterdir():
                if entry.is_file() and entry.stat().st_mtime < limit:
                    entry.unlink(missing_ok=True)
        except OSError:
            pass

    def _read(self, path: str, strokes) -> QImage:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read()
        if strokes and not image.isNull():
            image = annotate_image(image, strokes)
        return image

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%d_%H-%M-%S")

    def _separate(self) -> None:
        paths = []
        for path, strokes in self._items:
            if not strokes:
                paths.append(path)
                continue
            image = self._read(path, strokes)
            if image.isNull():
                continue
            target = self._folder / f"{safe_name(Path(path).stem)}_marked_{self._stamp()}.png"
            if image.save(str(target), "PNG", 50):
                paths.append(str(target))
        if not paths:
            self._signals.failed.emit(self._number, "The images can't be opened")
            return
        self._signals.finished.emit(self._number, None, paths, None)

    def _merge(self) -> None:
        usable = []
        sizes = []
        for path, strokes in self._items:
            size = oriented_size(path)
            if size.isValid() and not size.isEmpty():
                usable.append((path, strokes))
                sizes.append(size)
        if not usable:
            self._signals.failed.emit(self._number, "The images can't be opened")
            return
        layout_plan = plan(sizes, self._layout)
        image = draw((self._read(path, strokes) for path, strokes in usable), layout_plan)
        target = self._folder / f"Spona_{self._layout}_{len(usable)}_{self._stamp()}.png"
        paths = [str(target)] if image.save(str(target), "PNG", 50) else []
        self._signals.finished.emit(self._number, image, paths, layout_plan.rows)


class CallTask(QRunnable):
    def __init__(self, function, *arguments):
        super().__init__()
        self._function = function
        self._arguments = arguments

    def run(self):
        try:
            self._function(*self._arguments)
        except AdbError:
            pass


class NetworkReconnector(QThread):
    def __init__(self, adb: Adb, interval: float = 8.0, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._interval = interval
        self._lock = threading.Lock()
        self._addresses: list[str] = []
        self._stopping = threading.Event()
        self._wakeup = threading.Event()

    def set_addresses(self, addresses: list[str]) -> None:
        with self._lock:
            self._addresses = list(addresses)
        self._wakeup.set()

    def run(self):
        while not self._stopping.is_set():
            with self._lock:
                addresses = list(self._addresses)
            if addresses:
                try:
                    present = {device.serial: device.state for device in self._adb.devices()}
                except AdbError:
                    present = {}
                for address in addresses:
                    if self._stopping.is_set():
                        break
                    state = present.get(address)
                    if state == "device":
                        continue
                    if state is not None:
                        self._adb.disconnect(address)
                    self._adb.connect(address)
            self._wakeup.wait(self._interval)
            self._wakeup.clear()

    def stop(self):
        self._stopping.set()
        self._wakeup.set()
        if not self.wait(2000):
            self.terminate()
            self.wait(1000)


class ClipboardSession(threading.Thread):
    def __init__(self, adb: Adb, helper: Path, serial: str, bridge: "ClipboardBridge"):
        super().__init__(daemon=True)
        self._adb = adb
        self._helper = helper
        self.serial = serial
        self._bridge = bridge
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._process = None
        self._ready = False
        self._known: str | None = None
        self._pending: str | None = None

    def send(self, text: str) -> bool:
        with self._lock:
            if text == self._known:
                return False
            self._known = text
            self._pending = text
            self._changed.notify_all()
        return True

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            process = self._process
            self._changed.notify_all()
        if process is not None:
            _end_process(process)

    def run(self):
        threading.Thread(target=self._write_loop, daemon=True).start()
        delay = 2.0
        while not self._stopping.is_set():
            began = time.monotonic()
            error = self._session()
            if time.monotonic() - began > 60:
                delay = 2.0
            if self._stopping.wait(delay):
                break
            if error:
                self._bridge.failed.emit(self.serial, error)
            delay = min(delay * 2, 30.0)

    def _session(self) -> str | None:
        try:
            remote = self._adb.install_clip_helper(self.serial, self._helper)
            process = self._adb.open_shell(self.serial, clip_helper_command(remote))
        except (AdbError, OSError) as error:
            return str(error)
        with self._lock:
            self._process = process
        if self._stopping.is_set():
            _end_process(process)
        noise: list[str] = []
        try:
            for raw in process.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("R "):
                    with self._lock:
                        self._ready = True
                        self._changed.notify_all()
                    self._bridge.ready.emit(self.serial)
                elif line.startswith("C "):
                    try:
                        text = base64.b64decode(line[2:]).decode("utf-8", "replace")
                    except (binascii.Error, ValueError):
                        continue
                    with self._lock:
                        self._known = text
                    self._bridge.received.emit(self.serial, text)
                elif line.startswith("E "):
                    self._bridge.failed.emit(self.serial, line[2:])
                elif line and line != "A":
                    noise.append(line)
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                self._ready = False
                self._process = None
            _end_process(process)
            try:
                process.stdout.close()
            except (OSError, ValueError):
                pass
        return noise[-1] if noise else None

    def _write_loop(self) -> None:
        while True:
            with self._lock:
                while not self._stopping.is_set() and (self._pending is None or not self._ready):
                    self._changed.wait()
                if self._stopping.is_set():
                    return
                text, process = self._pending, self._process
                self._pending = None
            data = b"S " + base64.b64encode(text.encode("utf-8")) + b"\n"
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError, AttributeError):
                with self._lock:
                    if self._pending is None:
                        self._pending = text
                    if self._process is process:
                        self._ready = False


def _end_process(process) -> None:
    try:
        process.stdin.write(b"Q\n")
        process.stdin.flush()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        process.wait(0.4)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.stdin.close()
    except (OSError, ValueError, AttributeError):
        pass


class ClipboardBridge(QObject):
    received = Signal(str, str)
    ready = Signal(str)
    failed = Signal(str, str)

    def __init__(self, adb: Adb, helper: Path, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._helper = helper
        self._sessions: dict[str, ClipboardSession] = {}

    def configure(self, serials) -> None:
        wanted = set(serials)
        for serial in [serial for serial in self._sessions if serial not in wanted]:
            self._sessions.pop(serial).stop()
        for serial in sorted(wanted):
            if serial not in self._sessions:
                session = ClipboardSession(self._adb, self._helper, serial, self)
                self._sessions[serial] = session
                session.start()

    def send(self, text: str) -> int:
        return sum(1 for session in list(self._sessions.values()) if session.send(text))

    def stop(self) -> None:
        for session in self._sessions.values():
            session.stop()
        self._sessions.clear()
