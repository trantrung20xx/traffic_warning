from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import hypot
from typing import Optional, Sequence

from app.core.config import LanePolygon
from app.logic.polygon import PreparedPolygon

DIRECTION_STATUS_CORRECT = "correct_direction"
DIRECTION_STATUS_WRONG = "wrong_direction"
DIRECTION_STATUS_UNKNOWN = "unknown"
DIRECTION_STATUS_NOT_CONFIGURED = "not_configured"


@dataclass
class DirectionEvaluation:
    status: str
    dot: Optional[float]
    should_emit_violation: bool


@dataclass
class DirectionState:
    candidate_lane_id: Optional[int] = None
    candidate_started_ts: Optional[datetime] = None
    last_status: str = DIRECTION_STATUS_NOT_CONFIGURED
    last_dot: Optional[float] = None
    last_seen_ts: Optional[datetime] = None


@dataclass
class DirectionRule:
    lane_id: int
    direction_path: list[tuple[float, float]]
    check_shape: PreparedPolygon


@dataclass(frozen=True)
class DirectionDetectionSettings:
    same_direction_cos_threshold: float
    opposite_direction_cos_threshold: float
    min_duration_ms: int
    min_displacement_px: float
    min_samples: int

    @classmethod
    def from_values(
        cls,
        *,
        same_direction_cos_threshold: float = 0.25,
        opposite_direction_cos_threshold: float = -0.25,
        min_duration_ms: int = 600,
        min_displacement_px: float = 8.0,
        min_samples: int = 3,
    ) -> "DirectionDetectionSettings":
        same_threshold = float(same_direction_cos_threshold)
        opposite_threshold = float(opposite_direction_cos_threshold)
        if opposite_threshold >= same_threshold:
            raise ValueError("opposite_direction_cos_threshold must be smaller than same_direction_cos_threshold")
        return cls(
            same_direction_cos_threshold=same_threshold,
            opposite_direction_cos_threshold=opposite_threshold,
            min_duration_ms=max(int(min_duration_ms), 1),
            min_displacement_px=max(float(min_displacement_px), 0.1),
            min_samples=max(int(min_samples), 2),
        )


class DirectionLogic:
    """
    Runtime logic phát hiện xe đi đúng/ngược chiều theo từng lane.

    Thiết kế thành component riêng để không trộn với turn-detection rule hiện có.
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        settings: Optional[DirectionDetectionSettings] = None,
    ):
        self._settings = settings or DirectionDetectionSettings.from_values()
        self._rules_by_lane: dict[int, DirectionRule] = {}
        for lane in lane_polygons:
            raw_rule = getattr(lane, "direction_rule", None)
            if raw_rule is None or not bool(raw_rule.enabled):
                continue

            raw_direction_path = list(raw_rule.direction_path or [])
            if len(raw_direction_path) < 2:
                continue

            direction_path = [self._point_tuple(point) for point in raw_direction_path]
            check_zone_points = raw_rule.check_zone if raw_rule.check_zone else lane.polygon
            if not check_zone_points or len(check_zone_points) < 3:
                continue

            self._rules_by_lane[lane.lane_id] = DirectionRule(
                lane_id=lane.lane_id,
                direction_path=direction_path,
                check_shape=PreparedPolygon.from_points(check_zone_points),
            )

        self._states: dict[int, DirectionState] = {}

    def evaluate(
        self,
        *,
        vehicle_id: int,
        lane_id: Optional[int],
        lane_started_ts: Optional[datetime],
        trajectory_centers: Sequence[tuple[datetime, tuple[float, float]]],
        ts: datetime,
    ) -> DirectionEvaluation:
        state = self._states.get(vehicle_id)
        if state is None:
            state = DirectionState()
            self._states[vehicle_id] = state
        state.last_seen_ts = ts

        if lane_id is None:
            return self._unknown_result(state, reset_candidate=True)

        rule = self._rules_by_lane.get(lane_id)
        if rule is None:
            return self._not_configured_result(state)

        lane_samples = self._lane_samples_since(trajectory_centers=trajectory_centers, lane_started_ts=lane_started_ts)
        if len(lane_samples) < self._settings.min_samples:
            return self._unknown_result(state, reset_candidate=True)

        current_point = lane_samples[-1][1]
        if not rule.check_shape.contains_xy(current_point[0], current_point[1]):
            return self._unknown_result(state, reset_candidate=True)

        start_point = lane_samples[0][1]
        end_point = lane_samples[-1][1]
        displacement_px = hypot(end_point[0] - start_point[0], end_point[1] - start_point[1])
        if displacement_px < self._settings.min_displacement_px:
            return self._unknown_result(state, reset_candidate=True)

        vehicle_vector = self._normalize_vector(
            (
                end_point[0] - start_point[0],
                end_point[1] - start_point[1],
            )
        )
        if vehicle_vector is None:
            return self._unknown_result(state, reset_candidate=True)

        lane_vector = self._direction_vector_for_point(rule=rule, point=current_point)
        if lane_vector is None:
            return self._unknown_result(state, reset_candidate=True)

        dot = (vehicle_vector[0] * lane_vector[0]) + (vehicle_vector[1] * lane_vector[1])
        if dot >= self._settings.same_direction_cos_threshold:
            self._reset_candidate(state)
            return self._set_state_result(state, DIRECTION_STATUS_CORRECT, dot, should_emit_violation=False)

        if dot <= self._settings.opposite_direction_cos_threshold:
            if state.candidate_lane_id != lane_id or state.candidate_started_ts is None:
                state.candidate_lane_id = lane_id
                state.candidate_started_ts = ts
                return self._unknown_result(state, dot=dot, reset_candidate=False)

            elapsed_ms = int((ts - state.candidate_started_ts).total_seconds() * 1000.0)
            if elapsed_ms >= self._settings.min_duration_ms:
                return self._set_state_result(state, DIRECTION_STATUS_WRONG, dot, should_emit_violation=True)
            return self._unknown_result(state, dot=dot, reset_candidate=False)

        return self._unknown_result(state, dot=dot, reset_candidate=True)

    def status_for_vehicle(self, *, vehicle_id: int) -> tuple[str, Optional[float]]:
        state = self._states.get(vehicle_id)
        if state is None:
            return (DIRECTION_STATUS_NOT_CONFIGURED, None)
        return (state.last_status, state.last_dot)

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        stale_vehicle_ids = [
            vehicle_id
            for vehicle_id, state in self._states.items()
            if state.last_seen_ts is not None and state.last_seen_ts.timestamp() < cutoff_ts
        ]
        for vehicle_id in stale_vehicle_ids:
            del self._states[vehicle_id]

    @staticmethod
    def _set_state_result(
        state: DirectionState,
        status: str,
        dot: Optional[float],
        *,
        should_emit_violation: bool,
    ) -> DirectionEvaluation:
        state.last_status = status
        state.last_dot = dot
        return DirectionEvaluation(status=status, dot=dot, should_emit_violation=should_emit_violation)

    @staticmethod
    def _reset_candidate(state: DirectionState) -> None:
        state.candidate_lane_id = None
        state.candidate_started_ts = None

    @classmethod
    def _unknown_result(
        cls,
        state: DirectionState,
        *,
        dot: Optional[float] = None,
        reset_candidate: bool,
    ) -> DirectionEvaluation:
        if reset_candidate:
            cls._reset_candidate(state)
        return cls._set_state_result(state, DIRECTION_STATUS_UNKNOWN, dot, should_emit_violation=False)

    @classmethod
    def _not_configured_result(cls, state: DirectionState) -> DirectionEvaluation:
        cls._reset_candidate(state)
        return cls._set_state_result(state, DIRECTION_STATUS_NOT_CONFIGURED, None, should_emit_violation=False)

    @staticmethod
    def _lane_samples_since(
        *,
        trajectory_centers: Sequence[tuple[datetime, tuple[float, float]]],
        lane_started_ts: Optional[datetime],
    ) -> list[tuple[datetime, tuple[float, float]]]:
        if lane_started_ts is None:
            return list(trajectory_centers)
        return [sample for sample in trajectory_centers if sample[0] >= lane_started_ts]

    @staticmethod
    def _point_tuple(point: Sequence[float]) -> tuple[float, float]:
        return (float(point[0]), float(point[1]))

    @staticmethod
    def _normalize_vector(vector: tuple[float, float]) -> Optional[tuple[float, float]]:
        vx = float(vector[0])
        vy = float(vector[1])
        mag = hypot(vx, vy)
        if mag <= 1e-6:
            return None
        return (vx / mag, vy / mag)

    def _direction_vector_for_point(
        self,
        *,
        rule: DirectionRule,
        point: tuple[float, float],
    ) -> Optional[tuple[float, float]]:
        best_vector: Optional[tuple[float, float]] = None
        best_distance: Optional[float] = None

        for index in range(len(rule.direction_path) - 1):
            start = rule.direction_path[index]
            end = rule.direction_path[index + 1]
            direction = self._normalize_vector((end[0] - start[0], end[1] - start[1]))
            if direction is None:
                continue

            distance = self._distance_point_to_segment(point=point, start=start, end=end)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_vector = direction
        return best_vector

    @staticmethod
    def _distance_point_to_segment(
        *,
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        px, py = point
        x1, y1 = start
        x2, y2 = end

        dx = x2 - x1
        dy = y2 - y1
        length_sq = (dx * dx) + (dy * dy)
        if length_sq <= 1e-9:
            return hypot(px - x1, py - y1)

        t = ((px - x1) * dx + (py - y1) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        proj_x = x1 + (dx * t)
        proj_y = y1 + (dy * t)
        return hypot(px - proj_x, py - proj_y)
