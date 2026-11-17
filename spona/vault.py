import base64
import ctypes
import json
import sys
from pathlib import Path

from .settings import config_directory

CRYPTPROTECT_UI_FORBIDDEN = 0x1
ENTROPY = b"spona-device-secret"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi(protect: bool, data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    source, _source_buffer = _blob(data)
    entropy, _entropy_buffer = _blob(ENTROPY)
    target = _DataBlob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not function(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target),
    ):
        raise OSError("Windows DPAPI failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


class Vault:
    def __init__(self, path: Path | None = None):
        self.path = path or config_directory() / "vault.json"
        self.persistent = sys.platform == "win32"
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8")) if self.persistent else {}
        except (OSError, ValueError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def has(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> str | None:
        token = self._data.get(key)
        if not token:
            return None
        try:
            return _dpapi(False, base64.b64decode(token)).decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    def set(self, key: str, secret: str) -> bool:
        if not self.persistent:
            return False
        try:
            self._data[key] = base64.b64encode(_dpapi(True, secret.encode("utf-8"))).decode("ascii")
        except OSError:
            return False
        return self._save()

    def forget(self, key: str) -> None:
        if self._data.pop(key, None) is not None:
            self._save()

    def _save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            partial.replace(self.path)
        except OSError:
            return False
        return True
