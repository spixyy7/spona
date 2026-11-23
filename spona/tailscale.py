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
MOBILE_SYSTEMS = ("android", "ios")


@dataclass(frozen=True)
class TailscalePeer:
    name: str
    ip: str
    os: str
    online: bool
    mine: bool = True


@dataclass(frozen=True)
class Tailnet:
    running: bool
    name: str
    ips: tuple[str, ...]
    peers: tuple[TailscalePeer, ...]


def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return next((path for path in KNOWN_LOCATIONS if Path(path).is_file()), None)


def _status(cli: str) -> dict | None:
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
        return None
    return data if isinstance(data, dict) else None


def _ipv4(node: dict) -> list[str]:
    return [ip for ip in node.get("TailscaleIPs") or [] if "." in ip]


def _peers(data: dict) -> list[TailscalePeer]:
    me = (data.get("Self") or {}).get("UserID")
    peers = []
    for peer in (data.get("Peer") or {}).values():
        ips = _ipv4(peer)
        if not ips:
            continue
        name = peer.get("HostName") or (peer.get("DNSName") or "").split(".")[0] or ips[0]
        peers.append(
            TailscalePeer(
                name,
                ips[0],
                (peer.get("OS") or "").lower(),
                bool(peer.get("Online")),
                me is not None and peer.get("UserID") == me,
            )
        )
    return peers


def tailscale_peers() -> list[TailscalePeer]:
    cli = find_tailscale()
    data = _status(cli) if cli else None
    if data is None:
        return []
    peers = _peers(data)
    peers.sort(key=lambda peer: (peer.os != "android", not peer.online, peer.name.lower()))
    return peers


def tailnet_status() -> Tailnet | None:
    cli = find_tailscale()
    if cli is None:
        return None
    data = _status(cli)
    if data is None:
        return Tailnet(False, "", (), ())
    me = data.get("Self") or {}
    running = data.get("BackendState") == "Running"
    name = me.get("HostName") or (me.get("DNSName") or "").split(".")[0]
    return Tailnet(running, name, tuple(_ipv4(me)) if running else (), tuple(_peers(data)) if running else ())
