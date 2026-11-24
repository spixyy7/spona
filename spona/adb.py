import hashlib
import os
import re
import shlex
import shutil
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import quote

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ADB_EXECUTABLE = "adb.exe" if sys.platform == "win32" else "adb"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MARKET_NAME_PROPERTIES = (
    "ro.vendor.oplus.market.enname",
    "ro.product.marketname",
    "ro.product.vendor.marketname",
    "ro.product.odm.marketname",
    "ro.product.system.marketname",
    "ro.config.marketing_name",
    "ro.vendor.oplus.market.name",
)
PROPERTY_LINE = re.compile(r"^\[(?P<key>[^\]]+)\]:\s*\[(?P<value>.*)\]\s*$")
PHONE_SCREENSHOT_FOLDERS = ("/sdcard/Pictures/Screenshots", "/sdcard/DCIM/Screenshots", "/sdcard/Screenshots")
def recent_screenshots_command(folders, per_folder: int = 4) -> str:
    return (
        "for d in " + " ".join(folders) + "; do "
        '[ -d "$d" ] && cd "$d" && ls -t 2>/dev/null | head -n ' + str(per_folder) + ' | '
        'xargs stat -c "%s|%Y|$d/%n" 2>/dev/null; '
        "done; true"
    )


SCREEN_STATE_COMMAND = (
    'dumpsys power 2>/dev/null | grep -m1 "mWakefulness="; '
    'dumpsys window 2>/dev/null | grep -m1 "isKeyguardShowing="; '
    'dumpsys trust 2>/dev/null | grep -m1 -o "deviceLocked=[01]"; '
    "true"
)


DEFAULT_TCP_PORT = 5555
PHONE_TEMP_FOLDER = "/data/local/tmp"
CLIP_HELPER_CLASS = "app.spona.ClipHelper"


def clip_helper_command(remote: str) -> str:
    return f"CLASSPATH={shlex.quote(remote)} app_process / {CLIP_HELPER_CLASS}"


class AdbError(Exception):
    pass


def is_tailscale_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        return False
    return parts[0] == "100" and 64 <= int(parts[1]) <= 127


def normalize_address(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    host, separator, port = text.rpartition(":")
    if separator and port.isdigit() and host:
        return text
    return f"{text}:{DEFAULT_TCP_PORT}"


class UnlockOutcome(Enum):
    ALREADY = "already"
    UNLOCKED = "unlocked"
    NEEDS_SECRET = "needs_secret"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ScreenState:
    awake: bool
    locked: bool


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    manufacturer: str = ""
    market_name: str = ""
    android: str = ""
    screen_on: bool | None = None
    locked: bool | None = None
    hardware_serial: str = ""
    also_over: str = ""
    via: str = ""
    owner: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def remote(self) -> bool:
        return bool(self.owner)

    @property
    def network(self) -> bool:
        return ":" in self.serial and not self.owner

    @property
    def transport(self) -> str:
        if self.owner:
            return f"via {self.via}"
        if not self.network:
            return "USB"
        return "Tailscale" if is_tailscale_ip(self.serial.rsplit(":", 1)[0]) else "Wi-Fi"

    @property
    def identity(self) -> str:
        return self.hardware_serial or self.serial

    @property
    def name(self) -> str:
        if self.market_name:
            return self.market_name
        model = self.model.replace("_", " ").strip()
        if model and self.manufacturer and not model.lower().startswith(self.manufacturer.lower()):
            return f"{self.manufacturer} {model}"
        return model or self.product or self.serial


def _candidate_roots() -> list[Path]:
    roots = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk")
    roots.append(Path.home() / "Android" / "Sdk")
    roots.append(Path.home() / "Library" / "Android" / "sdk")
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots += [drive / "Android" / "Sdk", drive]
    return roots


def find_adb(preferred: str | None = None) -> str | None:
    if preferred and Path(preferred).is_file():
        return preferred
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    for root in _candidate_roots():
        candidate = root / "platform-tools" / ADB_EXECUTABLE
        if candidate.is_file():
            return str(candidate)
    return None


class Adb:
    def __init__(self, path: str):
        self.path = path

    def _run(self, arguments: list[str], serial: str | None = None, timeout: float = 15.0) -> bytes:
        command = [self.path]
        if serial:
            command += ["-s", serial]
        command += arguments
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as error:
            raise AdbError("adb did not respond in time") from error
        except OSError as error:
            raise AdbError(f"adb can't be started: {error}") from error
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            message = message or result.stdout.decode("utf-8", "replace").strip()
            raise AdbError(message or f"adb exited with code {result.returncode}")
        return result.stdout

    def start_server(self) -> None:
        try:
            subprocess.run(
                [self.path, "start-server"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            raise AdbError(f"adb server can't be started: {error}") from error

    def devices(self) -> list[Device]:
        output = self._run(["devices", "-l"], timeout=10).decode("utf-8", "replace")
        devices = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            fields = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
            devices.append(Device(parts[0], parts[1], fields.get("model", ""), fields.get("product", "")))
        return devices

    def properties(self, serial: str) -> dict[str, str]:
        output = self._run(["shell", "getprop"], serial=serial, timeout=10).decode("utf-8", "replace")
        properties = {}
        for line in output.splitlines():
            match = PROPERTY_LINE.match(line.strip())
            if match:
                properties[match["key"]] = match["value"]
        return properties

    def describe(self, device: Device) -> Device:
        properties = self.properties(device.serial)
        market_name = next((properties[key] for key in MARKET_NAME_PROPERTIES if properties.get(key)), "")
        return Device(
            serial=device.serial,
            state=device.state,
            model=properties.get("ro.product.model", device.model),
            product=device.product,
            manufacturer=properties.get("ro.product.manufacturer", ""),
            market_name=market_name,
            android=properties.get("ro.build.version.release", ""),
            hardware_serial=properties.get("ro.serialno") or properties.get("ro.boot.serialno", ""),
        )

    def connect(self, address: str) -> bool:
        try:
            output = self._run(["connect", address], timeout=20).decode("utf-8", "replace")
        except AdbError:
            output = ""
        if "connected to" in output:
            return True
        self.disconnect(address)
        time.sleep(0.8)
        self.disconnect(address)
        return False

    def disconnect(self, address: str) -> None:
        try:
            self._run(["disconnect", address], timeout=8)
        except AdbError:
            pass

    def ipv4_addresses(self, serial: str) -> list[str]:
        output = self._run(["shell", "ip -4 -o addr show 2>/dev/null; true"], serial=serial, timeout=8)
        found = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", output.decode("utf-8", "replace"))
        return [ip for ip in found if not ip.startswith("127.")]

    def go_wireless(self, serial: str, extra_hosts: list[str], port: int = DEFAULT_TCP_PORT) -> str:
        properties = self.properties(serial)
        hardware = properties.get("ro.serialno") or properties.get("ro.boot.serialno", "")
        phone_ips = self.ipv4_addresses(serial)
        self._run(["tcpip", str(port)], serial=serial, timeout=15)
        time.sleep(2.5)
        hosts: list[str] = []
        ordered = [ip for ip in phone_ips if is_tailscale_ip(ip)] + list(extra_hosts)
        ordered += [ip for ip in phone_ips if not is_tailscale_ip(ip)]
        for host in ordered:
            if host not in hosts:
                hosts.append(host)
        for host in hosts:
            address = f"{host}:{port}"
            if not self.connect(address):
                continue
            try:
                found = self.properties(address)
            except AdbError:
                found = {}
            if not hardware or (found.get("ro.serialno") or found.get("ro.boot.serialno", "")) == hardware:
                return address
            self.disconnect(address)
        raise AdbError("couldn't reach the phone over the network. Turn Tailscale on on the phone and try again")

    def screencap(self, serial: str) -> bytes:
        data = self._run(["exec-out", "screencap", "-p"], serial=serial, timeout=25)
        start = data.find(PNG_SIGNATURE)
        if start < 0:
            raise AdbError("the phone did not return a screenshot")
        return data[start:]

    def recent_screenshots(self, serial: str, folders=PHONE_SCREENSHOT_FOLDERS) -> list[tuple[str, int, int]]:
        output = self._run(["shell", recent_screenshots_command(folders)], serial=serial, timeout=10)
        entries = []
        for line in output.decode("utf-8", "replace").splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            entries.append((parts[2], int(parts[0]), int(parts[1])))
        return entries

    def delete_files(self, serial: str, paths: list[str]) -> int:
        script = []
        for path in paths:
            quoted = shlex.quote(path)
            if not any(character in path for character in "'\"`$\\"):
                storage = "/storage/emulated/0/" + path[len("/sdcard/"):] if path.startswith("/sdcard/") else path
                where = f"_data='{storage}' OR _data='{path}'"
                script.append(
                    f'content delete --uri content://media/external/images/media --where "{where}" >/dev/null 2>&1'
                )
            script.append(f"rm -f {quoted}")
            script.append(f"[ -e {quoted} ] && echo KEEP || echo GONE")
        output = self._run(["shell", "; ".join(script)], serial=serial, timeout=30).decode("utf-8", "replace")
        return output.split().count("GONE")

    def push(self, serial: str, local: Path, remote: str) -> None:
        self._run(["push", str(local), remote], serial=serial, timeout=60)

    def restore_files(self, serial: str, items: list[tuple[str, str]]) -> int:
        restored = 0
        for local, remote in items:
            quoted = shlex.quote(remote)
            folder = shlex.quote(remote.rsplit("/", 1)[0])
            check = f"[ -e {quoted} ] && echo KEPT || mkdir -p {folder}"
            if b"KEPT" in self._run(["shell", check], serial=serial, timeout=10):
                restored += 1
                continue
            self.push(serial, Path(local), remote)
            storage = "/storage/emulated/0/" + remote[len("/sdcard/"):] if remote.startswith("/sdcard/") else remote
            address = shlex.quote("file://" + quote(storage))
            scan = f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d {address} >/dev/null 2>&1; true"
            self._run(["shell", scan], serial=serial, timeout=15)
            restored += 1
        return restored

    def install_clip_helper(self, serial: str, helper: Path) -> str:
        digest = hashlib.sha1(helper.read_bytes()).hexdigest()[:12]
        remote = f"{PHONE_TEMP_FOLDER}/spona-clip-{digest}.dex"
        script = (
            f"[ -s {remote} ] && echo READY; "
            f"for old in {PHONE_TEMP_FOLDER}/spona-clip-*.dex; do "
            f'[ "$old" = {remote} ] || rm -f "$old"; done; true'
        )
        if b"READY" not in self._run(["shell", script], serial=serial, timeout=10):
            self.push(serial, helper, remote)
        return remote

    def open_shell(self, serial: str, command: str) -> subprocess.Popen:
        try:
            return subprocess.Popen(
                [self.path, "-s", serial, "shell", "-T", command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as error:
            raise AdbError(f"adb can't be started: {error}") from error

    def file_size(self, serial: str, path: str) -> int:
        output = self._run(["shell", "stat -c %s " + shlex.quote(path) + " 2>/dev/null; true"], serial=serial, timeout=8)
        text = output.decode("utf-8", "replace").strip()
        return int(text) if text.isdigit() else -1

    def read_file(self, serial: str, path: str) -> bytes:
        return self._run(["exec-out", "cat " + shlex.quote(path)], serial=serial, timeout=30)

    def screen_state(self, serial: str) -> ScreenState:
        output = self._run(["shell", SCREEN_STATE_COMMAND], serial=serial, timeout=8).decode("utf-8", "replace")
        awake = "mWakefulness=" not in output or "mWakefulness=Awake" in output
        locked = "isKeyguardShowing=true" in output or "deviceLocked=1" in output
        return ScreenState(awake, locked)

    def keyevent(self, serial: str, key: str) -> None:
        self._run(["shell", "input", "keyevent", key], serial=serial, timeout=8)

    def wake(self, serial: str) -> None:
        self.keyevent(serial, "KEYCODE_WAKEUP")

    def sleep(self, serial: str) -> None:
        self.keyevent(serial, "KEYCODE_SLEEP")

    def screen_size(self, serial: str) -> tuple[int, int]:
        output = self._run(["shell", "wm", "size"], serial=serial, timeout=8).decode("utf-8", "replace")
        sizes = re.findall(r"(\d+)x(\d+)", output)
        if not sizes:
            return 1080, 2400
        width, height = sizes[-1]
        return int(width), int(height)

    def reveal_unlock_screen(self, serial: str) -> None:
        try:
            self._run(["shell", "wm", "dismiss-keyguard"], serial=serial, timeout=8)
        except AdbError:
            width, height = self.screen_size(serial)
            x = str(width // 2)
            self._run(
                ["shell", "input", "swipe", x, str(int(height * 0.85)), x, str(int(height * 0.3)), "250"],
                serial=serial,
                timeout=8,
            )

    def type_text(self, serial: str, text: str) -> None:
        self._run(["shell", "input text " + shlex.quote(text.replace(" ", "%s"))], serial=serial, timeout=8)

    def _wait_until_unlocked(self, serial: str, attempts: int, delay: float) -> bool:
        for _attempt in range(attempts):
            time.sleep(delay)
            if not self.screen_state(serial).locked:
                return True
        return False

    def unlock(self, serial: str, secret: str | None) -> UnlockOutcome:
        state = self.screen_state(serial)
        woke = False
        if not state.awake:
            self.wake(serial)
            woke = True
            time.sleep(0.6)
            state = self.screen_state(serial)
        if not state.locked:
            return UnlockOutcome.UNLOCKED if woke else UnlockOutcome.ALREADY
        self.reveal_unlock_screen(serial)
        if self._wait_until_unlocked(serial, attempts=2, delay=0.35):
            return UnlockOutcome.UNLOCKED
        if not secret:
            return UnlockOutcome.NEEDS_SECRET
        self.type_text(serial, secret)
        if self._wait_until_unlocked(serial, attempts=3, delay=0.3):
            return UnlockOutcome.UNLOCKED
        self.keyevent(serial, "KEYCODE_ENTER")
        if self._wait_until_unlocked(serial, attempts=6, delay=0.35):
            return UnlockOutcome.UNLOCKED
        return UnlockOutcome.REJECTED
