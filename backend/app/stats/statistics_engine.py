from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from app.schemas.events import ViolationEvent


@dataclass(frozen=True)
class ViolationAggKey:
    camera_id: Optional[str]
    road_name: Optional[str]
    intersection: Optional[str]
    vehicle_type: str
    violation: str


class StatisticsEngine:
    """
    Bộ thống kê thuần theo luật, không dùng AI.
    Có thể cộng dồn realtime trong bộ nhớ và song song hỗ trợ truy vấn từ DB.
    """

    def __init__(self):
        # Counter in-memory phục vụ dashboard realtime nhẹ.
        self._counts: dict[ViolationAggKey, int] = defaultdict(int)

    def update_realtime(self, event: ViolationEvent) -> None:
        # Key gom theo camera/vị trí/loại xe/loại lỗi để phù hợp các thẻ tổng hợp UI.
        key = ViolationAggKey(
            camera_id=event.camera_id,
            road_name=event.location.road_name,
            intersection=event.location.intersection,
            vehicle_type=event.vehicle_type,
            violation=event.violation,
        )
        # Cộng dồn theo key để có thể đọc thống kê tức thời mà không query DB.
        self._counts[key] += 1

