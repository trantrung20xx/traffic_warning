from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from math import hypot
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import (
    PreparedLine,
    PreparedPolygon,
    bbox_bottom_contact_points,
    signed_distance_to_line,
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
    anchor_point: tuple[float, float]
    max_progress_px: float = 0.0


@dataclass
class LineCrossingState:
    armed_side: Optional[int] = None
    pre_count: int = 0
    crossing_active: bool = False
    expected_post_side: Optional[int] = None
    post_count: int = 0
    post_distance_px: float = 0.0
    crossing_started_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None
    last_confirmed_ts: Optional[datetime] = None


@dataclass
class TrajectorySample:
    ts: datetime
    left: tuple[float, float]
    center: tuple[float, float]
    right: tuple[float, float]

    @property
    def contacts(self) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        return (self.left, self.center, self.right)


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
    trajectory: deque[TrajectorySample] = field(default_factory=deque)
    line_crossings: dict[str, LineCrossingState] = field(default_factory=dict)

    # Ghi nhớ lỗi đã phát ra để không bắn lặp nhiều lần cho cùng một xe.
    emitted: set[str] = field(default_factory=set)
    last_seen_ts: Optional[datetime] = None


class ViolationLogic:
    """
    Bộ luật phát hiện vi phạm, không phải AI end-to-end.

    `wrong_lane` dùng lane ổn định.
    `illegal_turn` dùng state machine riêng:
    `idle -> approach -> committed -> confirmed`.

    Phần line crossing không còn dựa vào đúng 2 frame liên tiếp.
    Mỗi vehicle giữ một trajectory ngắn hạn và từng line có trạng thái
    pre-side / crossing / post-side để giảm false positive do jitter, low FPS
    hoặc track jump ngắn hạn.
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
        turn_corridor_min_progress_px: float = 2.0,
        turn_corridor_min_duration_ms: int = 180,
        turn_state_timeout_ms: int = 3000,
        trajectory_history_window_ms: int = 2000,
        line_crossing_side_tolerance_px: float = 2.0,
        line_crossing_min_pre_frames: int = 2,
        line_crossing_min_post_frames: int = 2,
        line_crossing_min_displacement_px: float = 2.0,
        line_crossing_min_displacement_ratio: float = 0.02,
        line_crossing_max_gap_ms: int = 400,
        line_crossing_cooldown_ms: int = 1200,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_by_id = {lp.lane_id: lp for lp in lane_polygons}
        self._lane_order = [lp.lane_id for lp in lane_polygons]
        self._approach_shapes = {
            lp.lane_id: PreparedPolygon.from_points(lp.approach_zone) if lp.approach_zone else None
            for lp in lane_polygons
        }
        self._commit_gate_shapes = {
            lp.lane_id: PreparedPolygon.from_points(lp.commit_gate) if lp.commit_gate else None
            for lp in lane_polygons
        }
        self._commit_line_shapes = {
            lp.lane_id: PreparedLine.from_points(lp.commit_line) if lp.commit_line else None
            for lp in lane_polygons
        }
        self._turn_corridors = {
            maneuver: PreparedPolygon.from_points(polygon)
            for maneuver, polygon in (turn_corridors or {}).items()
        }
        self._exit_zones = {
            maneuver: PreparedPolygon.from_points(polygon)
            for maneuver, polygon in (exit_zones or {}).items()
        }
        self._exit_lines = {
            maneuver: PreparedLine.from_points(points)
            for maneuver, points in (exit_lines or {}).items()
        }
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._turn_candidate_window_ms = int(turn_candidate_window_ms)
        self._turn_corridor_min_progress_px = float(turn_corridor_min_progress_px)
        self._turn_corridor_min_duration_ms = int(turn_corridor_min_duration_ms)
        self._turn_state_timeout_ms = int(turn_state_timeout_ms)
        self._trajectory_history_window_ms = int(trajectory_history_window_ms)
        self._line_crossing_side_tolerance_px = float(line_crossing_side_tolerance_px)
        self._line_crossing_min_pre_frames = int(line_crossing_min_pre_frames)
        self._line_crossing_min_post_frames = int(line_crossing_min_post_frames)
        self._line_crossing_min_displacement_px = float(line_crossing_min_displacement_px)
        self._line_crossing_min_displacement_ratio = float(line_crossing_min_displacement_ratio)
        self._line_crossing_max_gap_ms = int(line_crossing_max_gap_ms)
        self._line_crossing_cooldown_ms = int(line_crossing_cooldown_ms)
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

        sample = self._build_sample(bbox_xyxy=bbox_xyxy, ts=ts)
        self._append_trajectory_sample(st=st, sample=sample)
        line_events = self._update_line_crossing_events(st=st, sample=sample, ts=ts)

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
            sample=sample,
            ts=ts,
            line_events=line_events,
            violations=violations,
        )
        return violations

    def _build_sample(
        self,
        *,
        bbox_xyxy: list[float] | tuple[float, ...],
        ts: datetime,
    ) -> TrajectorySample:
        left, center, right = bbox_bottom_contact_points(bbox_xyxy)
        return TrajectorySample(ts=ts, left=left, center=center, right=right)

    def _append_trajectory_sample(self, *, st: VehicleState, sample: TrajectorySample) -> None:
        st.trajectory.append(sample)
        cutoff_ts = sample.ts.timestamp() - (self._trajectory_history_window_ms / 1000.0)
        while st.trajectory and st.trajectory[0].ts.timestamp() < cutoff_ts:
            st.trajectory.popleft()

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
        sample: TrajectorySample,
        ts: datetime,
        line_events: set[str],
        violations: list[dict],
    ) -> None:
        turn_state = st.turn_state
        self._expire_turn_state(turn_state=turn_state, ts=ts)
        if turn_state.phase == "confirmed":
            return

        approach_lane_id = self._match_approach_lane(
            current_lane_id=current_lane_id,
            sample=sample,
        )
        if turn_state.phase in {"idle", "approach"} and approach_lane_id is not None:
            self._enter_approach(turn_state=turn_state, source_lane_id=approach_lane_id, ts=ts)

        commit_lane_id = self._match_commit_lane(
            current_lane_id=current_lane_id,
            source_lane_id=turn_state.source_lane_id if turn_state.phase == "approach" else None,
            sample=sample,
            line_events=line_events,
        )
        if commit_lane_id is not None and turn_state.phase != "committed":
            if turn_state.phase != "approach" or turn_state.source_lane_id != commit_lane_id:
                self._enter_approach(turn_state=turn_state, source_lane_id=commit_lane_id, ts=ts)
            self._enter_committed(turn_state=turn_state, ts=ts)

        if turn_state.phase != "committed" or turn_state.source_lane_id is None:
            return

        confirmed_maneuver = self._update_turn_confirmation(
            turn_state=turn_state,
            sample=sample,
            ts=ts,
            line_events=line_events,
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
        sample: TrajectorySample,
        ts: datetime,
        line_events: set[str],
    ) -> Optional[str]:
        exit_line_matches = [
            key.split(":", 1)[1]
            for key in line_events
            if key.startswith("exit:")
        ]
        if exit_line_matches:
            return self._select_maneuver(turn_state=turn_state, matches=exit_line_matches)

        exit_matches = self._match_zone_collection(self._exit_zones, sample=sample)
        if exit_matches:
            return self._select_maneuver(turn_state=turn_state, matches=exit_matches)

        corridor_matches = self._match_zone_collection(self._turn_corridors, sample=sample)
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
                anchor_point=sample.center,
            )
            turn_state.last_activity_ts = ts
            return self._confirm_turn_candidate_if_ready(turn_state=turn_state, ts=ts)

        elapsed_since_last_hit_ms = int((ts - candidate.last_hit_ts).total_seconds() * 1000.0)
        if candidate.maneuver != maneuver or elapsed_since_last_hit_ms > self._turn_candidate_window_ms:
            turn_state.maneuver_candidate = TurnManeuverCandidate(
                maneuver=maneuver,
                enter_ts=ts,
                last_hit_ts=ts,
                hit_count=1,
                anchor_point=sample.center,
            )
            turn_state.last_activity_ts = ts
            return self._confirm_turn_candidate_if_ready(turn_state=turn_state, ts=ts)

        candidate.hit_count += 1
        candidate.last_hit_ts = ts
        candidate.max_progress_px = max(
            candidate.max_progress_px,
            hypot(sample.center[0] - candidate.anchor_point[0], sample.center[1] - candidate.anchor_point[1]),
        )
        turn_state.last_activity_ts = ts
        return self._confirm_turn_candidate_if_ready(turn_state=turn_state, ts=ts)

    def _confirm_turn_candidate_if_ready(
        self,
        *,
        turn_state: TurnState,
        ts: datetime,
    ) -> Optional[str]:
        candidate = turn_state.maneuver_candidate
        if candidate is None:
            return None
        elapsed_ms = int((ts - candidate.enter_ts).total_seconds() * 1000.0)
        if candidate.max_progress_px < self._turn_corridor_min_progress_px:
            return None
        if candidate.hit_count >= self._turn_region_min_hits:
            return candidate.maneuver
        if elapsed_ms >= self._turn_corridor_min_duration_ms:
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
            self._reset_turn_state(turn_state=turn_state)

    def _reset_turn_state(self, *, turn_state: TurnState) -> None:
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
        sample: TrajectorySample,
    ) -> Optional[int]:
        prioritized_lane_ids: list[int] = []
        if current_lane_id is not None:
            prioritized_lane_ids.append(current_lane_id)
        prioritized_lane_ids.extend(lane_id for lane_id in self._lane_order if lane_id != current_lane_id)

        for lane_id in prioritized_lane_ids:
            approach_shape = self._approach_shapes.get(lane_id)
            if approach_shape and self._sample_inside_polygon(sample=sample, polygon=approach_shape):
                return lane_id
        return None

    def _match_commit_lane(
        self,
        *,
        current_lane_id: Optional[int],
        source_lane_id: Optional[int],
        sample: TrajectorySample,
        line_events: set[str],
    ) -> Optional[int]:
        prioritized_lane_ids: list[int] = []
        for lane_id in (source_lane_id, current_lane_id):
            if lane_id is not None and lane_id not in prioritized_lane_ids:
                prioritized_lane_ids.append(lane_id)
        prioritized_lane_ids.extend(lane_id for lane_id in self._lane_order if lane_id not in prioritized_lane_ids)

        for lane_id in prioritized_lane_ids:
            commit_gate = self._commit_gate_shapes.get(lane_id)
            if commit_gate and self._sample_inside_polygon(sample=sample, polygon=commit_gate):
                return lane_id
            if self._commit_line_shapes.get(lane_id) and f"commit:{lane_id}" in line_events:
                return lane_id
        return None

    def _match_zone_collection(
        self,
        collection: dict[str, PreparedPolygon],
        *,
        sample: TrajectorySample,
    ) -> list[str]:
        matches: list[str] = []
        for maneuver, polygon in collection.items():
            if self._sample_inside_polygon(sample=sample, polygon=polygon):
                matches.append(maneuver)
        return matches

    def _sample_inside_polygon(
        self,
        *,
        sample: TrajectorySample,
        polygon: PreparedPolygon,
    ) -> bool:
        if polygon.contains_xy(sample.center[0], sample.center[1]):
            return True
        hits = 0
        for point_x, point_y in sample.contacts:
            if polygon.contains_xy(point_x, point_y):
                hits += 1
        return hits >= 2

    def _update_line_crossing_events(
        self,
        *,
        st: VehicleState,
        sample: TrajectorySample,
        ts: datetime,
    ) -> set[str]:
        events: set[str] = set()
        previous_sample = st.trajectory[-2] if len(st.trajectory) >= 2 else None

        for lane_id, line in self._commit_line_shapes.items():
            if line is None:
                continue
            key = f"commit:{lane_id}"
            state = st.line_crossings.setdefault(key, LineCrossingState())
            if self._update_line_crossing_state(
                state=state,
                line=line,
                previous_sample=previous_sample,
                sample=sample,
                ts=ts,
            ):
                events.add(key)

        for maneuver, line in self._exit_lines.items():
            key = f"exit:{maneuver}"
            state = st.line_crossings.setdefault(key, LineCrossingState())
            if self._update_line_crossing_state(
                state=state,
                line=line,
                previous_sample=previous_sample,
                sample=sample,
                ts=ts,
            ):
                events.add(key)
        return events

    def _update_line_crossing_state(
        self,
        *,
        state: LineCrossingState,
        line: PreparedLine,
        previous_sample: Optional[TrajectorySample],
        sample: TrajectorySample,
        ts: datetime,
    ) -> bool:
        if state.last_seen_ts is not None:
            gap_ms = int((ts - state.last_seen_ts).total_seconds() * 1000.0)
            if gap_ms > self._line_crossing_max_gap_ms:
                self._reset_line_crossing_state(state=state)
        state.last_seen_ts = ts

        current_side = self._classify_sample_side(sample=sample, line=line)
        line_crossed = self._sample_crosses_line(previous_sample=previous_sample, sample=sample, line=line)
        required_post_distance_px = max(
            self._line_crossing_min_displacement_px,
            line.length * self._line_crossing_min_displacement_ratio,
        )

        if state.last_confirmed_ts is not None:
            cooldown_ms = int((ts - state.last_confirmed_ts).total_seconds() * 1000.0)
            if cooldown_ms <= self._line_crossing_cooldown_ms:
                self._rearm_line_crossing_state(state=state, current_side=current_side)
                return False

        if state.crossing_active and state.crossing_started_ts is not None:
            crossing_elapsed_ms = int((ts - state.crossing_started_ts).total_seconds() * 1000.0)
            if crossing_elapsed_ms > self._line_crossing_max_gap_ms:
                self._reset_line_crossing_state(state=state)

        if state.crossing_active:
            expected_post_side = state.expected_post_side
            if current_side == expected_post_side:
                state.post_count += 1
                state.post_distance_px = max(
                    state.post_distance_px,
                    self._sample_post_distance(sample=sample, line=line, expected_side=expected_post_side),
                )
                if (
                    state.post_count >= self._line_crossing_min_post_frames
                    and state.post_distance_px >= required_post_distance_px
                ):
                    state.last_confirmed_ts = ts
                    self._rearm_line_crossing_state(state=state, current_side=current_side)
                    return True
                return False

            if current_side == 0:
                return False

            self._reset_line_crossing_state(state=state)
            self._rearm_line_crossing_state(state=state, current_side=current_side)
            return False

        if state.armed_side is None:
            self._rearm_line_crossing_state(state=state, current_side=current_side)
            return False

        if current_side == 0:
            if line_crossed and state.pre_count >= self._line_crossing_min_pre_frames:
                state.crossing_active = True
                state.crossing_started_ts = ts
                state.expected_post_side = -state.armed_side
                state.post_count = 0
                state.post_distance_px = 0.0
            return False

        if current_side == state.armed_side:
            state.pre_count += 1
            return False

        if (
            line_crossed
            and state.pre_count >= self._line_crossing_min_pre_frames
            and current_side == -state.armed_side
        ):
            state.crossing_active = True
            state.crossing_started_ts = ts
            state.expected_post_side = current_side
            state.post_count = 1
            state.post_distance_px = self._sample_post_distance(
                sample=sample,
                line=line,
                expected_side=current_side,
            )
            if (
                state.post_count >= self._line_crossing_min_post_frames
                and state.post_distance_px >= required_post_distance_px
            ):
                state.last_confirmed_ts = ts
                self._rearm_line_crossing_state(state=state, current_side=current_side)
                return True
            return False

        self._rearm_line_crossing_state(state=state, current_side=current_side)
        return False

    def _reset_line_crossing_state(self, *, state: LineCrossingState) -> None:
        state.armed_side = None
        state.pre_count = 0
        state.crossing_active = False
        state.expected_post_side = None
        state.post_count = 0
        state.post_distance_px = 0.0
        state.crossing_started_ts = None

    def _rearm_line_crossing_state(self, *, state: LineCrossingState, current_side: int) -> None:
        state.crossing_active = False
        state.expected_post_side = None
        state.post_count = 0
        state.post_distance_px = 0.0
        state.crossing_started_ts = None
        if current_side == 0:
            return
        if state.armed_side == current_side:
            state.pre_count += 1
            return
        state.armed_side = current_side
        state.pre_count = 1

    def _classify_sample_side(self, *, sample: TrajectorySample, line: PreparedLine) -> int:
        line_start, line_end = line.coords
        distances = [
            signed_distance_to_line(point, line_start, line_end)
            for point in sample.contacts
        ]
        signs = [self._distance_to_side(distance) for distance in distances]
        non_zero_signs = [sign for sign in signs if sign != 0]
        if not non_zero_signs:
            return 0
        if all(sign == non_zero_signs[0] for sign in non_zero_signs):
            return non_zero_signs[0]
        center_sign = signs[1]
        if center_sign != 0:
            return center_sign
        return 0

    def _distance_to_side(self, distance: float) -> int:
        if abs(distance) < self._line_crossing_side_tolerance_px:
            return 0
        return 1 if distance > 0 else -1

    def _sample_crosses_line(
        self,
        *,
        previous_sample: Optional[TrajectorySample],
        sample: TrajectorySample,
        line: PreparedLine,
    ) -> bool:
        if previous_sample is None:
            return False
        for start_point, end_point in zip(previous_sample.contacts, sample.contacts):
            if line.intersects_segment(start_point, end_point):
                return True
        return False

    def _sample_post_distance(
        self,
        *,
        sample: TrajectorySample,
        line: PreparedLine,
        expected_side: Optional[int],
    ) -> float:
        if expected_side is None:
            return 0.0
        line_start, line_end = line.coords
        distances = [
            signed_distance_to_line(point, line_start, line_end)
            for point in sample.contacts
        ]
        relevant = [
            abs(distance)
            for distance in distances
            if self._distance_to_side(distance) == expected_side
        ]
        if relevant:
            return max(relevant)
        return abs(distances[1])

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
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        to_delete: list[int] = []
        for vid, st in self._vehicle_states.items():
            if st.last_seen_ts is None:
                continue
            while st.lane_history and st.lane_history[0][0].timestamp() < cutoff_ts:
                st.lane_history.popleft()
            while st.trajectory and st.trajectory[0].ts.timestamp() < cutoff_ts:
                st.trajectory.popleft()
            if st.last_seen_ts.timestamp() < cutoff_ts:
                to_delete.append(vid)
        for vid in to_delete:
            del self._vehicle_states[vid]
