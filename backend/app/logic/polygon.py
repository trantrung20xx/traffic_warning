from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Sequence

from shapely import LineString, Polygon, contains_xy, intersects, intersects_xy, prepare


def _normalize_point(point: Sequence[float]) -> tuple[float, float]:
    return (float(point[0]), float(point[1]))


@dataclass(slots=True)
class PreparedPolygon:
    """
    Polygon đã được `shapely.prepare()` để tăng tốc cho các phép contains lặp lại.

    Đây là hot path của lane matching và spatial turn-state nên việc chuẩn bị trước
    geometry giúp sạch code hơn và giảm chi phí tự xử lý hình học ở Python.
    """

    geometry: Polygon

    @classmethod
    def from_points(cls, points: Sequence[Sequence[float]]) -> "PreparedPolygon":
        polygon = Polygon([_normalize_point(point) for point in points])
        prepare(polygon)
        return cls(geometry=polygon)

    def contains_xy(self, x: float, y: float) -> bool:
        return bool(contains_xy(self.geometry, float(x), float(y)))

    def segment_overlap_length(self, start: Sequence[float], end: Sequence[float]) -> float:
        """
        Độ dài phần đoạn thẳng nằm trong polygon.

        Hữu ích cho bài toán gán làn vì đáy bbox của xe phản ánh tốt hơn việc
        xe đang đè lên làn nào so với chỉ kiểm tra đúng 1 điểm tâm.
        """
        segment = LineString([_normalize_point(start), _normalize_point(end)])
        if segment.is_empty:
            return 0.0
        intersection = self.geometry.intersection(segment)
        return float(intersection.length)


@dataclass(slots=True)
class PreparedLine:
    geometry: LineString

    @classmethod
    def from_points(cls, points: Sequence[Sequence[float]]) -> "PreparedLine":
        return cls(geometry=LineString([_normalize_point(point) for point in points]))

    @property
    def coords(self) -> tuple[tuple[float, float], tuple[float, float]]:
        start, end = self.geometry.coords
        return (_normalize_point(start), _normalize_point(end))

    @property
    def length(self) -> float:
        return float(self.geometry.length)

    def intersects_segment(self, start: Sequence[float], end: Sequence[float]) -> bool:
        start_xy = _normalize_point(start)
        end_xy = _normalize_point(end)
        if start_xy == end_xy:
            return bool(intersects_xy(self.geometry, start_xy[0], start_xy[1]))
        return bool(intersects(self.geometry, LineString([start_xy, end_xy])))


def point_in_polygon(point_x: float, point_y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Wrapper tương thích cho code cũ, bên dưới dùng Shapely thay vì ray casting tự viết."""
    return PreparedPolygon.from_points(polygon).contains_xy(point_x, point_y)


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
    return float(LineString([_normalize_point(start), _normalize_point(end)]).length)


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
    """Wrapper tương thích cho code cũ, bên dưới dùng predicate hình học của Shapely."""
    return PreparedLine.from_points((start_b, end_b)).intersects_segment(start_a, end_a)

