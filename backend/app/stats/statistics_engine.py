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
        self._counts: dict[ViolationAggKey, int] = defaultdict(int)

    def update_realtime(self, event: ViolationEvent) -> None:
        key = ViolationAggKey(
            camera_id=event.camera_id,
            road_name=event.location.road_name,
            intersection=event.location.intersection,
            vehicle_type=event.vehicle_type,
            violation=event.violation,
        )
        self._counts[key] += 1

    def snapshot_rows(self) -> list[dict]:
        rows: list[dict] = []
        for key, count in self._counts.items():
            rows.append(
                {
                    "camera_id": key.camera_id,
                    "road_name": key.road_name,
                    "intersection": key.intersection,
                    "vehicle_type": key.vehicle_type,
                    "violation": key.violation,
                    "count": count,
                }
            )
        # Sắp xếp cố định để UI hiển thị ổn định giữa các lần render.
        rows.sort(key=lambda r: (r.get("camera_id") or "", r.get("violation") or "", r.get("vehicle_type") or ""))
        return rows

