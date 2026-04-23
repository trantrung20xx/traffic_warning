from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import (
    bbox_bottom_center,
    point_in_polygon,
    segment_intersects_segment,
)


@dataclass
class IllegalLaneCandidate:
    source_lane_id: int
    target_lane_id: int
    started_ts: datetime


@dataclass
class TurnManeuverCandidate:
    maneuver: str
    enter_ts: datetime
    last_hit_ts: datetime
    hit_count: int


@dataclass
class TurnState:
    phase: str = "idle"
    source_lane_id: Optional[int] = None
    approach_ts: Optional[datetime] = None
    committed_ts: Optional[datetime] = None
    confirmed_maneuver: Optional[str] = None
    last_activity_ts: Optional[datetime] = None
    maneuver_candidate: Optional[TurnManeuverCandidate] = None


@dataclass
class VehicleState:
    vehicle_id: int
    vehicle_type: str

    current_stable_lane_id: Optional[int] = None
    lane_history: deque[tuple[datetime, int]] = field(default_factory=deque)
    illegal_lane_candidate: Optional[IllegalLaneCandidate] = None
    turn_state: TurnState = field(default_factory=TurnState)
    last_position: Optional[tuple[float, float]] = None

    # Ghi nhớ lỗi đã phát ra để không bắn lặp nhiều lần cho cùng một xe.
    emitted: set[str] = field(default_factory=set)
    last_seen_ts: Optional[datetime] = None


class ViolationLogic:
    """
    Bộ luật phát hiện vi phạm, không phải AI end-to-end.

    `wrong_lane` tiếp tục dùng lane ổn định hiện tại.
    `illegal_turn` dùng state machine riêng:
    `idle -> approach -> committed -> confirmed`.
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        turn_corridors: Optional[dict[str, list[list[float]]]] = None,
        exit_zones: Optional[dict[str, list[list[float]]]] = None,
        exit_lines: Optional[dict[str, list[list[float]]]] = None,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
        turn_candidate_window_ms: int = 500,
        turn_state_timeout_ms: int = 3000,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_by_id = {lp.lane_id: lp for lp in lane_polygons}
        self._lane_order = [lp.lane_id for lp in lane_polygons]
        self._turn_corridors = turn_corridors or {}
        self._exit_zones = exit_zones or {}
        self._exit_lines = exit_lines or {}
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._turn_candidate_window_ms = int(turn_candidate_window_ms)
        self._turn_state_timeout_ms = int(turn_state_timeout_ms)
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

        px, py = bbox_bottom_center(bbox_xyxy)
        violations: list[dict] = []

        self._update_lane_state(st=st, lane_id=lane_id, ts=ts, violations=violations)

        current_lane_id = st.current_stable_lane_id
        if current_lane_id is not None:
            lp = self._lane_by_id.get(current_lane_id)
            if lp is not None:
                allowed_vehicle_types = lp.allowed_vehicle_types
                if (
                    allowed_vehicle_types
                    and vehicle_type not in allowed_vehicle_types
                    and "vehicle_type_not_allowed" not in st.emitted
                ):
                    st.emitted.add("vehicle_type_not_allowed")
                    violations.append({"lane_id": current_lane_id, "violation": "vehicle_type_not_allowed"})

        self._update_turn_state(
            st=st,
            current_lane_id=current_lane_id,
            point_x=px,
            point_y=py,
            ts=ts,
            violations=violations,
        )
        st.last_position = (px, py)
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
        current_lane_id: Optional[int],
        point_x: float,
        point_y: float,
        ts: datetime,
        violations: list[dict],
    ) -> None:
        turn_state = st.turn_state
        self._expire_turn_state(turn_state=turn_state, ts=ts)
        if turn_state.phase == "confirmed":
            return

        approach_lane_id = self._match_approach_lane(
            current_lane_id=current_lane_id,
            point_x=point_x,
            point_y=point_y,
        )
        if turn_state.phase in {"idle", "approach"} and approach_lane_id is not None:
            self._enter_approach(turn_state=turn_state, source_lane_id=approach_lane_id, ts=ts)

        commit_lane_id = self._match_commit_lane(
            current_lane_id=current_lane_id,
            source_lane_id=turn_state.source_lane_id if turn_state.phase == "approach" else None,
            previous_point=st.last_position,
            point_x=point_x,
            point_y=point_y,
        )
        if commit_lane_id is not None and turn_state.phase != "committed":
            if turn_state.phase != "approach" or turn_state.source_lane_id != commit_lane_id:
                self._enter_approach(turn_state=turn_state, source_lane_id=commit_lane_id, ts=ts)
            self._enter_committed(turn_state=turn_state, ts=ts)

        if turn_state.phase != "committed" or turn_state.source_lane_id is None:
            return

        confirmed_maneuver = self._update_turn_confirmation(
            turn_state=turn_state,
            previous_point=st.last_position,
            point_x=point_x,
            point_y=point_y,
            ts=ts,
        )
        if confirmed_maneuver is None:
            return

        turn_state.phase = "confirmed"
        turn_state.confirmed_maneuver = confirmed_maneuver
        turn_state.last_activity_ts = ts

        lane_cfg = self._lane_by_id.get(turn_state.source_lane_id)
        if lane_cfg is None:
            return

        allowed_maneuvers = lane_cfg.allowed_maneuvers or []
        if allowed_maneuvers and confirmed_maneuver not in allowed_maneuvers:
            violation_key = f"turn_{confirmed_maneuver}_not_allowed"
            if violation_key not in st.emitted:
                st.emitted.add(violation_key)
                violations.append(
                    {
                        "lane_id": turn_state.source_lane_id,
                        "violation": violation_key,
                    }
                )

    def _enter_approach(self, *, turn_state: TurnState, source_lane_id: int, ts: datetime) -> None:
        if turn_state.phase == "committed":
            return
        if turn_state.phase == "approach" and turn_state.source_lane_id == source_lane_id:
            turn_state.last_activity_ts = ts
            return

        turn_state.phase = "approach"
        turn_state.source_lane_id = source_lane_id
        turn_state.approach_ts = ts
        turn_state.committed_ts = None
        turn_state.confirmed_maneuver = None
        turn_state.last_activity_ts = ts
        turn_state.maneuver_candidate = None

    def _enter_committed(self, *, turn_state: TurnState, ts: datetime) -> None:
        if turn_state.phase == "committed":
            turn_state.last_activity_ts = ts
            return
        if turn_state.source_lane_id is None:
            return

        turn_state.phase = "committed"
        turn_state.committed_ts = ts
        turn_state.last_activity_ts = ts
        turn_state.maneuver_candidate = None

    def _update_turn_confirmation(
        self,
        *,
        turn_state: TurnState,
        previous_point: Optional[tuple[float, float]],
        point_x: float,
        point_y: float,
        ts: datetime,
    ) -> Optional[str]:
        exit_line_matches = self._match_line_collection(
            self._exit_lines,
            previous_point=previous_point,
            point_x=point_x,
            point_y=point_y,
        )
        if exit_line_matches:
            return self._select_maneuver(turn_state=turn_state, matches=exit_line_matches)

        exit_matches = self._match_zone_collection(self._exit_zones, point_x=point_x, point_y=point_y)
        if exit_matches:
            return self._select_maneuver(turn_state=turn_state, matches=exit_matches)

        corridor_matches = self._match_zone_collection(self._turn_corridors, point_x=point_x, point_y=point_y)
        if not corridor_matches:
            self._expire_maneuver_candidate(turn_state=turn_state, ts=ts)
            return None

        maneuver = self._select_maneuver(turn_state=turn_state, matches=corridor_matches)
        candidate = turn_state.maneuver_candidate
        if candidate is None:
            turn_state.maneuver_candidate = TurnManeuverCandidate(
                maneuver=maneuver,
                enter_ts=ts,
                last_hit_ts=ts,
                hit_count=1,
            )
            turn_state.last_activity_ts = ts
            return None

        elapsed_since_last_hit_ms = int((ts - candidate.last_hit_ts).total_seconds() * 1000.0)
        if candidate.maneuver != maneuver or elapsed_since_last_hit_ms > self._turn_candidate_window_ms:
            turn_state.maneuver_candidate = TurnManeuverCandidate(
                maneuver=maneuver,
                enter_ts=ts,
                last_hit_ts=ts,
                hit_count=1,
            )
            turn_state.last_activity_ts = ts
            return None

        candidate.hit_count += 1
        candidate.last_hit_ts = ts
        turn_state.last_activity_ts = ts
        if candidate.hit_count >= self._turn_region_min_hits:
            return candidate.maneuver
        return None

    def _expire_maneuver_candidate(self, *, turn_state: TurnState, ts: datetime) -> None:
        candidate = turn_state.maneuver_candidate
        if candidate is None:
            return
        elapsed_since_last_hit_ms = int((ts - candidate.last_hit_ts).total_seconds() * 1000.0)
        if elapsed_since_last_hit_ms > self._turn_candidate_window_ms:
            turn_state.maneuver_candidate = None

    def _expire_turn_state(self, *, turn_state: TurnState, ts: datetime) -> None:
        if turn_state.phase == "confirmed":
            return
        if turn_state.last_activity_ts is None:
            return
        elapsed_ms = int((ts - turn_state.last_activity_ts).total_seconds() * 1000.0)
        if elapsed_ms > self._turn_state_timeout_ms:
            turn_state.phase = "idle"
            turn_state.source_lane_id = None
            turn_state.approach_ts = None
            turn_state.committed_ts = None
            turn_state.confirmed_maneuver = None
            turn_state.last_activity_ts = None
            turn_state.maneuver_candidate = None

    def _match_approach_lane(
        self,
        *,
        current_lane_id: Optional[int],
        point_x: float,
        point_y: float,
    ) -> Optional[int]:
        prioritized_lane_ids: list[int] = []
        if current_lane_id is not None:
            prioritized_lane_ids.append(current_lane_id)
        prioritized_lane_ids.extend(lane_id for lane_id in self._lane_order if lane_id != current_lane_id)

        for lane_id in prioritized_lane_ids:
            lane_cfg = self._lane_by_id.get(lane_id)
            approach_polygon = lane_cfg.approach_zone if lane_cfg is not None else None
            if approach_polygon and point_in_polygon(point_x, point_y, approach_polygon):
                return lane_id
        return None

    def _match_commit_lane(
        self,
        *,
        current_lane_id: Optional[int],
        source_lane_id: Optional[int],
        previous_point: Optional[tuple[float, float]],
        point_x: float,
        point_y: float,
    ) -> Optional[int]:
        prioritized_lane_ids: list[int] = []
        for lane_id in (source_lane_id, current_lane_id):
            if lane_id is not None and lane_id not in prioritized_lane_ids:
                prioritized_lane_ids.append(lane_id)
        prioritized_lane_ids.extend(lane_id for lane_id in self._lane_order if lane_id not in prioritized_lane_ids)

        for lane_id in prioritized_lane_ids:
            lane_cfg = self._lane_by_id.get(lane_id)
            if lane_cfg is None:
                continue
            if lane_cfg.commit_gate and point_in_polygon(point_x, point_y, lane_cfg.commit_gate):
                return lane_id
            if lane_cfg.commit_line and previous_point is not None:
                if segment_intersects_segment(
                    previous_point,
                    (point_x, point_y),
                    lane_cfg.commit_line[0],
                    lane_cfg.commit_line[1],
                ):
                    return lane_id
        return None

    def _match_zone_collection(
        self,
        collection: dict[str, list[list[float]]],
        *,
        point_x: float,
        point_y: float,
    ) -> list[str]:
        matches: list[str] = []
        for maneuver, polygon in collection.items():
            if point_in_polygon(point_x, point_y, polygon):
                matches.append(maneuver)
        return matches

    def _match_line_collection(
        self,
        collection: dict[str, list[list[float]]],
        *,
        previous_point: Optional[tuple[float, float]],
        point_x: float,
        point_y: float,
    ) -> list[str]:
        if previous_point is None:
            return []
        matches: list[str] = []
        current_point = (point_x, point_y)
        for maneuver, line in collection.items():
            if segment_intersects_segment(previous_point, current_point, line[0], line[1]):
                matches.append(maneuver)
        return matches

    def _select_maneuver(self, *, turn_state: TurnState, matches: list[str]) -> str:
        candidate = turn_state.maneuver_candidate
        if candidate is not None and candidate.maneuver in matches:
            return candidate.maneuver
        return matches[0]

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
        cutoff_ms = current_ts.timestamp() - float(max_age_s)
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
