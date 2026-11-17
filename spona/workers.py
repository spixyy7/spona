import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage

from .adb import Adb, AdbError, Device
from .naming import release_path, reserve_path


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

    def __init__(self, adb: Adb, thumb_bound: QSize, interval: float = 1.0, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._thumb_bound = thumb_bound
        self._interval = interval
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
            entry for entry in self._adb.recent_screenshots(serial)
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
            if path in seen or modified < since:
                continue
            if size > 0 and pending.get(path) == size:
                seen.add(path)
                pending.pop(path, None)
                self._receive(serial, path, folder, move)
            else:
                pending[path] = size

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
    finished = Signal(str, int, int)
    failed = Signal(str, str)


class PhoneDeleteTask(QRunnable):
    def __init__(self, adb: Adb, serial: str, remote_paths: list[str], signals: DeleteSignals):
        super().__init__()
        self._adb = adb
        self._serial = serial
        self._remote_paths = list(remote_paths)
        self._signals = signals

    def run(self):
        try:
            removed = self._adb.delete_files(self._serial, self._remote_paths)
        except AdbError as error:
            self._signals.failed.emit(self._serial, str(error))
            return
        self._signals.finished.emit(self._serial, removed, len(self._remote_paths))
