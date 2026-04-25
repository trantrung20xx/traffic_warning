from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from math import atan2, hypot
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
    source_lane_duration_ms: int = 0


@dataclass
class TurnEvidence:
    maneuver: str
    score: float = 0.0
    first_seen_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None
    corridor_hits: int = 0
    exit_zone_hits: int = 0
    exit_line_hits: int = 0
    heading_support_hits: int = 0
    curvature_support_hits: int = 0
    opposite_direction_hits: int = 0
    temporal_hits: int = 0
    last_reject_reason: Optional[str] = None


@dataclass
class ViolationLifecycle:
    phase: str = "candidate"  # candidate -> confirmed -> emitted -> active -> expired
    event_window_id: int = 1
    first_ts: Optional[datetime] = None
    emitted_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None


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
class MotionFeatures:
    heading_vector: Optional[tuple[float, float]] = None
    entry_vector: Optional[tuple[float, float]] = None
    heading_change_deg: float = 0.0
    signed_heading_change_deg: float = 0.0
    curvature: float = 0.0
    opposite_direction: bool = False


@dataclass
class TurnState:
    phase: str = "idle"
    source_lane_id: Optional[int] = None
    confirmed_maneuver: Optional[str] = None
    last_activity_ts: Optional[datetime] = None

    entry_heading_vector: Optional[tuple[float, float]] = None
    lane_direction_vector: Optional[tuple[float, float]] = None
    last_scored_maneuver: Optional[str] = None
    evidences: dict[str, TurnEvidence] = field(default_factory=dict)
    last_reject_reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class VehicleState:
    vehicle_id: int
    vehicle_type: str

    current_stable_lane_id: Optional[int] = None
    current_lane_started_ts: Optional[datetime] = None
    lane_history: deque[tuple[datetime, int]] = field(default_factory=deque)
    illegal_lane_candidate: Optional[IllegalLaneCandidate] = None
    turn_state: TurnState = field(default_factory=TurnState)
    trajectory: deque[TrajectorySample] = field(default_factory=deque)
    line_crossings: dict[str, LineCrossingState] = field(default_factory=dict)
    violation_lifecycles: dict[str, ViolationLifecycle] = field(default_factory=dict)
    last_seen_ts: Optional[datetime] = None


class ViolationLogic:
    """
    Bộ luật phát hiện vi phạm dựa trên logic hình học + trajectory ngắn hạn.

    Nâng cấp chính:
    - Dùng evidence fusion deterministic thay vì single-signal.
    - Suy luận heading/curvature/opposite-direction tự động từ trajectory.
    - Quản lý lifecycle để chống double emit và chống lặp do overlap/lane drift.
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        vehicle_type_min_duration_ms: int = 900,
        wrong_lane_min_duration_ms: int = 1200,
        wrong_lane_min_source_stable_ms: int = 0,
        turn_region_min_hits: int = 3,
        turn_state_timeout_ms: int = 3000,
        trajectory_history_window_ms: int = 2000,
        line_crossing_side_tolerance_px: float = 2.0,
        line_crossing_min_pre_frames: int = 2,
        line_crossing_min_post_frames: int = 2,
        line_crossing_min_displacement_px: float = 2.0,
        line_crossing_min_displacement_ratio: float = 0.02,
        line_crossing_max_gap_ms: int = 400,
        line_crossing_cooldown_ms: int = 1200,
        violation_rearm_window_ms: int = 3500,
        evidence_expire_ms: int = 1600,
        motion_window_samples: int = 8,
        heading_straight_max_deg: float = 32.0,
        heading_turn_min_deg: float = 18.0,
        heading_turn_max_deg: float = 155.0,
        heading_u_turn_min_change_deg: float = 110.0,
        heading_side_sign_tolerance: float = 1e-6,
        heading_value_sign_tolerance: float = 1e-5,
        heading_straight_curvature_max: float = 0.28,
        curvature_u_turn_min: float = 0.2,
        curvature_straight_max: float = 0.24,
        curvature_turn_min: float = 0.04,
        curvature_fallback_min: float = 0.02,
        opposite_direction_cos_threshold: float = -0.3,
        evidence_decay_per_frame: float = 0.18,
        evidence_score_cap: float = 30.0,
        evidence_weight_corridor: float = 2.1,
        evidence_weight_exit_zone: float = 4.1,
        evidence_weight_exit_line: float = 5.2,
        evidence_weight_heading_support: float = 1.3,
        evidence_weight_curvature_support: float = 0.7,
        evidence_weight_opposite_direction: float = 2.0,
        evidence_weight_temporal_bonus: float = 0.4,
        evidence_penalty_no_signal: float = 0.35,
        evidence_temporal_hits_min: int = 2,
        evidence_strong_exit_min_temporal_hits: int = 2,
        evidence_strong_exit_min_corridor_hits: int = 2,
        threshold_turn_score: float = 4.2,
        threshold_turn_score_with_exit: float = 4.2,
        threshold_u_turn_score: float = 7.2,
        threshold_u_turn_score_with_exit: float = 5.0,
        threshold_straight_score: float = 4.5,
        trajectory_sample_inside_min_hits: int = 2,
        trajectory_entry_heading_lookback_points: int = 4,
        trajectory_heading_local_window_points: int = 3,
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
        self._lane_turn_corridors = self._build_lane_zone_collection(
            lane_polygons=lane_polygons,
            geometry_field="turn_corridor",
        )
        self._lane_exit_zones = self._build_lane_zone_collection(
            lane_polygons=lane_polygons,
            geometry_field="exit_zone",
        )
        self._lane_exit_lines = self._build_lane_line_collection(
            lane_polygons=lane_polygons,
            geometry_field="exit_line",
        )

        self._vehicle_type_min_duration_ms = int(vehicle_type_min_duration_ms)
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._wrong_lane_min_source_stable_ms = int(wrong_lane_min_source_stable_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._turn_state_timeout_ms = int(turn_state_timeout_ms)
        self._trajectory_history_window_ms = int(trajectory_history_window_ms)
        self._line_crossing_side_tolerance_px = float(line_crossing_side_tolerance_px)
        self._line_crossing_min_pre_frames = int(line_crossing_min_pre_frames)
        self._line_crossing_min_post_frames = int(line_crossing_min_post_frames)
        self._line_crossing_min_displacement_px = float(line_crossing_min_displacement_px)
        self._line_crossing_min_displacement_ratio = float(line_crossing_min_displacement_ratio)
        self._line_crossing_max_gap_ms = int(line_crossing_max_gap_ms)
        self._line_crossing_cooldown_ms = int(line_crossing_cooldown_ms)
        self._violation_rearm_window_ms = int(violation_rearm_window_ms)
        self._evidence_expire_ms = int(evidence_expire_ms)
        self._motion_window_samples = max(int(motion_window_samples), 3)
        self._trajectory_sample_inside_min_hits = max(int(trajectory_sample_inside_min_hits), 1)
        self._trajectory_entry_heading_lookback_points = max(int(trajectory_entry_heading_lookback_points), 2)
        self._trajectory_heading_local_window_points = max(int(trajectory_heading_local_window_points), 2)

        self._straight_heading_max_deg = float(heading_straight_max_deg)
        self._turn_heading_min_deg = float(heading_turn_min_deg)
        self._turn_heading_max_deg = float(heading_turn_max_deg)
        self._u_turn_min_heading_change_deg = float(heading_u_turn_min_change_deg)
        self._heading_side_sign_tolerance = abs(float(heading_side_sign_tolerance))
        self._heading_value_sign_tolerance = abs(float(heading_value_sign_tolerance))
        self._straight_curvature_max_for_heading_support = float(heading_straight_curvature_max)
        self._u_turn_min_curvature = float(curvature_u_turn_min)
        self._straight_curvature_max = float(curvature_straight_max)
        self._turn_curvature_min = float(curvature_turn_min)
        self._fallback_curvature_min = float(curvature_fallback_min)
        self._opposite_direction_cos_threshold = float(opposite_direction_cos_threshold)
        self._evidence_decay_per_frame = float(evidence_decay_per_frame)
        self._evidence_score_cap = max(float(evidence_score_cap), 0.0)
        self._evidence_weight_corridor = float(evidence_weight_corridor)
        self._evidence_weight_exit_zone = float(evidence_weight_exit_zone)
        self._evidence_weight_exit_line = float(evidence_weight_exit_line)
        self._evidence_weight_heading_support = float(evidence_weight_heading_support)
        self._evidence_weight_curvature_support = float(evidence_weight_curvature_support)
        self._evidence_weight_opposite_direction = float(evidence_weight_opposite_direction)
        self._evidence_weight_temporal_bonus = float(evidence_weight_temporal_bonus)
        self._evidence_penalty_no_signal = float(evidence_penalty_no_signal)
        self._evidence_temporal_hits_min = max(int(evidence_temporal_hits_min), 1)
        self._evidence_strong_exit_min_temporal_hits = max(int(evidence_strong_exit_min_temporal_hits), 1)
        self._evidence_strong_exit_min_corridor_hits = max(int(evidence_strong_exit_min_corridor_hits), 1)
        self._turn_score_threshold = float(threshold_turn_score)
        self._turn_score_threshold_with_exit = float(threshold_turn_score_with_exit)
        self._u_turn_score_threshold = float(threshold_u_turn_score)
        self._u_turn_score_threshold_with_exit = float(threshold_u_turn_score_with_exit)
        self._straight_score_threshold = float(threshold_straight_score)

        self._lane_commit_points = {
            lp.lane_id: self._lane_commit_point(lp)
            for lp in lane_polygons
        }
        self._lane_direction_vectors = {
            lp.lane_id: self._lane_direction_vector(lp)
            for lp in lane_polygons
        }
        self._lane_known_maneuvers = self._build_lane_known_maneuvers(lane_polygons)
        self._lane_maneuver_anchor_points = self._build_lane_maneuver_anchor_points(lane_polygons)

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
                if allowed_vehicle_types and vehicle_type not in allowed_vehicle_types:
                    lifecycle_key = f"vehicle_type_not_allowed:lane_{current_lane_id}:veh_{vehicle_type}"
                    self._emit_violation_if_needed(
                        st=st,
                        lifecycle_key=lifecycle_key,
                        lane_id=current_lane_id,
                        violation="vehicle_type_not_allowed",
                        ts=ts,
                        min_active_ms=self._vehicle_type_min_duration_ms,
                        evidence_summary={
                            "rule": "vehicle_type_not_allowed",
                            "vehicle_type": vehicle_type,
                            "stable_lane_id": current_lane_id,
                            "allowed_vehicle_types": list(allowed_vehicle_types),
                        },
                        violations=violations,
                    )

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
            st.current_lane_started_ts = ts
            self._append_lane_history(st=st, lane_id=lane_id, ts=ts)
            self._update_wrong_lane_candidate(st=st, ts=ts, violations=violations)
            return

        if lane_id != previous_lane_id:
            source_lane_started_ts = st.current_lane_started_ts or ts
            source_lane_duration_ms = int((ts - source_lane_started_ts).total_seconds() * 1000.0)
            source_allows_vehicle_type = self._lane_allows_vehicle_type(
                lane_id=previous_lane_id,
                vehicle_type=st.vehicle_type,
            )
            target_allows_vehicle_type = self._lane_allows_vehicle_type(
                lane_id=lane_id,
                vehicle_type=st.vehicle_type,
            )
            corrective_transition = (not source_allows_vehicle_type) and target_allows_vehicle_type

            st.current_stable_lane_id = lane_id
            st.current_lane_started_ts = ts
            self._append_lane_history(st=st, lane_id=lane_id, ts=ts)

            allowed_lane_changes = self._allowed_lane_changes_for(previous_lane_id)
            if lane_id in allowed_lane_changes or corrective_transition:
                st.illegal_lane_candidate = None
            else:
                st.illegal_lane_candidate = IllegalLaneCandidate(
                    source_lane_id=previous_lane_id,
                    target_lane_id=lane_id,
                    started_ts=ts,
                    source_lane_duration_ms=source_lane_duration_ms,
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

        if candidate.source_lane_duration_ms < self._wrong_lane_min_source_stable_ms:
            st.illegal_lane_candidate = None
            return

        if self._is_corrective_lane_transition(
            vehicle_type=st.vehicle_type,
            source_lane_id=candidate.source_lane_id,
            target_lane_id=candidate.target_lane_id,
        ):
            st.illegal_lane_candidate = None
            return

        duration_ms = int((ts - candidate.started_ts).total_seconds() * 1000.0)
        if duration_ms < self._wrong_lane_min_duration_ms:
            return

        lifecycle_key = f"wrong_lane:{candidate.source_lane_id}->{candidate.target_lane_id}"
        self._emit_violation_if_needed(
            st=st,
            lifecycle_key=lifecycle_key,
            lane_id=candidate.target_lane_id,
            violation="wrong_lane",
            ts=ts,
            evidence_summary={
                "rule": "lane_change_rule",
                "source_lane_id": candidate.source_lane_id,
                "target_lane_id": candidate.target_lane_id,
                "source_lane_duration_ms": candidate.source_lane_duration_ms,
                "target_lane_duration_ms": duration_ms,
            },
            violations=violations,
        )

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
            self._enter_committed(st=st, turn_state=turn_state, ts=ts)

        # Fallback: cho phép vào pha committed nếu lane chưa cấu hình commit gate/line,
        # nhưng đã có bằng chứng turn theo corridor/exit ngay trong lane hiện tại.
        if (
            turn_state.phase != "committed"
            and current_lane_id is not None
            and not self._lane_has_commit_signal(lane_id=current_lane_id)
            and self._lane_has_turn_evidence(
                lane_id=current_lane_id,
                sample=sample,
                line_events=line_events,
            )
        ):
            if turn_state.phase != "approach" or turn_state.source_lane_id != current_lane_id:
                self._enter_approach(turn_state=turn_state, source_lane_id=current_lane_id, ts=ts)
            self._enter_committed(st=st, turn_state=turn_state, ts=ts)

        if turn_state.phase != "committed" or turn_state.source_lane_id is None:
            return

        confirmed_maneuver = self._update_turn_confirmation(
            st=st,
            turn_state=turn_state,
            source_lane_id=turn_state.source_lane_id,
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
            lifecycle_key = (
                f"{violation_key}:lane_{turn_state.source_lane_id}:maneuver_{confirmed_maneuver}"
            )
            evidence = turn_state.evidences.get(confirmed_maneuver)
            motion = self._compute_motion_features(st=st, turn_state=turn_state)
            self._emit_violation_if_needed(
                st=st,
                lifecycle_key=lifecycle_key,
                lane_id=turn_state.source_lane_id,
                violation=violation_key,
                ts=ts,
                evidence_summary=self._build_turn_evidence_summary(
                    source_lane_id=turn_state.source_lane_id,
                    maneuver=confirmed_maneuver,
                    evidence=evidence,
                    motion=motion,
                ),
                violations=violations,
            )

    def _enter_approach(self, *, turn_state: TurnState, source_lane_id: int, ts: datetime) -> None:
        if turn_state.phase == "committed":
            return
        if turn_state.phase == "approach" and turn_state.source_lane_id == source_lane_id:
            turn_state.last_activity_ts = ts
            return

        turn_state.phase = "approach"
        turn_state.source_lane_id = source_lane_id
        turn_state.confirmed_maneuver = None
        turn_state.last_activity_ts = ts
        turn_state.entry_heading_vector = None
        turn_state.lane_direction_vector = self._lane_direction_vectors.get(source_lane_id)
        turn_state.last_scored_maneuver = None
        turn_state.evidences.clear()
        turn_state.last_reject_reasons.clear()

    def _enter_committed(self, *, st: VehicleState, turn_state: TurnState, ts: datetime) -> None:
        if turn_state.phase == "committed":
            turn_state.last_activity_ts = ts
            return
        if turn_state.source_lane_id is None:
            return

        turn_state.phase = "committed"
        turn_state.last_activity_ts = ts
        turn_state.entry_heading_vector = self._estimate_entry_heading_vector(st=st, turn_state=turn_state)
        if turn_state.lane_direction_vector is None:
            turn_state.lane_direction_vector = self._lane_direction_vectors.get(turn_state.source_lane_id)

    def _update_turn_confirmation(
        self,
        *,
        st: VehicleState,
        turn_state: TurnState,
        source_lane_id: int,
        sample: TrajectorySample,
        ts: datetime,
        line_events: set[str],
    ) -> Optional[str]:
        exit_line_matches = self._extract_exit_line_matches(
            line_events=line_events,
            source_lane_id=source_lane_id,
        )

        exit_zone_matches = set(
            self._match_lane_zone_collection(
                self._lane_exit_zones,
                lane_id=source_lane_id,
                sample=sample,
            )
        )

        corridor_matches = set(
            self._match_lane_zone_collection(
                self._lane_turn_corridors,
                lane_id=source_lane_id,
                sample=sample,
            )
        )
        if not (exit_line_matches or exit_zone_matches or corridor_matches or turn_state.evidences):
            return None

        self._decay_turn_evidence(turn_state=turn_state, ts=ts)
        motion = self._compute_motion_features(st=st, turn_state=turn_state)
        maneuvers_to_score = (
            set(turn_state.evidences)
            | exit_line_matches
            | exit_zone_matches
            | corridor_matches
            | self._maneuver_set_for_lane(source_lane_id=source_lane_id)
        )
        maneuvers_to_score = {m for m in maneuvers_to_score if m}
        if not maneuvers_to_score:
            return None

        best_frame_maneuver: Optional[str] = None
        best_frame_score = 0.0

        for maneuver in maneuvers_to_score:
            evidence = turn_state.evidences.get(maneuver)
            if evidence is None:
                evidence = TurnEvidence(maneuver=maneuver)
                turn_state.evidences[maneuver] = evidence

            frame_score = self._score_maneuver_evidence(
                maneuver=maneuver,
                evidence=evidence,
                source_lane_id=source_lane_id,
                motion=motion,
                turn_state=turn_state,
                ts=ts,
                corridor_matches=corridor_matches,
                exit_zone_matches=exit_zone_matches,
                exit_line_matches=exit_line_matches,
            )
            if frame_score > best_frame_score:
                best_frame_score = frame_score
                best_frame_maneuver = maneuver

        if best_frame_maneuver is not None:
            turn_state.last_scored_maneuver = best_frame_maneuver
            turn_state.last_activity_ts = ts

        ranked = sorted(
            turn_state.evidences.values(),
            key=lambda ev: (
                ev.score,
                ev.exit_line_hits + ev.exit_zone_hits,
                ev.temporal_hits,
            ),
            reverse=True,
        )
        turn_state.last_reject_reasons.clear()
        for evidence in ranked:
            if self._evidence_confirms_maneuver(
                maneuver=evidence.maneuver,
                evidence=evidence,
                source_lane_id=source_lane_id,
                motion=motion,
            ):
                turn_state.last_reject_reasons.clear()
                return evidence.maneuver
            if evidence.last_reject_reason:
                turn_state.last_reject_reasons[evidence.maneuver] = evidence.last_reject_reason
        return None

    def _decay_turn_evidence(self, *, turn_state: TurnState, ts: datetime) -> None:
        stale: list[str] = []
        for maneuver, evidence in turn_state.evidences.items():
            if evidence.last_seen_ts is not None:
                elapsed_ms = int((ts - evidence.last_seen_ts).total_seconds() * 1000.0)
                if elapsed_ms > self._evidence_expire_ms:
                    stale.append(maneuver)
                    continue
            evidence.score = max(evidence.score - self._evidence_decay_per_frame, 0.0)
        for maneuver in stale:
            del turn_state.evidences[maneuver]
            if turn_state.last_scored_maneuver == maneuver:
                turn_state.last_scored_maneuver = None

    def _score_maneuver_evidence(
        self,
        *,
        maneuver: str,
        evidence: TurnEvidence,
        source_lane_id: int,
        motion: MotionFeatures,
        turn_state: TurnState,
        ts: datetime,
        corridor_matches: set[str],
        exit_zone_matches: set[str],
        exit_line_matches: set[str],
    ) -> float:
        frame_score = 0.0
        signal_hit = False

        if maneuver in corridor_matches:
            evidence.corridor_hits += 1
            signal_hit = True
            frame_score += self._evidence_weight_corridor
        if maneuver in exit_zone_matches:
            evidence.exit_zone_hits += 1
            signal_hit = True
            frame_score += self._evidence_weight_exit_zone
        if maneuver in exit_line_matches:
            evidence.exit_line_hits += 1
            signal_hit = True
            frame_score += self._evidence_weight_exit_line

        heading_support = self._heading_support_for_maneuver(
            maneuver=maneuver,
            source_lane_id=source_lane_id,
            motion=motion,
        )
        if heading_support:
            evidence.heading_support_hits += 1
            frame_score += self._evidence_weight_heading_support

        curvature_support = self._curvature_support_for_maneuver(maneuver=maneuver, motion=motion)
        if curvature_support:
            evidence.curvature_support_hits += 1
            frame_score += self._evidence_weight_curvature_support

        opposite_support = maneuver == "u_turn" and motion.opposite_direction
        if opposite_support:
            evidence.opposite_direction_hits += 1
            frame_score += self._evidence_weight_opposite_direction

        if signal_hit or heading_support:
            if evidence.first_seen_ts is None:
                evidence.first_seen_ts = ts
            evidence.last_seen_ts = ts
            if turn_state.last_scored_maneuver == maneuver:
                evidence.temporal_hits += 1
                frame_score += self._evidence_weight_temporal_bonus
            else:
                evidence.temporal_hits = max(evidence.temporal_hits, 1)
        else:
            frame_score -= self._evidence_penalty_no_signal

        evidence.score = min(max(evidence.score + frame_score, 0.0), self._evidence_score_cap)
        return frame_score

    def _evidence_confirms_maneuver(
        self,
        *,
        maneuver: str,
        evidence: TurnEvidence,
        source_lane_id: int,
        motion: MotionFeatures,
    ) -> bool:
        evidence.last_reject_reason = None
        has_path_evidence = (
            evidence.corridor_hits > 0
            or evidence.exit_zone_hits > 0
            or evidence.exit_line_hits > 0
        )
        if not has_path_evidence:
            return self._reject_evidence(
                evidence=evidence,
                reason="missing_path_evidence",
            )

        strong_exit = evidence.exit_line_hits > 0 or evidence.exit_zone_hits > 0
        temporal_ok = evidence.temporal_hits >= self._evidence_temporal_hits_min or strong_exit

        if maneuver == "u_turn":
            if motion.heading_change_deg < self._u_turn_min_heading_change_deg:
                return self._reject_evidence(
                    evidence=evidence,
                    reason="u_turn_heading_change_too_small",
                )
            if not motion.opposite_direction and evidence.opposite_direction_hits <= 0:
                return self._reject_evidence(
                    evidence=evidence,
                    reason="u_turn_opposite_direction_missing",
                )
            if motion.curvature < self._u_turn_min_curvature and evidence.curvature_support_hits <= 0:
                return self._reject_evidence(
                    evidence=evidence,
                    reason="u_turn_curvature_too_low",
                )
            if not strong_exit and evidence.corridor_hits < self._turn_region_min_hits:
                return self._reject_evidence(
                    evidence=evidence,
                    reason="u_turn_corridor_support_too_low",
                )
            score_threshold = (
                self._u_turn_score_threshold_with_exit
                if strong_exit
                else self._u_turn_score_threshold
            )
            return self._confirm_with_threshold(
                evidence=evidence,
                score_threshold=score_threshold,
                temporal_ok=temporal_ok,
            )

        if maneuver == "straight":
            if not self._heading_support_for_maneuver(
                maneuver=maneuver,
                source_lane_id=source_lane_id,
                motion=motion,
            ):
                return self._reject_evidence(
                    evidence=evidence,
                    reason="straight_heading_not_supported",
                )
            if not strong_exit and evidence.corridor_hits < self._turn_region_min_hits:
                return self._reject_evidence(
                    evidence=evidence,
                    reason="straight_corridor_support_too_low",
                )
            return self._confirm_with_threshold(
                evidence=evidence,
                score_threshold=self._straight_score_threshold,
                temporal_ok=temporal_ok,
            )

        if maneuver in {"left", "right"}:
            if (
                evidence.heading_support_hits <= 0
                and not strong_exit
                and evidence.corridor_hits < self._turn_region_min_hits
            ):
                return self._reject_evidence(
                    evidence=evidence,
                    reason=f"{maneuver}_heading_or_path_support_missing",
                )
            if motion.opposite_direction and motion.heading_change_deg >= self._u_turn_min_heading_change_deg:
                return self._reject_evidence(
                    evidence=evidence,
                    reason=f"{maneuver}_looks_like_u_turn",
                )
            if (
                strong_exit
                and evidence.temporal_hits < self._evidence_strong_exit_min_temporal_hits
                and evidence.corridor_hits < self._evidence_strong_exit_min_corridor_hits
            ):
                return self._reject_evidence(
                    evidence=evidence,
                    reason=f"{maneuver}_weak_exit_without_temporal_support",
                )
            if not strong_exit and evidence.corridor_hits < self._turn_region_min_hits:
                return self._reject_evidence(
                    evidence=evidence,
                    reason=f"{maneuver}_corridor_support_too_low",
                )
            score_threshold = (
                self._turn_score_threshold_with_exit
                if strong_exit
                else self._turn_score_threshold
            )
            return self._confirm_with_threshold(
                evidence=evidence,
                score_threshold=score_threshold,
                temporal_ok=temporal_ok,
            )

        # Các maneuver lạ (nếu có) vẫn giữ rule deterministic.
        return self._confirm_with_threshold(
            evidence=evidence,
            score_threshold=self._turn_score_threshold,
            temporal_ok=temporal_ok,
        )

    @staticmethod
    def _reject_evidence(*, evidence: TurnEvidence, reason: str) -> bool:
        evidence.last_reject_reason = reason
        return False

    def _confirm_with_threshold(
        self,
        *,
        evidence: TurnEvidence,
        score_threshold: float,
        temporal_ok: bool,
    ) -> bool:
        if evidence.score < score_threshold:
            return self._reject_evidence(
                evidence=evidence,
                reason="score_below_threshold",
            )
        if not temporal_ok:
            return self._reject_evidence(
                evidence=evidence,
                reason="temporal_consistency_not_met",
            )
        evidence.last_reject_reason = None
        return True

    def _build_turn_evidence_summary(
        self,
        *,
        source_lane_id: int,
        maneuver: str,
        evidence: Optional[TurnEvidence],
        motion: MotionFeatures,
    ) -> dict:
        if evidence is None:
            return {
                "rule": "turn_maneuver",
                "source_lane_id": source_lane_id,
                "maneuver": maneuver,
            }
        summary = {
            "rule": "turn_maneuver",
            "source_lane_id": source_lane_id,
            "maneuver": maneuver,
            "score": round(float(evidence.score), 3),
            "corridor_hits": int(evidence.corridor_hits),
            "exit_zone_hits": int(evidence.exit_zone_hits),
            "exit_line_hits": int(evidence.exit_line_hits),
            "heading_support_hits": int(evidence.heading_support_hits),
            "curvature_support_hits": int(evidence.curvature_support_hits),
            "opposite_direction_hits": int(evidence.opposite_direction_hits),
            "temporal_hits": int(evidence.temporal_hits),
            "heading_change_deg": round(float(motion.heading_change_deg), 2),
            "curvature": round(float(motion.curvature), 4),
            "opposite_direction": bool(motion.opposite_direction),
        }
        if evidence.last_reject_reason:
            summary["reject_reason"] = evidence.last_reject_reason
        return summary

    def _estimate_entry_heading_vector(
        self,
        *,
        st: VehicleState,
        turn_state: TurnState,
    ) -> Optional[tuple[float, float]]:
        points = [sample.center for sample in st.trajectory]
        if len(points) >= 2:
            start_idx = max(len(points) - self._trajectory_entry_heading_lookback_points, 0)
            start = points[start_idx]
            end = points[-1]
            vec = self._normalize_vector((end[0] - start[0], end[1] - start[1]))
            if vec is not None:
                return vec
        if turn_state.source_lane_id is not None:
            return self._lane_direction_vectors.get(turn_state.source_lane_id)
        return None

    def _compute_motion_features(self, *, st: VehicleState, turn_state: TurnState) -> MotionFeatures:
        samples = list(st.trajectory)
        if len(samples) < 2:
            return MotionFeatures(
                entry_vector=turn_state.entry_heading_vector or turn_state.lane_direction_vector
            )

        recent = samples[-self._motion_window_samples:]
        path_length = 0.0
        for idx in range(1, len(recent)):
            path_length += hypot(
                recent[idx].center[0] - recent[idx - 1].center[0],
                recent[idx].center[1] - recent[idx - 1].center[1],
            )

        start = recent[0].center
        end = recent[-1].center
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        displacement = hypot(dx, dy)
        local_start = recent[max(len(recent) - self._trajectory_heading_local_window_points, 0)].center
        heading_vector = self._normalize_vector((end[0] - local_start[0], end[1] - local_start[1]))
        if heading_vector is None:
            heading_vector = self._normalize_vector((dx, dy))
        entry_vector = turn_state.entry_heading_vector or turn_state.lane_direction_vector

        heading_change_deg = 0.0
        signed_heading_change_deg = 0.0
        opposite_direction = False
        if heading_vector is not None and entry_vector is not None:
            cross = (entry_vector[0] * heading_vector[1]) - (entry_vector[1] * heading_vector[0])
            dot = (entry_vector[0] * heading_vector[0]) + (entry_vector[1] * heading_vector[1])
            heading_change_deg = abs(atan2(cross, dot)) * (180.0 / 3.141592653589793)
            signed_heading_change_deg = atan2(cross, dot) * (180.0 / 3.141592653589793)
            opposite_direction = dot <= self._opposite_direction_cos_threshold

        curvature = 0.0
        if displacement > 1e-6:
            curvature = max((path_length / displacement) - 1.0, 0.0)

        return MotionFeatures(
            heading_vector=heading_vector,
            entry_vector=entry_vector,
            heading_change_deg=heading_change_deg,
            signed_heading_change_deg=signed_heading_change_deg,
            curvature=curvature,
            opposite_direction=opposite_direction,
        )

    def _heading_support_for_maneuver(
        self,
        *,
        maneuver: str,
        source_lane_id: int,
        motion: MotionFeatures,
    ) -> bool:
        if motion.entry_vector is None or motion.heading_vector is None:
            return False

        angle = motion.heading_change_deg
        if maneuver == "straight":
            return (
                angle <= self._straight_heading_max_deg
                and motion.curvature <= self._straight_curvature_max_for_heading_support
            )

        if maneuver == "u_turn":
            return angle >= self._u_turn_min_heading_change_deg and motion.opposite_direction

        if maneuver not in {"left", "right"}:
            return angle >= self._turn_heading_min_deg

        if not (self._turn_heading_min_deg <= angle <= self._turn_heading_max_deg):
            return False

        observed_sign = self._sign_of_value(
            motion.signed_heading_change_deg,
            tolerance=self._heading_value_sign_tolerance,
        )
        expected_sign = self._expected_turn_side_sign(source_lane_id=source_lane_id, maneuver=maneuver)
        if expected_sign is None or expected_sign == 0:
            return observed_sign != 0
        return observed_sign == expected_sign

    def _curvature_support_for_maneuver(self, *, maneuver: str, motion: MotionFeatures) -> bool:
        if maneuver == "straight":
            return motion.curvature <= self._straight_curvature_max
        if maneuver == "u_turn":
            return motion.curvature >= self._u_turn_min_curvature
        if maneuver in {"left", "right"}:
            return motion.curvature >= self._turn_curvature_min
        return motion.curvature >= self._fallback_curvature_min

    def _expected_turn_side_sign(self, *, source_lane_id: int, maneuver: str) -> Optional[int]:
        lane_dir = self._lane_direction_vectors.get(source_lane_id)
        commit_point = self._lane_commit_points.get(source_lane_id)
        anchor_point = (self._lane_maneuver_anchor_points.get(source_lane_id) or {}).get(maneuver)
        if lane_dir is None or commit_point is None or anchor_point is None:
            return None
        rel_vec = (anchor_point[0] - commit_point[0], anchor_point[1] - commit_point[1])
        side_metric = (lane_dir[0] * rel_vec[1]) - (lane_dir[1] * rel_vec[0])
        return self._sign_of_value(side_metric, tolerance=self._heading_side_sign_tolerance)

    def _expire_turn_state(self, *, turn_state: TurnState, ts: datetime) -> None:
        if turn_state.last_activity_ts is None:
            return
        elapsed_ms = int((ts - turn_state.last_activity_ts).total_seconds() * 1000.0)
        if elapsed_ms > self._turn_state_timeout_ms:
            self._reset_turn_state(turn_state=turn_state)

    def _reset_turn_state(self, *, turn_state: TurnState) -> None:
        turn_state.phase = "idle"
        turn_state.source_lane_id = None
        turn_state.confirmed_maneuver = None
        turn_state.last_activity_ts = None
        turn_state.entry_heading_vector = None
        turn_state.lane_direction_vector = None
        turn_state.last_scored_maneuver = None
        turn_state.evidences.clear()
        turn_state.last_reject_reasons.clear()

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

    def _lane_has_commit_signal(self, *, lane_id: int) -> bool:
        return self._commit_gate_shapes.get(lane_id) is not None or self._commit_line_shapes.get(lane_id) is not None

    def _lane_has_turn_evidence(
        self,
        *,
        lane_id: int,
        sample: TrajectorySample,
        line_events: set[str],
    ) -> bool:
        if self._extract_exit_line_matches(line_events=line_events, source_lane_id=lane_id):
            return True
        if self._match_lane_zone_collection(self._lane_exit_zones, lane_id=lane_id, sample=sample):
            return True
        if self._match_lane_zone_collection(self._lane_turn_corridors, lane_id=lane_id, sample=sample):
            return True
        return False

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
        return hits >= self._trajectory_sample_inside_min_hits

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

        for lane_id, lane_lines in self._lane_exit_lines.items():
            for maneuver, line in lane_lines.items():
                key = f"exit_lane:{lane_id}:{maneuver}"
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

    def _build_lane_zone_collection(
        self,
        *,
        lane_polygons: list[LanePolygon],
        geometry_field: str,
    ) -> dict[int, dict[str, PreparedPolygon]]:
        by_lane: dict[int, dict[str, PreparedPolygon]] = {}
        for lane in lane_polygons:
            maneuvers = getattr(lane, "maneuvers", None) or {}
            if not isinstance(maneuvers, dict):
                continue
            lane_map: dict[str, PreparedPolygon] = {}
            for maneuver, maneuver_cfg in maneuvers.items():
                if not self._maneuver_is_enabled(maneuver_cfg):
                    continue
                points = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field=geometry_field)
                if not points:
                    continue
                lane_map[maneuver] = PreparedPolygon.from_points(points)
            if lane_map:
                by_lane[lane.lane_id] = lane_map
        return by_lane

    def _build_lane_line_collection(
        self,
        *,
        lane_polygons: list[LanePolygon],
        geometry_field: str,
    ) -> dict[int, dict[str, PreparedLine]]:
        by_lane: dict[int, dict[str, PreparedLine]] = {}
        for lane in lane_polygons:
            maneuvers = getattr(lane, "maneuvers", None) or {}
            if not isinstance(maneuvers, dict):
                continue
            lane_map: dict[str, PreparedLine] = {}
            for maneuver, maneuver_cfg in maneuvers.items():
                if not self._maneuver_is_enabled(maneuver_cfg):
                    continue
                points = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field=geometry_field)
                if not points:
                    continue
                lane_map[maneuver] = PreparedLine.from_points(points)
            if lane_map:
                by_lane[lane.lane_id] = lane_map
        return by_lane

    def _extract_maneuver_points(
        self,
        *,
        maneuver_cfg,
        field: str,
    ) -> Optional[list[list[float]]]:
        if maneuver_cfg is None:
            return None
        if isinstance(maneuver_cfg, dict):
            points = maneuver_cfg.get(field)
        else:
            points = getattr(maneuver_cfg, field, None)
        if not points or not isinstance(points, list):
            return None
        normalized: list[list[float]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            normalized.append([float(point[0]), float(point[1])])
        return normalized or None

    def _match_lane_zone_collection(
        self,
        collection: dict[int, dict[str, PreparedPolygon]],
        *,
        lane_id: int,
        sample: TrajectorySample,
    ) -> list[str]:
        lane_map = collection.get(lane_id) or {}
        matches: list[str] = []
        for maneuver, polygon in lane_map.items():
            if self._sample_inside_polygon(sample=sample, polygon=polygon):
                matches.append(maneuver)
        return matches

    def _extract_exit_line_matches(
        self,
        *,
        line_events: set[str],
        source_lane_id: int,
    ) -> set[str]:
        matches: set[str] = set()
        lane_prefix = f"exit_lane:{source_lane_id}:"
        for key in line_events:
            if key.startswith(lane_prefix):
                matches.add(key[len(lane_prefix):])
        return matches

    def _maneuver_set_for_lane(self, *, source_lane_id: int) -> set[str]:
        maneuvers = self._lane_known_maneuvers.get(source_lane_id) or set()
        return {maneuver for maneuver in maneuvers if maneuver}

    def _allowed_lane_changes_for(self, lane_id: int) -> list[int]:
        lane_cfg = self._lane_by_id.get(lane_id)
        if lane_cfg is None or lane_cfg.allowed_lane_changes is None:
            return [lane_id]
        return lane_cfg.allowed_lane_changes

    def _lane_allows_vehicle_type(self, *, lane_id: int, vehicle_type: str) -> bool:
        lane_cfg = self._lane_by_id.get(lane_id)
        if lane_cfg is None:
            return True
        allowed_vehicle_types = lane_cfg.allowed_vehicle_types
        if not allowed_vehicle_types:
            return True
        return vehicle_type in allowed_vehicle_types

    def _is_corrective_lane_transition(
        self,
        *,
        vehicle_type: str,
        source_lane_id: int,
        target_lane_id: int,
    ) -> bool:
        source_allows = self._lane_allows_vehicle_type(lane_id=source_lane_id, vehicle_type=vehicle_type)
        target_allows = self._lane_allows_vehicle_type(lane_id=target_lane_id, vehicle_type=vehicle_type)
        return (not source_allows) and target_allows

    def _append_lane_history(self, *, st: VehicleState, lane_id: int, ts: datetime) -> None:
        if st.lane_history and st.lane_history[-1][1] == lane_id:
            st.lane_history[-1] = (ts, lane_id)
            return
        st.lane_history.append((ts, lane_id))

    def _emit_violation_if_needed(
        self,
        *,
        st: VehicleState,
        lifecycle_key: str,
        lane_id: int,
        violation: str,
        ts: datetime,
        min_active_ms: int = 0,
        evidence_summary: Optional[dict] = None,
        violations: list[dict],
    ) -> None:
        lifecycle = self._touch_violation_lifecycle(st=st, key=lifecycle_key, ts=ts)
        lifecycle.phase = "confirmed"

        if lifecycle.first_ts is None:
            lifecycle.first_ts = ts
        active_ms = int((ts - lifecycle.first_ts).total_seconds() * 1000.0)
        if active_ms < int(min_active_ms):
            lifecycle.phase = "candidate"
            lifecycle.last_seen_ts = ts
            return

        if lifecycle.emitted_ts is not None:
            lifecycle.phase = "active"
            lifecycle.last_seen_ts = ts
            return

        lifecycle.phase = "emitted"
        lifecycle.emitted_ts = ts
        lifecycle.phase = "active"
        lifecycle.last_seen_ts = ts
        payload = {"lane_id": lane_id, "violation": violation}
        if evidence_summary:
            payload["evidence_summary"] = evidence_summary
        violations.append(payload)

    def _touch_violation_lifecycle(self, *, st: VehicleState, key: str, ts: datetime) -> ViolationLifecycle:
        lifecycle = st.violation_lifecycles.get(key)
        if lifecycle is None:
            lifecycle = ViolationLifecycle(first_ts=ts, last_seen_ts=ts)
            st.violation_lifecycles[key] = lifecycle
            return lifecycle

        if lifecycle.last_seen_ts is not None:
            elapsed_ms = int((ts - lifecycle.last_seen_ts).total_seconds() * 1000.0)
            if elapsed_ms > self._violation_rearm_window_ms:
                lifecycle.phase = "expired"
                lifecycle.event_window_id += 1
                lifecycle.first_ts = ts
                lifecycle.emitted_ts = None

        if lifecycle.phase == "expired":
            lifecycle.phase = "candidate"
        lifecycle.last_seen_ts = ts
        return lifecycle

    def _lane_commit_point(self, lane: LanePolygon) -> tuple[float, float]:
        if lane.commit_gate:
            return self._centroid_of_points(lane.commit_gate)
        if lane.commit_line:
            return self._line_midpoint(lane.commit_line)
        if lane.approach_zone:
            return self._centroid_of_points(lane.approach_zone)
        return self._centroid_of_points(lane.polygon)

    def _lane_direction_vector(self, lane: LanePolygon) -> tuple[float, float]:
        commit_point = self._lane_commit_points.get(lane.lane_id)
        if commit_point is None:
            commit_point = self._lane_commit_point(lane)

        if lane.approach_zone:
            approach_center = self._centroid_of_points(lane.approach_zone)
            vec = self._normalize_vector(
                (
                    commit_point[0] - approach_center[0],
                    commit_point[1] - approach_center[1],
                )
            )
            if vec is not None:
                return vec

        lane_center = self._centroid_of_points(lane.polygon)
        vec = self._normalize_vector(
            (
                commit_point[0] - lane_center[0],
                commit_point[1] - lane_center[1],
            )
        )
        if vec is not None:
            return vec
        return (0.0, 1.0)

    def _build_lane_known_maneuvers(
        self,
        lane_polygons: list[LanePolygon],
    ) -> dict[int, set[str]]:
        by_lane: dict[int, set[str]] = {}
        for lane in lane_polygons:
            lane_set: set[str] = set()
            maneuvers = getattr(lane, "maneuvers", None) or {}
            if isinstance(maneuvers, dict):
                for maneuver, cfg in maneuvers.items():
                    if not maneuver:
                        continue
                    if not self._maneuver_is_enabled(cfg):
                        continue
                    lane_set.add(maneuver)
            if lane.allowed_maneuvers:
                for maneuver in lane.allowed_maneuvers:
                    cfg = maneuvers.get(maneuver) if isinstance(maneuvers, dict) else None
                    if maneuver and (cfg is None or self._maneuver_is_enabled(cfg)):
                        lane_set.add(maneuver)
            if lane_set:
                by_lane[lane.lane_id] = lane_set
        return by_lane

    def _build_lane_maneuver_anchor_points(
        self,
        lane_polygons: list[LanePolygon],
    ) -> dict[int, dict[str, tuple[float, float]]]:
        anchors_by_lane: dict[int, dict[str, tuple[float, float]]] = {}
        for lane in lane_polygons:
            maneuvers = getattr(lane, "maneuvers", None) or {}
            if not isinstance(maneuvers, dict):
                continue
            lane_anchors: dict[str, tuple[float, float]] = {}
            for maneuver, maneuver_cfg in maneuvers.items():
                if not self._maneuver_is_enabled(maneuver_cfg):
                    continue
                exit_line = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field="exit_line")
                if exit_line:
                    lane_anchors[maneuver] = self._line_midpoint(exit_line)
                    continue
                exit_zone = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field="exit_zone")
                if exit_zone:
                    lane_anchors[maneuver] = self._centroid_of_points(exit_zone)
                    continue
                corridor = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field="turn_corridor")
                if corridor:
                    lane_anchors[maneuver] = self._centroid_of_points(corridor)
                    continue
                movement_path = self._extract_maneuver_points(maneuver_cfg=maneuver_cfg, field="movement_path")
                if movement_path:
                    lane_anchors[maneuver] = movement_path[-1]
            if lane_anchors:
                anchors_by_lane[lane.lane_id] = lane_anchors
        return anchors_by_lane

    @staticmethod
    def _maneuver_is_enabled(maneuver_cfg) -> bool:
        if maneuver_cfg is None:
            return True
        if isinstance(maneuver_cfg, dict):
            return bool(maneuver_cfg.get("enabled", True))
        return bool(getattr(maneuver_cfg, "enabled", True))

    @staticmethod
    def _centroid_of_points(points: list[list[float]]) -> tuple[float, float]:
        if not points:
            return (0.0, 0.0)
        sx = sum(float(point[0]) for point in points)
        sy = sum(float(point[1]) for point in points)
        n = max(len(points), 1)
        return (sx / n, sy / n)

    @staticmethod
    def _line_midpoint(points: list[list[float]]) -> tuple[float, float]:
        if len(points) < 2:
            return ViolationLogic._centroid_of_points(points)
        start = points[0]
        end = points[1]
        return ((float(start[0]) + float(end[0])) / 2.0, (float(start[1]) + float(end[1])) / 2.0)

    @staticmethod
    def _normalize_vector(vector: tuple[float, float]) -> Optional[tuple[float, float]]:
        vx, vy = float(vector[0]), float(vector[1])
        mag = hypot(vx, vy)
        if mag <= 1e-6:
            return None
        return (vx / mag, vy / mag)

    @staticmethod
    def _sign_of_value(value: float, *, tolerance: float = 1e-5) -> int:
        if abs(value) <= tolerance:
            return 0
        return 1 if value > 0 else -1

    def get_recent_trajectories(
        self,
        *,
        limit: int = 30,
        lane_id: Optional[int] = None,
        vehicle_type: Optional[str] = None,
        min_points: int = 3,
    ) -> list[dict]:
        rows: list[dict] = []
        for state in self._vehicle_states.values():
            if lane_id is not None and state.current_stable_lane_id != lane_id:
                continue
            if vehicle_type and state.vehicle_type != vehicle_type:
                continue
            points = [[float(sample.center[0]), float(sample.center[1])] for sample in state.trajectory]
            if len(points) < max(int(min_points), 2):
                continue
            rows.append(
                {
                    "vehicle_id": state.vehicle_id,
                    "vehicle_type": state.vehicle_type,
                    "lane_id": state.current_stable_lane_id,
                    "last_seen_ts": state.last_seen_ts.isoformat() if state.last_seen_ts else None,
                    "points": points,
                    "turn_phase": state.turn_state.phase,
                    "turn_source_lane_id": state.turn_state.source_lane_id,
                    "turn_confirmed_maneuver": state.turn_state.confirmed_maneuver,
                    "turn_reject_reasons": dict(state.turn_state.last_reject_reasons),
                }
            )
        rows.sort(key=lambda row: row.get("last_seen_ts") or "", reverse=True)
        return rows[: max(int(limit), 1)]

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        to_delete: list[int] = []
        for vid, st in self._vehicle_states.items():
            if st.last_seen_ts is None:
                continue

            while st.lane_history and st.lane_history[0][0].timestamp() < cutoff_ts:
                st.lane_history.popleft()
            while st.trajectory and st.trajectory[0].ts.timestamp() < cutoff_ts:
                st.trajectory.popleft()

            stale_lifecycle_keys = [
                key
                for key, lifecycle in st.violation_lifecycles.items()
                if lifecycle.last_seen_ts is not None and lifecycle.last_seen_ts.timestamp() < cutoff_ts
            ]
            for key in stale_lifecycle_keys:
                del st.violation_lifecycles[key]

            if st.last_seen_ts.timestamp() < cutoff_ts:
                to_delete.append(vid)
        for vid in to_delete:
            del self._vehicle_states[vid]
