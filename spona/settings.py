import json
import os
from pathlib import Path

APP_NAME = "Spona"


def config_directory() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or config_directory() / "settings.json"
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            partial.replace(self.path)
        except OSError:
            pass

    @property
    def save_dir(self) -> Path | None:
        value = self._data.get("save_dir")
        return Path(value) if value else None

    @save_dir.setter
    def save_dir(self, path: Path) -> None:
        self.set("save_dir", str(path))

    @property
    def deselected_devices(self) -> set[str]:
        return set(self._data.get("deselected_devices", []))

    @deselected_devices.setter
    def deselected_devices(self, serials: set[str]) -> None:
        self.set("deselected_devices", sorted(serials))
