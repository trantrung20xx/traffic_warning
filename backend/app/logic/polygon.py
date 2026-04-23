from __future__ import annotations

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

