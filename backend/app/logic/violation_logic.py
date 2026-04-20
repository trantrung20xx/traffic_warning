from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import bbox_bottom_center, point_in_polygon


@dataclass
class VehicleState:
    vehicle_id: int
    vehicle_type: str

    primary_lane_id: Optional[int] = None
    # Tính sai làn theo thời gian thực thay vì số frame để không phụ thuộc FPS.
    illegal_lane_started_ts: Optional[datetime] = None

    # Trạng thái suy luận hướng đi cuối cùng của xe theo turn region.
    first_maneuver: Optional[str] = None
    maneuver_hit_counts: dict[str, int] = field(default_factory=dict)

    # Ghi nhớ lỗi đã phát ra để không bắn lặp nhiều lần cho cùng một xe.
    emitted: set[str] = field(default_factory=set)
    last_seen_ts: Optional[datetime] = None


class ViolationLogic:
    """
    Bộ luật phát hiện vi phạm, không phải AI end-to-end.
    Quyết định vi phạm dựa trên:
    - polygon làn và turn region cấu hình riêng cho từng camera
    - lịch sử track của từng xe qua `vehicle_id`
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_by_id = {lp.lane_id: lp for lp in lane_polygons}
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._vehicle_states: dict[int, VehicleState] = {}

    def update_and_maybe_generate_violation(
        self,
        *,
        vehicle_id: int,
        vehicle_type: str,
        lane_id: Optional[int],
        bbox_xyxy: list[float] | tuple[float, ...],
        ts: datetime,
    ) -> list[dict]:
        """
        Trả về danh sách vi phạm ứng viên:
        [
          {
            "lane_id": int,
            "violation": str,
          },
          ...
        ]
        """
        st = self._vehicle_states.get(vehicle_id)
        if st is None:
            st = VehicleState(vehicle_id=vehicle_id, vehicle_type=vehicle_type)
            self._vehicle_states[vehicle_id] = st
        st.last_seen_ts = ts

        px, py = bbox_bottom_center(bbox_xyxy)
        primary_lane_id = st.primary_lane_id

        # Ghi nhận làn gốc ngay khi đã có lane_id đủ tin cậy để làm mốc so sánh về sau.
        if primary_lane_id is None and lane_id is not None:
            st.primary_lane_id = lane_id
            primary_lane_id = lane_id

        if primary_lane_id is None:
            return []

        lp = self._lane_by_id.get(primary_lane_id)
        if lp is None:
            return []

        violations: list[dict] = []

        # ----------------------
        # Kiểm tra loại phương tiện có được phép đi trong làn gốc hay không.
        # ----------------------
        allowed_vehicle_types = lp.allowed_vehicle_types
        if allowed_vehicle_types:
            if vehicle_type not in allowed_vehicle_types and "vehicle_type_not_allowed" not in st.emitted:
                st.emitted.add("vehicle_type_not_allowed")
                violations.append({"lane_id": primary_lane_id, "violation": "vehicle_type_not_allowed"})

        # ----------------------
        # Kiểm tra đi sai làn: xe phải nằm sai làn đủ lâu mới phát cảnh báo.
        # ----------------------
        allowed_lane_changes = lp.allowed_lane_changes
        if allowed_lane_changes is None:
            allowed_lane_changes = [primary_lane_id]

        if lane_id is not None:
            if lane_id in allowed_lane_changes:
                st.illegal_lane_started_ts = None
            else:
                if st.illegal_lane_started_ts is None:
                    st.illegal_lane_started_ts = ts
                duration_ms = int((ts - st.illegal_lane_started_ts).total_seconds() * 1000.0)
                if duration_ms >= self._wrong_lane_min_duration_ms and "wrong_lane" not in st.emitted:
                    st.emitted.add("wrong_lane")
                    violations.append({"lane_id": primary_lane_id, "violation": "wrong_lane"})

        # ----------------------
        # Kiểm tra hướng đi sai bằng cách xem xe đi vào turn region nào đầu tiên.
        # ----------------------
        if lp.turn_regions:
            for maneuver, poly in lp.turn_regions.items():
                if point_in_polygon(px, py, poly):
                    st.maneuver_hit_counts[maneuver] = st.maneuver_hit_counts.get(maneuver, 0) + 1
                    if (
                        st.first_maneuver is None
                        and st.maneuver_hit_counts[maneuver] >= self._turn_region_min_hits
                    ):
                        st.first_maneuver = maneuver

                        allowed_maneuvers = lp.allowed_maneuvers or []
                        if allowed_maneuvers and maneuver not in allowed_maneuvers:
                            if f"turn_{maneuver}_not_allowed" not in st.emitted:
                                st.emitted.add(f"turn_{maneuver}_not_allowed")
                                violations.append(
                                    {
                                        "lane_id": primary_lane_id,
                                        "violation": f"turn_{maneuver}_not_allowed",
                                    }
                                )
                    break
        return violations

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        """
        Xóa trạng thái của các xe quá cũ để bộ nhớ không tăng mãi trong lúc chạy lâu.
        """
        cutoff_ms = (current_ts.timestamp() - float(max_age_s))
        to_delete: list[int] = []
        for vid, st in self._vehicle_states.items():
            if st.last_seen_ts is None:
                continue
            if st.last_seen_ts.timestamp() < cutoff_ms:
                to_delete.append(vid)
        for vid in to_delete:
            del self._vehicle_states[vid]

