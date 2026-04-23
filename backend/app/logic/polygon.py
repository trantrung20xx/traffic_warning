from __future__ import annotations

from math import hypot
from typing import Iterable, Sequence


def point_in_polygon(point_x: float, point_y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """
    Kiểm tra một điểm có nằm trong polygon hay không bằng thuật toán ray casting.
    `polygon` có dạng `[[x, y], ...]` và cần ít nhất 3 đỉnh.
    """
    inside = False
    n = len(polygon)
    if n < 3:
        return False

    x = point_x
    y = point_y
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        # Kiểm tra cạnh hiện tại có cắt tia ngang kéo sang phải từ điểm cần xét hay không.
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            # Tính hoành độ giao điểm để biết có cần đảo trạng thái trong/ngoài polygon hay không.
            x_int = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x_int > x:
                inside = not inside
    return inside


def bbox_bottom_center(bbox_xyxy: Sequence[float]) -> tuple[float, float]:
    """Lấy điểm giữa cạnh đáy của bounding box để đại diện vị trí bánh xe chạm mặt đường."""
    x1, y1, x2, y2 = bbox_xyxy
    return (float(x1 + x2) / 2.0, float(y2))


def bbox_bottom_contact_points(bbox_xyxy: Sequence[float]) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """
    Lấy 3 điểm tiếp xúc gần mép dưới của bounding box để giảm phụ thuộc vào đúng 1 điểm giữa.

    Dùng 1/4, 1/2 và 3/4 cạnh đáy thay vì lấy sát hai góc để đỡ nhạy với box rung hoặc box bị nở.
    """
    x1, y1, x2, y2 = bbox_xyxy
    width = float(x2) - float(x1)
    left_x = float(x1) + (width * 0.25)
    center_x = float(x1 + x2) / 2.0
    right_x = float(x2) - (width * 0.25)
    bottom_y = float(y2)
    return ((left_x, bottom_y), (center_x, bottom_y), (right_x, bottom_y))


def line_length(start: Sequence[float], end: Sequence[float]) -> float:
    return hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))


def signed_distance_to_line(
    point: Sequence[float],
    line_start: Sequence[float],
    line_end: Sequence[float],
) -> float:
    """
    Khoảng cách có dấu từ điểm tới đường thẳng vô hạn đi qua `line_start -> line_end`.

    Dấu được dùng để biết điểm nằm ở phía nào của line, rất hữu ích cho xác nhận cắt line theo thời gian.
    """
    x0, y0 = float(point[0]), float(point[1])
    x1, y1 = float(line_start[0]), float(line_start[1])
    x2, y2 = float(line_end[0]), float(line_end[1])
    denominator = hypot(x2 - x1, y2 - y1)
    if denominator <= 1e-9:
        return 0.0
    numerator = ((x2 - x1) * (y0 - y1)) - ((y2 - y1) * (x0 - x1))
    return numerator / denominator


def segment_intersects_segment(
    start_a: Sequence[float],
    end_a: Sequence[float],
    start_b: Sequence[float],
    end_b: Sequence[float],
) -> bool:
    """Kiểm tra hai đoạn thẳng có cắt nhau hay không."""

    def orientation(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> int:
        value = (float(q[1]) - float(p[1])) * (float(r[0]) - float(q[0])) - (
            float(q[0]) - float(p[0])
        ) * (float(r[1]) - float(q[1]))
        if abs(value) < 1e-9:
            return 0
        return 1 if value > 0 else 2

    def on_segment(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> bool:
        return (
            min(float(p[0]), float(r[0])) - 1e-9 <= float(q[0]) <= max(float(p[0]), float(r[0])) + 1e-9
            and min(float(p[1]), float(r[1])) - 1e-9 <= float(q[1]) <= max(float(p[1]), float(r[1])) + 1e-9
        )

    o1 = orientation(start_a, end_a, start_b)
    o2 = orientation(start_a, end_a, end_b)
    o3 = orientation(start_b, end_b, start_a)
    o4 = orientation(start_b, end_b, end_a)

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and on_segment(start_a, start_b, end_a):
        return True
    if o2 == 0 and on_segment(start_a, end_b, end_a):
        return True
    if o3 == 0 and on_segment(start_b, start_a, end_b):
        return True
    if o4 == 0 and on_segment(start_b, end_a, end_b):
        return True

    return False

