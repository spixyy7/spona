import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .adb import CREATE_NO_WINDOW

KNOWN_LOCATIONS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/bin/tailscale",
)


@dataclass(frozen=True)
class TailscalePeer:
    name: str
    ip: str
    os: str
    online: bool


def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return next((path for path in KNOWN_LOCATIONS if Path(path).is_file()), None)


def tailscale_peers() -> list[TailscalePeer]:
    cli = find_tailscale()
    if cli is None:
        return []
    try:
        result = subprocess.run(
            [cli, "status", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    peers = []
    for peer in (data.get("Peer") or {}).values():
        ips = [ip for ip in peer.get("TailscaleIPs") or [] if "." in ip]
        if not ips:
            continue
        name = peer.get("HostName") or (peer.get("DNSName") or "").split(".")[0] or ips[0]
        peers.append(TailscalePeer(name, ips[0], (peer.get("OS") or "").lower(), bool(peer.get("Online"))))
    peers.sort(key=lambda peer: (peer.os != "android", not peer.online, peer.name.lower()))
    return peers
