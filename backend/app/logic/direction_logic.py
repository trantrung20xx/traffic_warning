from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import hypot, sqrt
from typing import Optional, Sequence

from app.core.config import LanePolygon
from app.logic.polygon import PreparedPolygon

DIRECTION_STATUS_CORRECT = "correct_direction"
DIRECTION_STATUS_WRONG = "wrong_direction"
DIRECTION_STATUS_UNKNOWN = "unknown"
DIRECTION_STATUS_NOT_CONFIGURED = "not_configured"

_DIRECTION_SIGN_FORWARD = 1
_DIRECTION_SIGN_REVERSE = -1


@dataclass
class DirectionEvaluation:
    status: str
    dot: Optional[float]
    should_emit_violation: bool


@dataclass
class DirectionState:
    candidate_lane_id: Optional[int] = None
    candidate_state_sign: Optional[int] = None
    candidate_started_ts: Optional[datetime] = None
    last_status: str = DIRECTION_STATUS_NOT_CONFIGURED
    last_dot: Optional[float] = None
    last_seen_ts: Optional[datetime] = None


@dataclass
class DirectionRule:
    lane_id: int
    reference_path: list[tuple[float, float]]
    check_shape: PreparedPolygon
    source: str  # direction_path | auto_centerline


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


@dataclass(frozen=True)
class _PathProjection:
    distance_px: float
    progress: float
    tangent_forward: tuple[float, float]


@dataclass(frozen=True)
class _DirectionObservation:
    heading_dot: float
    progress_delta: float
    distance_px: float


@dataclass(frozen=True)
class _DirectionMatchResult:
    state_sign: int
    forward_dot: float
    confidence_margin: float


class DirectionLogic:
    """
    Runtime logic phát hiện xe đi đúng/ngược chiều theo từng lane.

    Khi thiếu direction_path, hệ thống tự sinh centerline từ lane polygon và
    dùng map-matching + Viterbi 2 trạng thái (forward/reverse) để giữ ổn định.
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        settings: Optional[DirectionDetectionSettings] = None,
    ):
        self._settings = settings or DirectionDetectionSettings.from_values()
        self._rules_by_lane: dict[int, DirectionRule] = {}

        # Weights/tuning for centerline map matching.
        self._distance_scale_px = 30.0
        self._progress_scale = 0.04
        self._weight_heading = 1.0
        self._weight_progress = 0.7
        self._weight_distance = 0.45
        self._viterbi_transition_penalty = 0.35
        self._viterbi_confidence_margin_min = 0.25
        self._viterbi_max_samples = max(int(self._settings.min_samples) + 1, 4)

        for lane in lane_polygons:
            raw_rule = getattr(lane, "direction_rule", None)
            if raw_rule is None or not bool(raw_rule.enabled):
                continue

            raw_direction_path = list(raw_rule.direction_path or [])
            reference_path = self._sanitize_path(raw_direction_path)
            source = "direction_path"

            if len(reference_path) < 2:
                reference_path = self._build_centerline_reference_path(lane=lane)
                source = "auto_centerline"

            if len(reference_path) < 2:
                continue

            check_zone_points = raw_rule.check_zone if raw_rule.check_zone else lane.polygon
            if not check_zone_points or len(check_zone_points) < 3:
                continue

            self._rules_by_lane[lane.lane_id] = DirectionRule(
                lane_id=lane.lane_id,
                reference_path=reference_path,
                check_shape=PreparedPolygon.from_points(check_zone_points),
                source=source,
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

        match = self._map_match_direction(rule=rule, lane_samples=lane_samples)
        if match is None:
            return self._unknown_result(state, reset_candidate=True)

        dot = float(match.forward_dot)
        confident = match.confidence_margin >= self._viterbi_confidence_margin_min

        if dot >= self._settings.same_direction_cos_threshold:
            self._reset_candidate(state)
            return self._set_state_result(state, DIRECTION_STATUS_CORRECT, dot, should_emit_violation=False)

        if (
            confident
            and match.state_sign == _DIRECTION_SIGN_REVERSE
            and dot <= self._settings.opposite_direction_cos_threshold
        ):
            if (
                state.candidate_lane_id != lane_id
                or state.candidate_started_ts is None
                or state.candidate_state_sign != _DIRECTION_SIGN_REVERSE
            ):
                state.candidate_lane_id = lane_id
                state.candidate_state_sign = _DIRECTION_SIGN_REVERSE
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
        state.candidate_state_sign = None
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

    @staticmethod
    def _sanitize_path(points: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        cleaned: list[tuple[float, float]] = []
        for raw in points:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            point = (float(raw[0]), float(raw[1]))
            if cleaned and hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) <= 1e-6:
                continue
            cleaned.append(point)
        return cleaned if len(cleaned) >= 2 else []

    def _build_centerline_reference_path(self, *, lane: LanePolygon) -> list[tuple[float, float]]:
        points = self._sanitize_path(list(lane.polygon or []))
        if len(points) < 3:
            return []
        commit_anchor = self._lane_commit_anchor(lane=lane)

        center_x = sum(point[0] for point in points) / len(points)
        center_y = sum(point[1] for point in points) / len(points)

        cov_xx = 0.0
        cov_xy = 0.0
        cov_yy = 0.0
        for px, py in points:
            dx = px - center_x
            dy = py - center_y
            cov_xx += dx * dx
            cov_xy += dx * dy
            cov_yy += dy * dy
        cov_xx /= len(points)
        cov_xy /= len(points)
        cov_yy /= len(points)

        trace = cov_xx + cov_yy
        det = (cov_xx * cov_yy) - (cov_xy * cov_xy)
        disc = max((trace * trace * 0.25) - det, 0.0)
        lambda_major = (trace * 0.5) + sqrt(disc)

        if abs(cov_xy) > 1e-9:
            axis = (lambda_major - cov_yy, cov_xy)
        elif cov_xx >= cov_yy:
            axis = (1.0, 0.0)
        else:
            axis = (0.0, 1.0)

        axis = self._normalize_vector(axis)
        if axis is None:
            return []

        if commit_anchor is not None:
            preferred_vec = self._normalize_vector((commit_anchor[0] - center_x, commit_anchor[1] - center_y))
            if preferred_vec is not None:
                alt_axis = (-axis[1], axis[0])
                align_axis = abs((axis[0] * preferred_vec[0]) + (axis[1] * preferred_vec[1]))
                align_alt = abs((alt_axis[0] * preferred_vec[0]) + (alt_axis[1] * preferred_vec[1]))
                if align_alt > align_axis:
                    axis = alt_axis

        projections = [((px - center_x) * axis[0]) + ((py - center_y) * axis[1]) for px, py in points]
        t_min = min(projections)
        t_max = max(projections)
        if (t_max - t_min) <= 1e-6:
            return []

        start = (center_x + (axis[0] * t_min), center_y + (axis[1] * t_min))
        end = (center_x + (axis[0] * t_max), center_y + (axis[1] * t_max))
        start, end = self._orient_reference_line(start=start, end=end, lane=lane)
        return [start, end]

    def _orient_reference_line(
        self,
        *,
        start: tuple[float, float],
        end: tuple[float, float],
        lane: LanePolygon,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        commit_anchor = self._lane_commit_anchor(lane=lane)
        if commit_anchor is not None:
            t = self._projection_factor_to_segment(point=commit_anchor, start=start, end=end)
            # Keep "forward" pointing toward the endpoint near commit.
            if t < 0.5:
                return (end, start)
            return (start, end)

        dx = end[0] - start[0]
        dy = end[1] - start[1]
        if abs(dy) >= abs(dx):
            return (start, end) if dy >= 0.0 else (end, start)
        return (start, end) if dx >= 0.0 else (end, start)

    def _lane_commit_anchor(self, *, lane: LanePolygon) -> Optional[tuple[float, float]]:
        if lane.commit_gate and len(lane.commit_gate) >= 3:
            return self._centroid_of_points(lane.commit_gate)
        if lane.commit_line and len(lane.commit_line) >= 2:
            return self._line_midpoint(lane.commit_line)
        return None

    def _map_match_direction(
        self,
        *,
        rule: DirectionRule,
        lane_samples: list[tuple[datetime, tuple[float, float]]],
    ) -> Optional[_DirectionMatchResult]:
        recent_samples = lane_samples[-self._viterbi_max_samples :]
        if len(recent_samples) < 2:
            return None

        observations: list[_DirectionObservation] = []
        prev_projection: Optional[_PathProjection] = None
        for index in range(1, len(recent_samples)):
            prev_point = recent_samples[index - 1][1]
            curr_point = recent_samples[index][1]
            vehicle_vec = self._normalize_vector((curr_point[0] - prev_point[0], curr_point[1] - prev_point[1]))
            if vehicle_vec is None:
                continue

            curr_projection = self._project_to_path(point=curr_point, path=rule.reference_path)
            if curr_projection is None:
                continue
            if prev_projection is None:
                prev_projection = self._project_to_path(point=prev_point, path=rule.reference_path)
                if prev_projection is None:
                    prev_projection = curr_projection

            heading_dot = (vehicle_vec[0] * curr_projection.tangent_forward[0]) + (
                vehicle_vec[1] * curr_projection.tangent_forward[1]
            )
            progress_delta = curr_projection.progress - prev_projection.progress
            distance_px = (prev_projection.distance_px + curr_projection.distance_px) * 0.5
            observations.append(
                _DirectionObservation(
                    heading_dot=float(heading_dot),
                    progress_delta=float(progress_delta),
                    distance_px=float(distance_px),
                )
            )
            prev_projection = curr_projection

        if not observations:
            return None

        forward_score = self._viterbi_score(observations=observations, initial_sign=_DIRECTION_SIGN_FORWARD)
        reverse_score = self._viterbi_score(observations=observations, initial_sign=_DIRECTION_SIGN_REVERSE)

        if forward_score >= reverse_score:
            best_sign = _DIRECTION_SIGN_FORWARD
            confidence_margin = forward_score - reverse_score
        else:
            best_sign = _DIRECTION_SIGN_REVERSE
            confidence_margin = reverse_score - forward_score

        forward_dot = self._average_signed_dot(observations=observations, sign=_DIRECTION_SIGN_FORWARD)
        return _DirectionMatchResult(
            state_sign=best_sign,
            forward_dot=forward_dot,
            confidence_margin=float(confidence_margin),
        )

    def _viterbi_score(self, *, observations: list[_DirectionObservation], initial_sign: int) -> float:
        states = (_DIRECTION_SIGN_FORWARD, _DIRECTION_SIGN_REVERSE)
        scores = {state_sign: float("-inf") for state_sign in states}
        scores[initial_sign] = self._emission_score(observation=observations[0], sign=initial_sign)
        scores[-initial_sign] = self._emission_score(observation=observations[0], sign=-initial_sign) - self._viterbi_transition_penalty

        for observation in observations[1:]:
            next_scores = {state_sign: float("-inf") for state_sign in states}
            for next_sign in states:
                emit = self._emission_score(observation=observation, sign=next_sign)
                best = float("-inf")
                for prev_sign in states:
                    transition = 0.0 if prev_sign == next_sign else -self._viterbi_transition_penalty
                    best = max(best, scores[prev_sign] + transition)
                next_scores[next_sign] = best + emit
            scores = next_scores
        return max(scores.values())

    def _emission_score(self, *, observation: _DirectionObservation, sign: int) -> float:
        heading_term = max(min(sign * observation.heading_dot, 1.0), -1.0)
        progress_term = max(
            min((sign * observation.progress_delta) / max(self._progress_scale, 1e-6), 1.0),
            -1.0,
        )
        distance_penalty = min(observation.distance_px / max(self._distance_scale_px, 1e-6), 3.0)
        return (
            (self._weight_heading * heading_term)
            + (self._weight_progress * progress_term)
            - (self._weight_distance * distance_penalty)
        )

    @staticmethod
    def _average_signed_dot(*, observations: list[_DirectionObservation], sign: int) -> float:
        if not observations:
            return 0.0
        total = sum(max(min(sign * obs.heading_dot, 1.0), -1.0) for obs in observations)
        return float(total / len(observations))

    def _project_to_path(
        self,
        *,
        point: tuple[float, float],
        path: list[tuple[float, float]],
    ) -> Optional[_PathProjection]:
        if len(path) < 2:
            return None

        cumulative_length = 0.0
        best_distance: Optional[float] = None
        best_progress = 0.0
        best_tangent: Optional[tuple[float, float]] = None
        total_length = 0.0

        for index in range(len(path) - 1):
            start = path[index]
            end = path[index + 1]
            seg_vec = (end[0] - start[0], end[1] - start[1])
            seg_length = hypot(seg_vec[0], seg_vec[1])
            total_length += seg_length

            tangent = self._normalize_vector(seg_vec)
            if tangent is None or seg_length <= 1e-9:
                continue

            t = self._projection_factor_to_segment(point=point, start=start, end=end)
            closest_x = start[0] + (seg_vec[0] * t)
            closest_y = start[1] + (seg_vec[1] * t)
            distance = hypot(point[0] - closest_x, point[1] - closest_y)
            progress = cumulative_length + (seg_length * t)

            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_progress = progress
                best_tangent = tangent
            cumulative_length += seg_length

        if best_distance is None or best_tangent is None:
            return None

        normalized_progress = best_progress / max(total_length, 1e-6)
        return _PathProjection(
            distance_px=float(best_distance),
            progress=float(normalized_progress),
            tangent_forward=best_tangent,
        )

    @staticmethod
    def _projection_factor_to_segment(
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
            return 0.0

        t = ((px - x1) * dx + (py - y1) * dy) / length_sq
        return max(0.0, min(1.0, t))

    @staticmethod
    def _centroid_of_points(points: list[list[float]]) -> tuple[float, float]:
        sx = sum(float(point[0]) for point in points)
        sy = sum(float(point[1]) for point in points)
        size = max(len(points), 1)
        return (sx / size, sy / size)

    @staticmethod
    def _line_midpoint(points: list[list[float]]) -> tuple[float, float]:
        start = points[0]
        end = points[1]
        return ((float(start[0]) + float(end[0])) / 2.0, (float(start[1]) + float(end[1])) / 2.0)
