import json
import os
from pathlib import Path

from .settings import config_directory


class PhoneOrigins:
    def __init__(self, path: Path | None = None):
        self.path = path or config_directory() / "origins.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._data = {
            key: value
            for key, value in data.items()
            if isinstance(value, dict) and value.get("serial") and value.get("remote") and os.path.exists(key)
        }

    def get(self, local: str) -> dict | None:
        return self._data.get(os.path.normpath(local))

    def remember(self, local: str, serial: str, remote: str, device: str) -> None:
        self._data[os.path.normpath(local)] = {"serial": serial, "remote": remote, "device": device}
        self._save()

    def forget(self, locals_: list[str]) -> None:
        changed = False
        for local in locals_:
            changed = self._data.pop(os.path.normpath(local), None) is not None or changed
        if changed:
            self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            partial.replace(self.path)
        except OSError:
            pass
