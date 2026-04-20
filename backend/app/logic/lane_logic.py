from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import bbox_bottom_center, point_in_polygon


@dataclass(frozen=True)
class LaneMatch:
    lane_id: int


@dataclass
class LaneHistoryState:
    stable_lane_id: Optional[int] = None
    pending_lane_id: Optional[int] = None
    pending_started_ts: Optional[datetime] = None
    recent_observations: deque[tuple[datetime, Optional[int]]] = field(default_factory=deque)
    last_seen_ts: Optional[datetime] = None


class LaneLogic:
    """
    Gán `lane_id` bằng polygon vẽ tay.
    Phần này không dùng AI nhận diện làn đường.
    """

    def __init__(self, lane_polygons: list[LanePolygon]):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_polygons = {lp.lane_id: lp for lp in lane_polygons}
        self._lane_order = [lp.lane_id for lp in lane_polygons]

    def assign_lane_id_from_bbox_xyxy(self, bbox_xyxy: list[float] | tuple[float, ...]) -> Optional[int]:
        """Xác định làn theo điểm giữa cạnh đáy của bounding box phương tiện."""
        px, py = bbox_bottom_center(bbox_xyxy)

        matches: list[int] = []
        for lane_id in self._lane_order:
            lp = self._lane_polygons[lane_id]
            if point_in_polygon(px, py, lp.polygon):
                matches.append(lane_id)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Nếu polygon các làn chồng lấn do cấu hình sai thì tạm lấy làn xuất hiện trước.
            return matches[0]
        return None


class TemporalLaneAssigner:
    """
    Làm mượt kết quả gán làn theo từng frame trước khi đưa vào pipeline chính.
    Camera giao thông là camera cố định nên dùng cửa sổ đa số kèm hysteresis sẽ ổn định hơn
    so với phản ứng ngay theo một frame nhiễu.
    """

    def __init__(
        self,
        *,
        observation_window_ms: int = 1200,
        min_majority_hits: int = 3,
        switch_min_duration_ms: int = 700,
    ):
        self._observation_window_ms = int(observation_window_ms)
        self._min_majority_hits = int(min_majority_hits)
        self._switch_min_duration_ms = int(switch_min_duration_ms)
        self._vehicle_states: dict[int, LaneHistoryState] = {}

    def resolve_lane(self, *, vehicle_id: int, raw_lane_id: Optional[int], ts: datetime) -> Optional[int]:
        """Trả về làn ổn định cho một xe tại thời điểm hiện tại."""
        st = self._vehicle_states.get(vehicle_id)
        if st is None:
            st = LaneHistoryState()
            self._vehicle_states[vehicle_id] = st

        st.last_seen_ts = ts
        st.recent_observations.append((ts, raw_lane_id))
        self._prune_history(st, ts)

        counts = Counter(lane_id for _, lane_id in st.recent_observations if lane_id is not None)
        if not counts:
            return st.stable_lane_id

        majority_lane_id, majority_hits = counts.most_common(1)[0]

        if st.stable_lane_id is None:
            if majority_hits >= self._min_majority_hits:
                st.stable_lane_id = majority_lane_id
            return st.stable_lane_id

        if majority_lane_id == st.stable_lane_id:
            st.pending_lane_id = None
            st.pending_started_ts = None
            return st.stable_lane_id

        stable_hits = counts.get(st.stable_lane_id, 0)
        if majority_hits <= stable_hits:
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
        if duration_ms >= self._switch_min_duration_ms and majority_hits >= self._min_majority_hits:
            st.stable_lane_id = majority_lane_id
            st.pending_lane_id = None
            st.pending_started_ts = None

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

