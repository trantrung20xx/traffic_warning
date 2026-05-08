from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from math import isclose
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import PreparedPolygon, bbox_bottom_center, bbox_bottom_contact_points


@dataclass(slots=True)
class LaneScore:
    overlap_length: float
    overlap_ratio: float
    center_inside: bool
    left_contact_inside: bool
    right_contact_inside: bool
    confidence: float


@dataclass(slots=True)
class LaneObservation:
    raw_lane_id: Optional[int]
    confidence: float
    overlap_ratio: float
    center_inside: bool
    left_contact_inside: bool
    right_contact_inside: bool
    lane_scores: dict[int, LaneScore] = field(default_factory=dict)

    def confidence_for_lane(self, lane_id: int) -> float:
        score = self.lane_scores.get(lane_id)
        if score is not None:
            return float(score.confidence)
        if self.raw_lane_id == lane_id:
            return float(self.confidence)
        return 0.0


@dataclass
class LaneHistoryState:
    stable_lane_id: Optional[int] = None
    pending_lane_id: Optional[int] = None
    pending_started_ts: Optional[datetime] = None
    recent_observations: deque[tuple[datetime, LaneObservation]] = field(default_factory=deque)
    last_seen_ts: Optional[datetime] = None


class LaneLogic:
    """
    Gán `lane_id` bằng polygon vẽ tay.
    Phần này không dùng AI nhận diện làn đường.
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        preferred_lane_overlap_ratio: float = 0.8,
        preferred_lane_overlap_margin_px: float = 6.0,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_order = [lp.lane_id for lp in lane_polygons]
        self._lane_shapes = {lp.lane_id: PreparedPolygon.from_points(lp.polygon) for lp in lane_polygons}
        self._preferred_lane_overlap_ratio = float(preferred_lane_overlap_ratio)
        self._preferred_lane_overlap_margin_px = float(preferred_lane_overlap_margin_px)

    def assign_lane_id_from_bbox_xyxy(
        self,
        bbox_xyxy: list[float] | tuple[float, ...],
        *,
        preferred_lane_id: Optional[int] = None,
    ) -> Optional[int]:
        observation = self.observe_lane_from_bbox_xyxy(
            bbox_xyxy,
            preferred_lane_id=preferred_lane_id,
        )
        return observation.raw_lane_id

    def observe_lane_from_bbox_xyxy(
        self,
        bbox_xyxy: list[float] | tuple[float, ...],
        *,
        preferred_lane_id: Optional[int] = None,
    ) -> LaneObservation:
        """
        Trả về quan sát lane chi tiết cho mỗi frame.

        Quan sát này dùng cho hai mục đích:
        - ổn định lane theo thời gian (confidence-aware switch)
        - phát hiện sai làn với bằng chứng mạnh hơn chỉ lane_id.
        """
        px, py = bbox_bottom_center(bbox_xyxy)
        left_point, _, right_point = bbox_bottom_contact_points(bbox_xyxy)

        bottom_segment_len = max(abs(float(right_point[0]) - float(left_point[0])), 1e-6)
        lane_scores: dict[int, LaneScore] = {}
        overlap_scores: list[tuple[float, int, bool]] = []
        for lane_id in self._lane_order:
            shape = self._lane_shapes[lane_id]
            overlap_length = float(shape.segment_overlap_length(left_point, right_point))
            overlap_ratio = min(max(overlap_length / bottom_segment_len, 0.0), 1.0)
            center_inside = bool(shape.contains_xy(px, py))
            left_inside = bool(shape.contains_xy(left_point[0], left_point[1]))
            right_inside = bool(shape.contains_xy(right_point[0], right_point[1]))
            confidence = min(
                1.0,
                overlap_ratio
                + (0.18 if center_inside else 0.0)
                + (0.10 if left_inside else 0.0)
                + (0.10 if right_inside else 0.0),
            )
            lane_scores[lane_id] = LaneScore(
                overlap_length=overlap_length,
                overlap_ratio=overlap_ratio,
                center_inside=center_inside,
                left_contact_inside=left_inside,
                right_contact_inside=right_inside,
                confidence=confidence,
            )
            overlap_scores.append((overlap_length, lane_id, center_inside))

        raw_lane_id = self._select_raw_lane_id(
            preferred_lane_id=preferred_lane_id,
            overlap_scores=overlap_scores,
            lane_scores=lane_scores,
            center_x=px,
            center_y=py,
        )
        selected = lane_scores.get(raw_lane_id) if raw_lane_id is not None else None
        return LaneObservation(
            raw_lane_id=raw_lane_id,
            confidence=float(selected.confidence if selected is not None else 0.0),
            overlap_ratio=float(selected.overlap_ratio if selected is not None else 0.0),
            center_inside=bool(selected.center_inside if selected is not None else False),
            left_contact_inside=bool(selected.left_contact_inside if selected is not None else False),
            right_contact_inside=bool(selected.right_contact_inside if selected is not None else False),
            lane_scores=lane_scores,
        )

    def _select_raw_lane_id(
        self,
        *,
        preferred_lane_id: Optional[int],
        overlap_scores: list[tuple[float, int, bool]],
        lane_scores: dict[int, LaneScore],
        center_x: float,
        center_y: float,
    ) -> Optional[int]:
        best_overlap = max((score for score, _, _ in overlap_scores), default=0.0)
        if best_overlap > 0.0:
            if preferred_lane_id is not None:
                preferred_tuple = next(
                    (item for item in overlap_scores if item[1] == preferred_lane_id),
                    None,
                )
                if preferred_tuple is not None:
                    preferred_overlap, _, preferred_center_inside = preferred_tuple
                    if preferred_overlap > 0.0:
                        if preferred_overlap >= (best_overlap * self._preferred_lane_overlap_ratio):
                            return preferred_lane_id
                        if (
                            preferred_center_inside
                            and (best_overlap - preferred_overlap) <= self._preferred_lane_overlap_margin_px
                        ):
                            return preferred_lane_id

            overlap_matches = [
                lane_id
                for score, lane_id, _ in overlap_scores
                if isclose(score, best_overlap, rel_tol=1e-9, abs_tol=1e-9)
            ]
            if len(overlap_matches) == 1:
                return overlap_matches[0]
            if preferred_lane_id in overlap_matches:
                return preferred_lane_id

            center_overlap_matches = [
                lane_id
                for score, lane_id, center_inside in overlap_scores
                if center_inside and isclose(score, best_overlap, rel_tol=1e-9, abs_tol=1e-9)
            ]
            if len(center_overlap_matches) == 1:
                return center_overlap_matches[0]

            # Trường hợp còn hòa điểm, ưu tiên confidence cao hơn.
            best_by_conf = max(overlap_matches, key=lambda lane_id: lane_scores[lane_id].confidence, default=None)
            if best_by_conf is not None:
                return best_by_conf

        matches: list[int] = []
        for lane_id in self._lane_order:
            shape = self._lane_shapes[lane_id]
            if shape.contains_xy(center_x, center_y):
                matches.append(lane_id)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            if preferred_lane_id in matches:
                return preferred_lane_id
            # Nếu xe chưa có làn ổn định mà polygon bị chồng lấn thì lấy làn xuất hiện trước.
            return matches[0]
        return None


class TemporalLaneAssigner:
    """
    Làm mượt kết quả gán làn theo từng frame trước khi đưa vào pipeline chính.
    Camera giao thông là camera cố định nên dùng cửa sổ đa số + confidence + hysteresis
    để hạn chế lane drift do bbox rung.
    """

    def __init__(
        self,
        *,
        observation_window_ms: int = 1200,
        min_majority_hits: int = 3,
        switch_min_duration_ms: int = 700,
        switch_majority_ratio_min: float = 0.68,
        switch_score_margin_ratio: float = 0.16,
        switch_target_min_confidence: float = 0.62,
        switch_source_max_confidence: float = 0.48,
        switch_min_consecutive_target_frames: int = 3,
    ):
        self._observation_window_ms = int(observation_window_ms)
        self._min_majority_hits = int(min_majority_hits)
        self._switch_min_duration_ms = int(switch_min_duration_ms)
        self._switch_majority_ratio_min = min(max(float(switch_majority_ratio_min), 0.0), 1.0)
        self._switch_score_margin_ratio = max(float(switch_score_margin_ratio), 0.0)
        self._switch_target_min_confidence = min(max(float(switch_target_min_confidence), 0.0), 1.0)
        self._switch_source_max_confidence = min(max(float(switch_source_max_confidence), 0.0), 1.0)
        self._switch_min_consecutive_target_frames = max(int(switch_min_consecutive_target_frames), 1)
        self._vehicle_states: dict[int, LaneHistoryState] = {}

    def resolve_lane(
        self,
        *,
        vehicle_id: int,
        ts: datetime,
        raw_lane_id: Optional[int] = None,
        observation: Optional[LaneObservation] = None,
    ) -> Optional[int]:
        """Trả về làn ổn định cho một xe tại thời điểm hiện tại."""
        st = self._vehicle_states.get(vehicle_id)
        if st is None:
            st = LaneHistoryState()
            self._vehicle_states[vehicle_id] = st

        obs = observation if observation is not None else self._synthetic_observation(raw_lane_id=raw_lane_id)

        st.last_seen_ts = ts
        st.recent_observations.append((ts, obs))
        self._prune_history(st, ts)

        lane_weight: defaultdict[int, float] = defaultdict(float)
        lane_hits: Counter[int] = Counter()
        lane_conf_sum: defaultdict[int, float] = defaultdict(float)
        for _, item in st.recent_observations:
            lane_id = item.raw_lane_id
            if lane_id is None:
                continue
            conf = item.confidence_for_lane(lane_id)
            if conf <= 0.0:
                continue
            lane_weight[lane_id] += conf
            lane_hits[lane_id] += 1
            lane_conf_sum[lane_id] += conf

        if not lane_weight:
            return st.stable_lane_id

        majority_lane_id, majority_weight = max(
            lane_weight.items(),
            key=lambda row: (row[1], lane_hits.get(row[0], 0)),
        )
        majority_hits = lane_hits.get(majority_lane_id, 0)
        total_weight = sum(lane_weight.values())
        majority_ratio = (majority_weight / total_weight) if total_weight > 1e-6 else 0.0
        majority_avg_conf = (lane_conf_sum[majority_lane_id] / majority_hits) if majority_hits > 0 else 0.0

        if st.stable_lane_id is None:
            if majority_hits >= self._min_majority_hits:
                st.stable_lane_id = majority_lane_id
            return st.stable_lane_id

        if majority_lane_id == st.stable_lane_id:
            st.pending_lane_id = None
            st.pending_started_ts = None
            return st.stable_lane_id

        stable_lane_id = st.stable_lane_id
        stable_weight = lane_weight.get(stable_lane_id, 0.0)
        stable_hits = lane_hits.get(stable_lane_id, 0)
        stable_avg_conf = (lane_conf_sum[stable_lane_id] / stable_hits) if stable_hits > 0 else 0.0
        required_margin = self._switch_score_margin_ratio * max(total_weight, 1e-6)
        target_consecutive_ok = self._has_consecutive_target_hits(
            observations=st.recent_observations,
            target_lane_id=majority_lane_id,
            min_consecutive=self._switch_min_consecutive_target_frames,
            min_confidence=self._switch_target_min_confidence,
        )
        target_wins_clearly = majority_weight >= (stable_weight + required_margin)
        source_is_weak = (
            stable_avg_conf <= self._switch_source_max_confidence
            or majority_ratio >= 0.82
            or (stable_weight <= 1e-6)
            or (majority_weight >= (stable_weight * 1.65))
        )
        majority_ready = (
            majority_hits >= self._min_majority_hits
            and majority_ratio >= self._switch_majority_ratio_min
            and majority_avg_conf >= self._switch_target_min_confidence
        )

        if not (majority_ready and target_wins_clearly and source_is_weak and target_consecutive_ok):
            st.pending_lane_id = None
            st.pending_started_ts = None
            return st.stable_lane_id

        if st.pending_lane_id != majority_lane_id:
            st.pending_lane_id = majority_lane_id
            st.pending_started_ts = ts
            return st.stable_lane_id

        if st.pending_started_ts is None:
            st.pending_started_ts = ts
            return st.stable_lane_id

        duration_ms = int((ts - st.pending_started_ts).total_seconds() * 1000.0)
        if duration_ms >= self._switch_min_duration_ms:
            st.stable_lane_id = majority_lane_id
            st.pending_lane_id = None
            st.pending_started_ts = None

        return st.stable_lane_id

    def get_stable_lane(self, *, vehicle_id: int) -> Optional[int]:
        st = self._vehicle_states.get(vehicle_id)
        if st is None:
            return None
        return st.stable_lane_id

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        stale_ids = [
            vehicle_id
            for vehicle_id, state in self._vehicle_states.items()
            if state.last_seen_ts is not None and state.last_seen_ts.timestamp() < cutoff_ts
        ]
        for vehicle_id in stale_ids:
            del self._vehicle_states[vehicle_id]

    def _prune_history(self, state: LaneHistoryState, current_ts: datetime) -> None:
        """Loại các quan sát cũ đã nằm ngoài cửa sổ bỏ phiếu."""
        cutoff_ts = current_ts.timestamp() - (self._observation_window_ms / 1000.0)
        while state.recent_observations and state.recent_observations[0][0].timestamp() < cutoff_ts:
            state.recent_observations.popleft()

    @staticmethod
    def _synthetic_observation(raw_lane_id: Optional[int]) -> LaneObservation:
        if raw_lane_id is None:
            return LaneObservation(
                raw_lane_id=None,
                confidence=0.0,
                overlap_ratio=0.0,
                center_inside=False,
                left_contact_inside=False,
                right_contact_inside=False,
                lane_scores={},
            )
        return LaneObservation(
            raw_lane_id=raw_lane_id,
            confidence=1.0,
            overlap_ratio=1.0,
            center_inside=True,
            left_contact_inside=True,
            right_contact_inside=True,
            lane_scores={
                raw_lane_id: LaneScore(
                    overlap_length=1.0,
                    overlap_ratio=1.0,
                    center_inside=True,
                    left_contact_inside=True,
                    right_contact_inside=True,
                    confidence=1.0,
                )
            },
        )

    @staticmethod
    def _has_consecutive_target_hits(
        *,
        observations: deque[tuple[datetime, LaneObservation]],
        target_lane_id: int,
        min_consecutive: int,
        min_confidence: float,
    ) -> bool:
        streak = 0
        for _, obs in reversed(observations):
            if obs.raw_lane_id != target_lane_id:
                break
            if obs.confidence_for_lane(target_lane_id) < min_confidence:
                break
            streak += 1
            if streak >= min_consecutive:
                return True
        return False
