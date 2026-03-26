from __future__ import annotations

from typing import Iterable, Sequence


def point_in_polygon(point_x: float, point_y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """
    Ray casting algorithm.
    polygon: [[x,y], ...] with at least 3 points.
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
        # Check if edge intersects the horizontal ray to the right of point
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            # Compute x coordinate of intersection of the edge with the ray
            x_int = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x_int > x:
                inside = not inside
    return inside


def bbox_bottom_center(bbox_xyxy: Sequence[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_xyxy
    return (float(x1 + x2) / 2.0, float(y2))

