from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import bbox_bottom_center, point_in_polygon


@dataclass(frozen=True)
class LaneMatch:
    lane_id: int


class LaneLogic:
    """
    Assign lane_id using hand-crafted polygons.
    No AI lane detection is used.
    """

    def __init__(self, lane_polygons: list[LanePolygon]):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_polygons = {lp.lane_id: lp for lp in lane_polygons}
        self._lane_order = [lp.lane_id for lp in lane_polygons]

    def assign_lane_id_from_bbox_xyxy(self, bbox_xyxy: list[float] | tuple[float, ...]) -> Optional[int]:
        px, py = bbox_bottom_center(bbox_xyxy)

        matches: list[int] = []
        for lane_id in self._lane_order:
            lp = self._lane_polygons[lane_id]
            if point_in_polygon(px, py, lp.polygon):
                matches.append(lane_id)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Misconfigured overlapping polygons. Skeleton: choose the first.
            return matches[0]
        return None

