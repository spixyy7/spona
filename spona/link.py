import json
import socket
import struct
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer, QTcpSocket

from .tailscale import MOBILE_SYSTEMS, tailnet_status

PORT = 47615
PROTOCOL = 1
PREFIX = struct.Struct(">II")
MAX_HEADER = 256 * 1024
MAX_PAYLOAD = 80 * 1024 * 1024
HELLO_MS = 8000
CONNECT_MS = 6000
PING_MS = 15000
SILENT_SECONDS = 50.0
SCAN_MS = 10000
RETRY_SECONDS = (15, 30, 60, 120)
SENT_KEEP_SECONDS = 600.0


@dataclass(frozen=True)
class Neighborhood:
    available: bool
    reason: str
    name: str
    listen: tuple[str, ...]
    candidates: tuple[tuple[str, str], ...]
    trusted: frozenset


def tailnet_neighborhood() -> Neighborhood:
    status = tailnet_status()
    if status is None:
        return Neighborhood(False, "missing", "", (), (), frozenset())
    if not status.running:
        return Neighborhood(False, "stopped", status.name, (), (), frozenset())
    mine = [peer for peer in status.peers if peer.mine]
    return Neighborhood(
        True,
        "",
        status.name,
        status.ips,
        tuple((peer.name, peer.ip) for peer in mine if peer.online and peer.os not in MOBILE_SYSTEMS),
        frozenset(peer.ip for peer in mine),
    )


def plain_address(address: QHostAddress) -> str:
    text = address.toString()
    return text[7:] if text.startswith("::ffff:") else text


@dataclass(eq=False)
class Peer:
    id: str
    name: str
    address: str
    version: str = ""
    host: bool = False
    since: float = 0.0
    phones: list = field(default_factory=list)
    channel: "Channel | None" = None
    sent: dict = field(default_factory=dict)
    last_clip: tuple = ()

    @property
    def rank(self) -> tuple[float, str]:
        return self.since, self.id


class Channel(QObject):
    message = Signal(object, dict, bytes)
    closed = Signal(object)

    def __init__(self, sock: QTcpSocket, outgoing: bool, address: str, parent=None):
        super().__init__(parent)
        self.socket = sock
        self.outgoing = outgoing
        self.address = address
        self.peer: Peer | None = None
        self.heard = time.monotonic()
        self._buffer = bytearray()
        self._closed = False
        sock.setParent(self)
        sock.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
        sock.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
        sock.readyRead.connect(self._read)
        sock.disconnected.connect(self.close)
        sock.errorOccurred.connect(lambda _error: self.close())
        if sock.bytesAvailable():
            QTimer.singleShot(0, self._read)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def send(self, header: dict, payload: bytes = b"") -> bool:
        if self._closed:
            return False
        data = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.socket.write(PREFIX.pack(len(data), len(payload)) + data)
        if payload:
            self.socket.write(payload)
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.socket.abort()
        except RuntimeError:
            pass
        self.closed.emit(self)
        self.deleteLater()

    def _read(self) -> None:
        if self._closed:
            return
        chunk = self.socket.readAll().data()
        if not chunk:
            return
        self._buffer += chunk
        self.heard = time.monotonic()
        while not self._closed and len(self._buffer) >= PREFIX.size:
            header_size, payload_size = PREFIX.unpack_from(self._buffer)
            if header_size > MAX_HEADER or payload_size > (MAX_PAYLOAD if self.peer is not None else 0):
                self.close()
                return
            total = PREFIX.size + header_size + payload_size
            if len(self._buffer) < total:
                return
            raw = bytes(self._buffer[PREFIX.size:PREFIX.size + header_size])
            payload = bytes(self._buffer[PREFIX.size + header_size:total])
            del self._buffer[:total]
            try:
                header = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self.close()
                return
            if not isinstance(header, dict):
                self.close()
                return
            self.message.emit(self, header, payload)


class _ScanSignals(QObject):
    done = Signal(object)


class _ScanTask(QRunnable):
    def __init__(self, discover, signals: _ScanSignals):
        super().__init__()
        self._discover = discover
        self._signals = signals

    def run(self):
        try:
            found = self._discover()
        except Exception:
            found = None
        self._signals.done.emit(found)


class Link(QObject):
    peers_changed = Signal()
    received = Signal(object, dict, bytes)
    neighborhood_changed = Signal()

    def __init__(
        self,
        identity: str,
        version: str,
        port: int = PORT,
        peer_port: int | None = None,
        discover=tailnet_neighborhood,
        parent=None,
    ):
        super().__init__(parent)
        self.identity = identity
        self.version = version
        self.name = socket.gethostname()
        self._port = port
        self._peer_port = peer_port or port
        self._discover = discover
        self._state = {"host": False, "since": 0.0, "phones": []}
        self._servers: dict[str, QTcpServer] = {}
        self._listen_error = ""
        self._pending: set[Channel] = set()
        self._dialing: dict[str, QTcpSocket] = {}
        self._retry: dict[str, tuple[float, int]] = {}
        self._peers: dict[str, Peer] = {}
        self._hood: Neighborhood | None = None
        self._scanning = False
        self._running = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._scan_signals = _ScanSignals(self)
        self._scan_signals.done.connect(self._on_scan)
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_MS)
        self._scan_timer.timeout.connect(self.scan)
        self._ping_timer = QTimer(self)
        self._ping_timer.setInterval(PING_MS)
        self._ping_timer.timeout.connect(self._ping)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def neighborhood(self) -> Neighborhood | None:
        return self._hood

    @property
    def listen_error(self) -> str:
        return self._listen_error

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._scan_timer.start()
        self._ping_timer.start()
        self.scan()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._scan_timer.stop()
        self._ping_timer.stop()
        for server in self._servers.values():
            server.close()
            server.deleteLater()
        self._servers.clear()
        for sock in self._dialing.values():
            sock.abort()
            sock.deleteLater()
        self._dialing.clear()
        self._retry.clear()
        for channel in list(self._pending):
            channel.close()
        self._pending.clear()
        peers = list(self._peers.values())
        self._peers.clear()
        had = bool(peers)
        for peer in peers:
            if peer.channel is not None:
                peer.channel.close()
        self._hood = None
        self.neighborhood_changed.emit()
        if had:
            self.peers_changed.emit()

    def peers(self) -> list[Peer]:
        return sorted(self._peers.values(), key=lambda peer: (peer.name.lower(), peer.id))

    def peer(self, identity: str) -> Peer | None:
        return self._peers.get(identity)

    def set_state(self, host: bool, since: float, phones: list) -> None:
        state = {"host": bool(host), "since": float(since if host else 0.0), "phones": list(phones if host else [])}
        if state == self._state:
            return
        self._state = state
        for peer in list(self._peers.values()):
            self.send(peer.id, self._state_message("state"))

    def send(self, identity: str, header: dict, payload: bytes = b"") -> bool:
        peer = self._peers.get(identity)
        if peer is None or peer.channel is None:
            return False
        return peer.channel.send(header, payload)

    def mark_sent(self, peer: Peer, digest: str) -> bool:
        now = time.monotonic()
        for key in [key for key, when in peer.sent.items() if now - when > SENT_KEEP_SECONDS]:
            del peer.sent[key]
        if digest in peer.sent:
            return False
        peer.sent[digest] = now
        return True

    def scan(self) -> None:
        if not self._running or self._scanning:
            return
        self._scanning = True
        self._pool.start(_ScanTask(self._discover, self._scan_signals))

    def _state_message(self, kind: str) -> dict:
        message = {"t": kind, "name": self.name, **self._state}
        if kind == "hello":
            message.update({"app": "spona", "proto": PROTOCOL, "id": self.identity, "version": self.version})
        return message

    def _on_scan(self, hood) -> None:
        self._scanning = False
        if not self._running:
            return
        if not isinstance(hood, Neighborhood):
            hood = Neighborhood(False, "stopped", "", (), (), frozenset())
        previous = self._hood
        self._hood = hood
        if hood.name and hood.name != self.name:
            self.name = hood.name
            for peer in list(self._peers.values()):
                self.send(peer.id, self._state_message("state"))
        self._listen(hood.listen)
        for peer in list(self._peers.values()):
            if peer.address not in hood.trusted and peer.channel is not None:
                peer.channel.close()
        online = {address for _name, address in hood.candidates}
        was_online = {address for _name, address in previous.candidates} if previous else set()
        for address in online - was_online:
            self._retry.pop(address, None)
        self._dial(hood.candidates)
        if previous != hood:
            self.neighborhood_changed.emit()

    def _listen(self, addresses: tuple[str, ...]) -> None:
        wanted = set(addresses)
        for address in [address for address in self._servers if address not in wanted]:
            server = self._servers.pop(address)
            server.close()
            server.deleteLater()
        errors = []
        for address in addresses:
            if address in self._servers:
                continue
            server = QTcpServer(self)
            if not server.listen(QHostAddress(address), self._port):
                errors.append(server.errorString())
                server.deleteLater()
                continue
            server.newConnection.connect(lambda source=server: self._accept(source))
            self._servers[address] = server
        self._listen_error = errors[0] if errors else ""

    def _accept(self, server: QTcpServer) -> None:
        trusted = self._hood.trusted if self._hood else frozenset()
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            if sock is None:
                break
            address = plain_address(sock.peerAddress())
            if not self._running or address not in trusted:
                sock.abort()
                sock.deleteLater()
                continue
            self._adopt(Channel(sock, False, address, self))

    def _dial(self, candidates) -> None:
        now = time.monotonic()
        busy = set(self._dialing) | {channel.address for channel in self._pending}
        busy |= {peer.address for peer in self._peers.values()}
        for _name, address in candidates:
            if address in busy:
                continue
            retry = self._retry.get(address)
            if retry is not None and retry[0] > now:
                continue
            sock = QTcpSocket(self)
            self._dialing[address] = sock
            sock.connected.connect(lambda value=address, source=sock: self._dialed(value, source))
            sock.errorOccurred.connect(lambda _error, value=address, source=sock: self._dial_failed(value, source))
            QTimer.singleShot(CONNECT_MS, lambda value=address, source=sock: self._dial_failed(value, source))
            sock.connectToHost(address, self._peer_port)

    def _dialed(self, address: str, sock: QTcpSocket) -> None:
        if self._dialing.get(address) is not sock:
            return
        del self._dialing[address]
        self._retry.pop(address, None)
        self._adopt(Channel(sock, True, address, self))

    def _dial_failed(self, address: str, sock: QTcpSocket) -> None:
        if self._dialing.get(address) is not sock:
            return
        del self._dialing[address]
        attempts = self._retry.get(address, (0.0, 0))[1] + 1
        delay = RETRY_SECONDS[min(attempts, len(RETRY_SECONDS)) - 1]
        self._retry[address] = (time.monotonic() + delay, attempts)
        sock.abort()
        sock.deleteLater()

    def _adopt(self, channel: Channel) -> None:
        self._pending.add(channel)
        channel.message.connect(self._on_message)
        channel.closed.connect(self._on_closed)
        channel.send(self._state_message("hello"))
        QTimer.singleShot(HELLO_MS, lambda source=channel: source.peer is None and source.close())

    def _on_message(self, channel: Channel, header: dict, payload: bytes) -> None:
        peer = channel.peer
        if peer is None:
            self._hello(channel, header)
            return
        if self._peers.get(peer.id) is not peer:
            return
        kind = header.get("t")
        if kind == "ping":
            return
        if kind == "state":
            if self._update(peer, header):
                self.peers_changed.emit()
            return
        self.received.emit(peer, header, payload)

    def _hello(self, channel: Channel, header: dict) -> None:
        identity = header.get("id")
        if (
            header.get("t") != "hello"
            or header.get("app") != "spona"
            or header.get("proto") != PROTOCOL
            or not isinstance(identity, str)
            or not identity
            or len(identity) > 64
            or identity == self.identity
        ):
            channel.close()
            return
        existing = self._peers.get(identity)
        if existing is not None and existing.channel is not None and not existing.channel.is_closed:
            if not self._prefer(channel, existing.channel, identity):
                channel.close()
                return
        peer = Peer(identity, str(header.get("name") or identity)[:64], channel.address, str(header.get("version") or "")[:24])
        peer.channel = channel
        channel.peer = peer
        self._pending.discard(channel)
        self._peers[identity] = peer
        self._update(peer, header)
        if existing is not None and existing.channel is not None:
            existing.channel.close()
        self.peers_changed.emit()

    def _prefer(self, new: Channel, old: Channel, identity: str) -> bool:
        first = min(self.identity, identity)
        new_starter = self.identity if new.outgoing else identity
        old_starter = self.identity if old.outgoing else identity
        if new_starter == old_starter:
            return True
        return new_starter == first

    def _update(self, peer: Peer, header: dict) -> bool:
        phones = header.get("phones")
        phones = [phone for phone in phones if isinstance(phone, dict) and phone.get("id")][:32] if isinstance(phones, list) else []
        try:
            since = float(header.get("since") or 0.0)
        except (TypeError, ValueError):
            since = 0.0
        values = (
            str(header.get("name") or peer.name)[:64],
            bool(header.get("host")),
            since,
            phones,
        )
        if values == (peer.name, peer.host, peer.since, peer.phones):
            return False
        peer.name, peer.host, peer.since, peer.phones = values
        return True

    def _on_closed(self, channel: Channel) -> None:
        self._pending.discard(channel)
        peer = channel.peer
        if peer is not None and self._peers.get(peer.id) is peer:
            del self._peers[peer.id]
            self.peers_changed.emit()

    def _ping(self) -> None:
        now = time.monotonic()
        for peer in list(self._peers.values()):
            channel = peer.channel
            if channel is None:
                continue
            if now - channel.heard > SILENT_SECONDS:
                channel.close()
            else:
                channel.send({"t": "ping"})
