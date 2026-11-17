import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage

from .adb import Adb, AdbError, Device


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
            data = self._grab()
            image = QImage.fromData(data)
            if image.isNull():
                raise AdbError("the phone returned an invalid image")
            self._target.parent.mkdir(parents=True, exist_ok=True)
            partial = self._target.with_name(self._target.name + ".part")
            partial.write_bytes(data)
            os.replace(partial, self._target)
            thumbnail = image.scaled(
                self._thumb_bound,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except (AdbError, OSError) as error:
            self._signals.failed.emit(self._batch, self._serial, str(error))
            return
        self._signals.captured.emit(self._batch, self._serial, str(self._target), image, thumbnail)


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
