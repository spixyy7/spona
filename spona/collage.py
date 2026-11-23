import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QImageIOHandler, QPainter, QPainterPath

COPY_LAYOUTS = (
    ("grid", "Grid  ·  one image, laid out to fit"),
    ("row", "Side by side  ·  one image, one row"),
    ("files", "Separate images  ·  each one on its own"),
)
LAYOUT_NAMES = {"grid": "as a grid", "row": "side by side", "files": "separately"}
BACKGROUND = "#131417"
GRID_TARGET = 1.3
LAST_ROW_LIMIT = 1.0
MIXED_LAST_ROW_LIMIT = 1.6
MIXED_SPREAD = 1.15
UNEVEN_WEIGHT = 0.35
EMPTY_WEIGHT = 2.0
MAX_ROWS = 40
WIDEST_ROW = 2.5
MAX_SIDE = 16000
MAX_PIXELS = 30_000_000
GAP_RATIO = 0.028
CORNER_RATIO = 0.022


@dataclass
class Plan:
    width: int
    height: int
    tiles: list[tuple[int, int, int, int]]
    rows: list[int]
    gap: int
    radius: int


def oriented_size(path: str) -> QSize:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
        size = size.transposed()
    return size


def _partition(aspects: list[float], rows: int) -> list[int]:
    count = len(aspects)
    prefix = [0.0]
    for aspect in aspects:
        prefix.append(prefix[-1] + aspect)
    mean = prefix[-1] / rows
    infinity = float("inf")
    best = [[infinity] * (count + 1) for _ in range(rows + 1)]
    split = [[0] * (count + 1) for _ in range(rows + 1)]
    best[0][0] = 0.0
    for row in range(1, rows + 1):
        for end in range(row, count - (rows - row) + 1):
            for start in range(end - 1, row - 2, -1):
                group = prefix[end] - prefix[start]
                if group > WIDEST_ROW * mean and start < end - 1:
                    break
                previous = best[row - 1][start]
                if previous == infinity:
                    continue
                cost = previous + (group - mean) ** 2
                if cost < best[row][end] - 1e-9:
                    best[row][end] = cost
                    split[row][end] = start
    sizes = []
    end = count
    for row in range(rows, 0, -1):
        start = split[row][end]
        sizes.append(end - start)
        end = start
    sizes.reverse()
    return sizes


def _even_rows(count: int, rows: int) -> list[int] | None:
    columns = math.ceil(count / rows)
    last = count - columns * (rows - 1)
    if last <= 0:
        return None
    return [columns] * (rows - 1) + [last]


def _mixed(aspects: list[float]) -> bool:
    return max(aspects) / min(aspects) > MIXED_SPREAD


def _row_heights(aspects: list[float], sizes: list[int], width: float, gap: float) -> list[float]:
    heights = []
    index = 0
    for size in sizes:
        total = sum(aspects[index : index + size])
        heights.append((width - gap * (size - 1)) / total)
        index += size
    if len(heights) > 1:
        limit = (MIXED_LAST_ROW_LIMIT if _mixed(aspects) else LAST_ROW_LIMIT) * sum(heights[:-1]) / (len(heights) - 1)
        heights[-1] = min(heights[-1], limit)
    return heights


def _score(aspects: list[float], sizes: list[int]) -> float:
    heights = _row_heights(aspects, sizes, 1.0, 0.0)
    total = sum(heights)
    middle = sorted(heights)[len(heights) // 2]
    uneven = sum(abs(math.log(height / middle)) for height in heights) / len(heights)
    empty = 0.0
    index = 0
    for size, height in zip(sizes, heights):
        empty += (1.0 - sum(aspects[index : index + size]) * height) * height
        index += size
    return abs(math.log(1.0 / total / GRID_TARGET)) + UNEVEN_WEIGHT * uneven + EMPTY_WEIGHT * empty / total


def choose_rows(aspects: list[float], layout: str) -> list[int]:
    count = len(aspects)
    if layout == "row" or count < 2:
        return [count]
    mixed = _mixed(aspects)
    best_sizes = [count]
    best_score = _score(aspects, best_sizes)
    estimate = math.sqrt(sum(aspects) / GRID_TARGET)
    for rows in range(2, min(count, MAX_ROWS, math.ceil(estimate * 2) + 2) + 1):
        sizes = _partition(aspects, rows) if mixed else _even_rows(count, rows)
        if sizes is None:
            continue
        score = _score(aspects, sizes)
        if score < best_score - 1e-9:
            best_sizes, best_score = sizes, score
    return best_sizes


def plan(sizes: list[QSize], layout: str) -> Plan:
    aspects = [size.width() / size.height() for size in sizes]
    rows = choose_rows(aspects, layout)
    unit = _row_heights(aspects, rows, 1.0, 0.0)
    per_image = []
    for row, size in enumerate(rows):
        per_image.extend([unit[row]] * size)
    wanted = sorted(size.height() / height for size, height in zip(sizes, per_image))
    width = wanted[len(wanted) // 2]
    heights = _row_heights(aspects, rows, width, 0.0)
    shorts = sorted(min(aspects[i] * heights[row], heights[row]) for row, i in _row_members(rows))
    gap = max(6.0, GAP_RATIO * shorts[(len(shorts) - 1) // 2])
    heights = _row_heights(aspects, rows, width, gap)
    total_width = width + 2 * gap
    total_height = sum(heights) + gap * (len(rows) + 1)
    scale = min(
        1.0,
        MAX_SIDE / total_width,
        MAX_SIDE / total_height,
        math.sqrt(MAX_PIXELS / (total_width * total_height)),
    )
    width *= scale
    gap *= scale
    heights = [height * scale for height in heights]
    tiles = []
    y = gap
    index = 0
    for row, size in enumerate(rows):
        height = heights[row]
        row_width = sum(aspects[index : index + size]) * height + gap * (size - 1)
        x = gap + (width - row_width) / 2
        top, bottom = round(y), round(y + height)
        for item in range(index, index + size):
            right = x + aspects[item] * height
            tiles.append((round(x), top, round(right) - round(x), bottom - top))
            x = right + gap
        y += height + gap
        index += size
    canvas_width = round(width + 2 * gap)
    canvas_height = round(y)
    short = min(min(w, h) for _x, _y, w, h in tiles)
    return Plan(canvas_width, canvas_height, tiles, rows, round(gap), round(CORNER_RATIO * short))


def _row_members(rows: list[int]):
    index = 0
    for row, size in enumerate(rows):
        for _ in range(size):
            yield row, index
            index += 1


def draw(images, layout_plan: Plan, background: str = BACKGROUND) -> QImage:
    canvas = QImage(layout_plan.width, layout_plan.height, QImage.Format.Format_RGB32)
    canvas.fill(QColor(background))
    painter = QPainter(canvas)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    for (x, y, width, height), image in zip(layout_plan.tiles, images):
        if image is None or image.isNull() or width <= 0 or height <= 0:
            continue
        scaled = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(x, y, _rounded(scaled, layout_plan.radius))
    painter.end()
    return canvas


def _rounded(image: QImage, radius: int) -> QImage:
    if radius <= 0:
        return image
    result = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(result.rect()), radius, radius)
    painter.fillPath(path, Qt.GlobalColor.white)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.drawImage(0, 0, image)
    painter.end()
    return result
