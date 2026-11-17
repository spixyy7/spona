import re
import threading
from pathlib import Path

_reserve_lock = threading.Lock()
_reserved: set[Path] = set()


def safe_name(text: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", text).strip("-.")
    return cleaned or "phone"


def reserve_path(folder: Path, stem: str, suffix: str) -> Path:
    with _reserve_lock:
        candidate = folder / f"{stem}{suffix}"
        counter = 2
        while candidate in _reserved or candidate.exists():
            candidate = folder / f"{stem}_{counter}{suffix}"
            counter += 1
        _reserved.add(candidate)
        return candidate


def release_path(path: Path) -> None:
    with _reserve_lock:
        _reserved.discard(path)
