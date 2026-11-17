import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap


class Glyph:
    CAMERA = "\ue722"
    FULLSCREEN = "\ue740"
    EXIT_FULLSCREEN = "\ue73f"
    DELETE = "\ue74d"
    FOLDER = "\ue8b7"
    FOLDER_OPEN = "\ued25"
    MOVE_TO_FOLDER = "\ue8de"
    COPY = "\ue8c8"
    PHONE = "\ue8ea"
    PREVIOUS = "\ue76b"
    NEXT = "\ue76c"
    SEARCH = "\ue721"
    LOCK = "\ue72e"
    LINK = "\ue71b"
    PEN = "\ue70f"
    ERASER = "\ue75c"
    UNDO = "\ue7a7"
    REDO = "\ue7a6"
    CLEAR = "\ue894"
    SAVE = "\ue74e"
    ACCEPT = "\ue8fb"
    DOWNLOAD = "\ue896"
    PASTE = "\ue77f"
    SETTINGS = "\ue713"
    UNLOCK = "\ue785"


_icon_family: list[str] = []


def icon_font_family() -> str:
    if not _icon_family:
        available = set(QFontDatabase.families())
        _icon_family.append(
            next((name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if name in available), "Segoe UI Symbol")
        )
    return _icon_family[0]


def _pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(2.0, screen.devicePixelRatio() if screen else 1.0)


def glyph_pixmap(glyph: str, color: str, size: int) -> QPixmap:
    ratio = _pixel_ratio()
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    font = QFont(icon_font_family())
    font.setPixelSize(round(size * 0.82))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return pixmap


def glyph_icon(glyph: str, color: str = "#e3e5e8", size: int = 18, disabled: str = "#55585f") -> QIcon:
    icon = QIcon()
    normal = glyph_pixmap(glyph, color, size)
    icon.addPixmap(normal, QIcon.Mode.Normal)
    icon.addPixmap(normal, QIcon.Mode.Active)
    icon.addPixmap(normal, QIcon.Mode.Selected)
    icon.addPixmap(glyph_pixmap(glyph, disabled, size), QIcon.Mode.Disabled)
    return icon


def dot_icon(color: str, size: int = 10) -> QIcon:
    ratio = _pixel_ratio()
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    halo = QColor(color)
    halo.setAlpha(70)
    painter.setBrush(halo)
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6))
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal)
    icon.addPixmap(pixmap, QIcon.Mode.Active)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    return icon


def app_icon_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    inset = max(1.0, size * 0.04)
    rect = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#5be69c"))
    gradient.setColorAt(1.0, QColor("#1f9e5c"))
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    painter.fillPath(path, gradient)
    font = QFont(icon_font_family())
    font.setPixelSize(round(size * 0.6))
    painter.setFont(font)
    painter.setPen(QColor("#0c1f14"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, Glyph.PHONE)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


def export_ico(path: Path) -> None:
    images = []
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        app_icon_pixmap(size).save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    Path(path).write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + blobs)
