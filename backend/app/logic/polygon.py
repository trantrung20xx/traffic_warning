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

