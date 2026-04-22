from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import bbox_bottom_center, point_in_polygon


@dataclass
class VehicleState:
    vehicle_id: int
    vehicle_type: str

    current_stable_lane_id: Optional[int] = None
    lane_history: deque[tuple[datetime, int]] = field(default_factory=deque)
    illegal_lane_candidate: Optional["IllegalLaneCandidate"] = None
    active_turn_candidate: Optional["TurnCandidate"] = None
    committed_turn: Optional[str] = None

    # Ghi nhớ lỗi đã phát ra để không bắn lặp nhiều lần cho cùng một xe.
    emitted: set[str] = field(default_factory=set)
    last_seen_ts: Optional[datetime] = None


@dataclass
class IllegalLaneCandidate:
    source_lane_id: int
    target_lane_id: int
    started_ts: datetime


@dataclass
class TurnCandidate:
    maneuver: str
    enter_ts: datetime
    last_hit_ts: datetime
    hit_count: int
    lane_id_at_entry: int


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
        turn_candidate_window_ms: int = 500,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_by_id = {lp.lane_id: lp for lp in lane_polygons}
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._turn_candidate_window_ms = int(turn_candidate_window_ms)
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
        st.vehicle_type = vehicle_type

        violations: list[dict] = []
        self._update_lane_state(st=st, lane_id=lane_id, ts=ts, violations=violations)

        current_lane_id = st.current_stable_lane_id
        if current_lane_id is None:
            return violations

        lp = self._lane_by_id.get(current_lane_id)
        if lp is None:
            return violations

        px, py = bbox_bottom_center(bbox_xyxy)

        # ----------------------
        # Kiểm tra loại phương tiện có được phép đi trong làn hiện tại hay không.
        # ----------------------
        allowed_vehicle_types = lp.allowed_vehicle_types
        if allowed_vehicle_types:
            if vehicle_type not in allowed_vehicle_types and "vehicle_type_not_allowed" not in st.emitted:
                st.emitted.add("vehicle_type_not_allowed")
                violations.append({"lane_id": current_lane_id, "violation": "vehicle_type_not_allowed"})

        # ----------------------
        # Kiểm tra hướng đi sai theo lane đã ổn định tại thời điểm commit turn.
        # ----------------------
        self._update_turn_state(
            st=st,
            current_lane_id=current_lane_id,
            point_x=px,
            point_y=py,
            ts=ts,
            violations=violations,
        )
        return violations

    def _update_lane_state(
        self,
        *,
        st: VehicleState,
        lane_id: Optional[int],
        ts: datetime,
        violations: list[dict],
    ) -> None:
        if lane_id is None:
            self._update_wrong_lane_candidate(st=st, ts=ts, violations=violations)
            self._expire_turn_candidate(st=st, ts=ts)
            return

        previous_lane_id = st.current_stable_lane_id
        if previous_lane_id is None:
            st.current_stable_lane_id = lane_id
            self._append_lane_history(st=st, lane_id=lane_id, ts=ts)
            self._update_wrong_lane_candidate(st=st, ts=ts, violations=violations)
            return

        if lane_id != previous_lane_id:
            st.current_stable_lane_id = lane_id
            self._append_lane_history(st=st, lane_id=lane_id, ts=ts)
            st.active_turn_candidate = None

            allowed_lane_changes = self._allowed_lane_changes_for(previous_lane_id)
            if lane_id in allowed_lane_changes:
                st.illegal_lane_candidate = None
            else:
                st.illegal_lane_candidate = IllegalLaneCandidate(
                    source_lane_id=previous_lane_id,
                    target_lane_id=lane_id,
                    started_ts=ts,
                )

        self._update_wrong_lane_candidate(st=st, ts=ts, violations=violations)

    def _update_wrong_lane_candidate(
        self,
        *,
        st: VehicleState,
        ts: datetime,
        violations: list[dict],
    ) -> None:
        candidate = st.illegal_lane_candidate
        if candidate is None:
            return

        if st.current_stable_lane_id != candidate.target_lane_id:
            st.illegal_lane_candidate = None
            return

        duration_ms = int((ts - candidate.started_ts).total_seconds() * 1000.0)
        if duration_ms >= self._wrong_lane_min_duration_ms and "wrong_lane" not in st.emitted:
            st.emitted.add("wrong_lane")
            violations.append({"lane_id": candidate.target_lane_id, "violation": "wrong_lane"})

    def _update_turn_state(
        self,
        *,
        st: VehicleState,
        current_lane_id: int,
        point_x: float,
        point_y: float,
        ts: datetime,
        violations: list[dict],
    ) -> None:
        if st.committed_turn is not None:
            return

        maneuver = self._match_turn_region(current_lane_id=current_lane_id, point_x=point_x, point_y=point_y)
        if maneuver is None:
            self._expire_turn_candidate(st=st, ts=ts)
            return

        candidate = st.active_turn_candidate
        window_ms = self._turn_candidate_window_ms

        if candidate is None:
            st.active_turn_candidate = TurnCandidate(
                maneuver=maneuver,
                enter_ts=ts,
                last_hit_ts=ts,
                hit_count=1,
                lane_id_at_entry=current_lane_id,
            )
            return

        elapsed_since_last_hit_ms = int((ts - candidate.last_hit_ts).total_seconds() * 1000.0)
        elapsed_since_enter_ms = int((ts - candidate.enter_ts).total_seconds() * 1000.0)

        if (
            candidate.maneuver != maneuver
            or candidate.lane_id_at_entry != current_lane_id
            or elapsed_since_last_hit_ms > window_ms
            or elapsed_since_enter_ms > window_ms
        ):
            st.active_turn_candidate = TurnCandidate(
                maneuver=maneuver,
                enter_ts=ts,
                last_hit_ts=ts,
                hit_count=1,
                lane_id_at_entry=current_lane_id,
            )
            return

        candidate.hit_count += 1
        candidate.last_hit_ts = ts

        if candidate.hit_count >= self._turn_region_min_hits:
            st.committed_turn = candidate.maneuver
            st.active_turn_candidate = None
            lane_at_turn_entry = candidate.lane_id_at_entry
            lane_cfg = self._lane_by_id.get(lane_at_turn_entry)
            if lane_cfg is None:
                return

            allowed_maneuvers = lane_cfg.allowed_maneuvers or []
            if allowed_maneuvers and candidate.maneuver not in allowed_maneuvers:
                violation_key = f"turn_{candidate.maneuver}_not_allowed"
                if violation_key not in st.emitted:
                    st.emitted.add(violation_key)
                    violations.append(
                        {
                            "lane_id": lane_at_turn_entry,
                            "violation": violation_key,
                        }
                    )

    def _expire_turn_candidate(self, *, st: VehicleState, ts: datetime) -> None:
        candidate = st.active_turn_candidate
        if candidate is None:
            return

        elapsed_since_last_hit_ms = int((ts - candidate.last_hit_ts).total_seconds() * 1000.0)
        if elapsed_since_last_hit_ms > self._turn_candidate_window_ms:
            st.active_turn_candidate = None

    def _match_turn_region(self, *, current_lane_id: int, point_x: float, point_y: float) -> Optional[str]:
        lane_cfg = self._lane_by_id.get(current_lane_id)
        if lane_cfg is None or not lane_cfg.turn_regions:
            return None

        for maneuver, poly in lane_cfg.turn_regions.items():
            if point_in_polygon(point_x, point_y, poly):
                return maneuver
        return None

    def _allowed_lane_changes_for(self, lane_id: int) -> list[int]:
        lane_cfg = self._lane_by_id.get(lane_id)
        if lane_cfg is None or lane_cfg.allowed_lane_changes is None:
            return [lane_id]
        return lane_cfg.allowed_lane_changes

    def _append_lane_history(self, *, st: VehicleState, lane_id: int, ts: datetime) -> None:
        if st.lane_history and st.lane_history[-1][1] == lane_id:
            st.lane_history[-1] = (ts, lane_id)
            return
        st.lane_history.append((ts, lane_id))

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        """
        Xóa trạng thái của các xe quá cũ để bộ nhớ không tăng mãi trong lúc chạy lâu.
        """
        cutoff_ms = (current_ts.timestamp() - float(max_age_s))
        to_delete: list[int] = []
        for vid, st in self._vehicle_states.items():
            if st.last_seen_ts is None:
                continue
            while st.lane_history and st.lane_history[0][0].timestamp() < cutoff_ms:
                st.lane_history.popleft()
            if st.last_seen_ts.timestamp() < cutoff_ms:
                to_delete.append(vid)
        for vid in to_delete:
            del self._vehicle_states[vid]

