import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

MARKER_COLORS = ("#ff4d4f", "#ffb020", "#3ddc84", "#3ba7ff", "#ffffff", "#111111")


class Brush:
    def __init__(self, color: str = MARKER_COLORS[0], size: int = 8, opacity: int = 100, tool: str = "pen"):
        self.color = color
        self.size = size
        self.opacity = opacity
        self.tool = tool

    def to_dict(self) -> dict:
        return {"color": self.color, "size": self.size, "opacity": self.opacity}

    @classmethod
    def from_dict(cls, data) -> "Brush":
        if not isinstance(data, dict):
            return cls()
        brush = cls()
        if QColor.isValidColorName(str(data.get("color", ""))):
            brush.color = str(data["color"])
        brush.size = max(1, min(64, int(data.get("size", brush.size))))
        brush.opacity = max(5, min(100, int(data.get("opacity", brush.opacity))))
        return brush


class Stroke:
    __slots__ = ("points", "color", "width", "opacity", "_path")

    def __init__(self, points: list[QPointF], color: str, width: float, opacity: float):
        self.points = tuple(points)
        self.color = color
        self.width = width
        self.opacity = opacity
        self._path: QPainterPath | None = None

    @property
    def path(self) -> QPainterPath:
        if self._path is None:
            self._path = smooth_path(self.points)
        return self._path


def smooth_path(points) -> QPainterPath:
    path = QPainterPath(points[0])
    if len(points) == 1:
        path.lineTo(points[0] + QPointF(0.01, 0.0))
        return path
    if len(points) == 2:
        path.lineTo(points[1])
        return path
    for index in range(1, len(points) - 1):
        current = points[index]
        following = points[index + 1]
        path.quadTo(current, QPointF((current.x() + following.x()) / 2, (current.y() + following.y()) / 2))
    path.lineTo(points[-1])
    return path


def paint_stroke(painter: QPainter, path: QPainterPath, color: str, width: float, opacity: float) -> None:
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    pen = QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def paint_strokes(painter: QPainter, strokes) -> None:
    for stroke in strokes:
        paint_stroke(painter, stroke.path, stroke.color, stroke.width, stroke.opacity)


def render_annotated(pixmap: QPixmap, strokes) -> QImage:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_strokes(painter, strokes)
    painter.end()
    return image


def _segment_distance(point: QPointF, start: QPointF, end: QPointF) -> float:
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    length = dx * dx + dy * dy
    if length == 0:
        return math.hypot(point.x() - start.x(), point.y() - start.y())
    t = max(0.0, min(1.0, ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / length))
    return math.hypot(point.x() - (start.x() + t * dx), point.y() - (start.y() + t * dy))


def stroke_hit(stroke: Stroke, point: QPointF, radius: float) -> bool:
    limit = radius + stroke.width / 2
    points = stroke.points
    if len(points) == 1:
        return math.hypot(point.x() - points[0].x(), point.y() - points[0].y()) <= limit
    return any(_segment_distance(point, start, end) <= limit for start, end in zip(points, points[1:]))


class AnnotationStore:
    def __init__(self):
        self._strokes: dict[str, list[Stroke]] = {}
        self._undo: dict[str, list[list[Stroke]]] = {}
        self._redo: dict[str, list[list[Stroke]]] = {}

    def strokes(self, key: str) -> list[Stroke]:
        return self._strokes.get(key, [])

    def has(self, key: str) -> bool:
        return bool(self._strokes.get(key))

    def can_undo(self, key: str) -> bool:
        return bool(self._undo.get(key))

    def can_redo(self, key: str) -> bool:
        return bool(self._redo.get(key))

    def commit(self, key: str, strokes: list[Stroke], previous: list[Stroke] | None = None) -> None:
        self._undo.setdefault(key, []).append(list(self.strokes(key) if previous is None else previous))
        self._redo[key] = []
        self._strokes[key] = list(strokes)

    def set_silently(self, key: str, strokes: list[Stroke]) -> None:
        self._strokes[key] = list(strokes)

    def add(self, key: str, stroke: Stroke) -> None:
        self.commit(key, self.strokes(key) + [stroke])

    def clear(self, key: str) -> bool:
        if not self.has(key):
            return False
        self.commit(key, [])
        return True

    def undo(self, key: str) -> bool:
        stack = self._undo.get(key)
        if not stack:
            return False
        self._redo.setdefault(key, []).append(self.strokes(key))
        self._strokes[key] = stack.pop()
        return True

    def redo(self, key: str) -> bool:
        stack = self._redo.get(key)
        if not stack:
            return False
        self._undo.setdefault(key, []).append(self.strokes(key))
        self._strokes[key] = stack.pop()
        return True

    def forget(self, key: str) -> None:
        self._strokes.pop(key, None)
        self._undo.pop(key, None)
        self._redo.pop(key, None)
