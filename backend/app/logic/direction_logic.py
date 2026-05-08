from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import hypot
from typing import Callable, Optional, Sequence

from app.core.config import LanePolygon
from app.logic.polygon import PreparedPolygon

DIRECTION_STATUS_CORRECT = "correct_direction"
DIRECTION_STATUS_WRONG = "wrong_direction"
DIRECTION_STATUS_UNKNOWN = "unknown"
DIRECTION_STATUS_NOT_CONFIGURED = "not_configured"


@dataclass
class DirectionEvaluation:
    # Kết quả đánh giá hướng ở frame hiện tại.
    status: str
    # Dot(cos) giữa vector trajectory và vector hướng tham chiếu.
    dot: Optional[float]
    # Cờ đã đủ điều kiện emit vi phạm ngược chiều.
    should_emit_violation: bool


@dataclass
class DirectionState:
    # candidate_* dùng như state machine chống báo sai ngược chiều trong vài frame nhiễu.
    # lane_id đang ở trạng thái nghi ngờ opposite.
    candidate_lane_id: Optional[int] = None
    # Thời điểm bắt đầu cửa sổ candidate.
    candidate_started_ts: Optional[datetime] = None
    # Trạng thái cuối cùng đã publish.
    last_status: str = DIRECTION_STATUS_NOT_CONFIGURED
    # Dot cuối cùng tương ứng trạng thái.
    last_dot: Optional[float] = None
    # Lần cuối cập nhật state, phục vụ prune.
    last_seen_ts: Optional[datetime] = None


@dataclass
class DirectionRule:
    # Quy tắc hướng theo từng lane đã được tiền xử lý về hình học runtime.
    lane_id: int
    direction_path: list[tuple[float, float]]
    check_shape: PreparedPolygon
    excluded_shapes: tuple[PreparedPolygon, ...]
    lane_span_px: float


@dataclass(frozen=True)
class SegmentObservation:
    # Timestamp của mẫu segment (lấy theo điểm cuối).
    ts: datetime
    # Vector đơn vị thể hiện hướng dịch chuyển của xe trên segment.
    vector: tuple[float, float]
    # Trung điểm segment để nội suy vector lane gần nhất.
    midpoint: tuple[float, float]
    # Độ dài dịch chuyển pixel trên segment, dùng làm trọng số.
    displacement_px: float


@dataclass(frozen=True)
class DirectionDetectionSettings:
    # Ngưỡng cos để coi là cùng chiều.
    same_direction_cos_threshold: float
    # Ngưỡng cos để coi là ngược chiều.
    opposite_direction_cos_threshold: float
    # Thời gian candidate opposite phải duy trì trước khi emit vi phạm.
    min_duration_ms: int
    min_displacement_px: float
    min_samples: int
    evaluation_window_samples: int
    segment_min_displacement_px: float
    segment_max_gap_ms: int
    warmup_min_duration_ms: int
    warmup_min_samples: int
    opposite_consensus_min_segments: int
    opposite_consensus_ratio_min: float
    opposite_min_displacement_px: float
    opposite_min_displacement_lane_ratio: float
    lane_consensus_sample_window: int
    lane_consensus_min_samples: int
    lane_consensus_inlier_dot_min: float
    lane_consensus_blend_weight: float
    lane_consensus_alignment_min_dot: float
    lane_consensus_max_age_ms: int
    trajectory_blend_weight: float
    trajectory_blend_min_alignment_dot: float

    @classmethod
    def from_values(
        cls,
        *,
        same_direction_cos_threshold: float = 0.25,
        opposite_direction_cos_threshold: float = -0.45,
        min_duration_ms: int = 700,
        min_displacement_px: float = 7.0,
        min_samples: int = 3,
        evaluation_window_samples: int = 12,
        segment_min_displacement_px: float = 2.0,
        segment_max_gap_ms: int = 450,
        warmup_min_duration_ms: int = 0,
        warmup_min_samples: int = 3,
        opposite_consensus_min_segments: int = 2,
        opposite_consensus_ratio_min: float = 0.55,
        opposite_min_displacement_px: float = 10.0,
        opposite_min_displacement_lane_ratio: float = 0.10,
        lane_consensus_sample_window: int = 48,
        lane_consensus_min_samples: int = 6,
        lane_consensus_inlier_dot_min: float = 0.75,
        lane_consensus_blend_weight: float = 0.28,
        lane_consensus_alignment_min_dot: float = 0.20,
        lane_consensus_max_age_ms: int = 180000,
        trajectory_blend_weight: float = 0.16,
        trajectory_blend_min_alignment_dot: float = 0.50,
    ) -> "DirectionDetectionSettings":
        same_threshold = float(same_direction_cos_threshold)
        opposite_threshold = float(opposite_direction_cos_threshold)
        # Ngưỡng opposite phải nhỏ hơn ngưỡng same để có vùng "unknown" ở giữa.
        if opposite_threshold >= same_threshold:
            raise ValueError("opposite_direction_cos_threshold must be smaller than same_direction_cos_threshold")
        return cls(
            same_direction_cos_threshold=same_threshold,
            opposite_direction_cos_threshold=opposite_threshold,
            min_duration_ms=max(int(min_duration_ms), 1),
            min_displacement_px=max(float(min_displacement_px), 0.1),
            min_samples=max(int(min_samples), 2),
            evaluation_window_samples=max(int(evaluation_window_samples), 4),
            segment_min_displacement_px=max(float(segment_min_displacement_px), 0.1),
            segment_max_gap_ms=max(int(segment_max_gap_ms), 1),
            warmup_min_duration_ms=max(int(warmup_min_duration_ms), 0),
            warmup_min_samples=max(int(warmup_min_samples), 2),
            opposite_consensus_min_segments=max(int(opposite_consensus_min_segments), 2),
            opposite_consensus_ratio_min=min(max(float(opposite_consensus_ratio_min), 0.0), 1.0),
            opposite_min_displacement_px=max(float(opposite_min_displacement_px), 0.1),
            opposite_min_displacement_lane_ratio=max(float(opposite_min_displacement_lane_ratio), 0.0),
            lane_consensus_sample_window=max(int(lane_consensus_sample_window), 4),
            lane_consensus_min_samples=max(int(lane_consensus_min_samples), 2),
            lane_consensus_inlier_dot_min=min(max(float(lane_consensus_inlier_dot_min), -1.0), 1.0),
            lane_consensus_blend_weight=min(max(float(lane_consensus_blend_weight), 0.0), 1.0),
            lane_consensus_alignment_min_dot=min(max(float(lane_consensus_alignment_min_dot), -1.0), 1.0),
            lane_consensus_max_age_ms=max(int(lane_consensus_max_age_ms), 1000),
            trajectory_blend_weight=min(max(float(trajectory_blend_weight), 0.0), 1.0),
            trajectory_blend_min_alignment_dot=min(max(float(trajectory_blend_min_alignment_dot), -1.0), 1.0),
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
            # Bỏ lane không bật rule hướng để giảm chi phí runtime không cần thiết.
            raw_rule = getattr(lane, "direction_rule", None)
            if raw_rule is None or not bool(raw_rule.enabled):
                continue

            raw_direction_path = list(raw_rule.direction_path or [])
            # Cần ít nhất 1 đoạn thẳng (2 điểm) mới tạo được vector hướng.
            if len(raw_direction_path) < 2:
                continue

            direction_path = [self._point_tuple(point) for point in raw_direction_path]
            # Nếu không có check_zone riêng thì fallback dùng polygon lane.
            check_zone_points = raw_rule.check_zone if raw_rule.check_zone else lane.polygon
            if not check_zone_points or len(check_zone_points) < 3:
                continue

            excluded_shapes = self._build_lane_excluded_shapes(
                lane=lane,
            )

            self._rules_by_lane[lane.lane_id] = DirectionRule(
                lane_id=lane.lane_id,
                direction_path=direction_path,
                check_shape=PreparedPolygon.from_points(check_zone_points),
                excluded_shapes=excluded_shapes,
                # lane_span phục vụ ngưỡng displacement tỉ lệ theo kích thước lane.
                lane_span_px=self._lane_span_px(lane),
            )

        self._states: dict[int, DirectionState] = {}
        # Consensus theo lane học dần từ trajectory đúng chiều để ổn định vector tham chiếu.
        self._lane_consensus_vectors: dict[int, deque[tuple[datetime, tuple[float, float]]]] = {
            lane_id: deque(maxlen=self._settings.lane_consensus_sample_window)
            for lane_id in self._rules_by_lane.keys()
        }

    def evaluate(
        self,
        *,
        vehicle_id: int,
        lane_id: Optional[int],
        lane_started_ts: Optional[datetime],
        trajectory_centers: Sequence[tuple[datetime, tuple[float, float]]],
        ts: datetime,
    ) -> DirectionEvaluation:
        # Resolve state hiện tại theo vehicle_id.
        state = self._states.get(vehicle_id)
        if state is None:
            state = DirectionState()
            self._states[vehicle_id] = state
        state.last_seen_ts = ts

        if lane_id is None:
            # Chưa có lane ổn định thì chưa đủ ngữ cảnh để kết luận hướng.
            return self._unknown_result(state, reset_candidate=True)

        rule = self._rules_by_lane.get(lane_id)
        if rule is None:
            # Lane không cấu hình direction_rule thì trả "not_configured" thay vì suy đoán.
            return self._not_configured_result(state)

        # Chỉ lấy trajectory kể từ lúc vào lane hiện tại để tránh dính dữ liệu lane trước.
        lane_samples = self._lane_samples_since(trajectory_centers=trajectory_centers, lane_started_ts=lane_started_ts)
        # Cắt giữ đoạn liên tục gần nhất, bỏ phần có gap lớn do mất track/frame.
        lane_samples = self._tail_contiguous_samples(lane_samples=lane_samples)
        # Warmup sau khi đổi lane để tránh kết luận từ trajectory còn dính lane trước.
        if self._is_warmup_active(lane_started_ts=lane_started_ts, ts=ts):
            return self._unknown_result(state, reset_candidate=True)
        if len(lane_samples) < self._settings.warmup_min_samples:
            return self._unknown_result(state, reset_candidate=True)

        # Giới hạn cửa sổ đánh giá để tăng tính realtime và giảm kéo dài lịch sử cũ.
        lane_samples = lane_samples[-self._settings.evaluation_window_samples:]
        if len(lane_samples) < self._settings.min_samples:
            return self._unknown_result(state, reset_candidate=True)

        current_point = lane_samples[-1][1]
        # Điểm hiện tại phải nằm trong vùng evaluable thì mới xét hướng.
        if not self._is_point_evaluable(rule=rule, point=current_point):
            return self._unknown_result(state, reset_candidate=True)

        observations = self._segment_observations(rule=rule, lane_samples=lane_samples)
        # min_samples điểm tương ứng tối thiểu min_samples-1 segment hợp lệ.
        if len(observations) < max(self._settings.min_samples - 1, 2):
            return self._unknown_result(state, reset_candidate=True)

        total_displacement = sum(obs.displacement_px for obs in observations)
        # Nếu xe hầu như đứng yên thì vector hướng không đáng tin.
        if total_displacement < self._settings.min_displacement_px:
            return self._unknown_result(state, reset_candidate=True)

        # Vector quỹ đạo tổng hợp theo trọng số displacement để segment dài ảnh hưởng mạnh hơn.
        trajectory_vector = self._weighted_mean_vector(
            vectors=[obs.vector for obs in observations],
            weights=[obs.displacement_px for obs in observations],
        )
        if trajectory_vector is None:
            return self._unknown_result(state, reset_candidate=True)

        dots: list[float] = []
        dot_weights: list[float] = []
        # Các bộ đếm phục vụ cả consensus theo tỉ lệ và theo streak.
        same_hits = 0
        opposite_hits = 0
        same_consecutive = 0
        opposite_consecutive = 0
        max_same_consecutive = 0
        max_opposite_consecutive = 0
        opposite_displacement_px = 0.0

        for obs in observations:
            # Mỗi segment trajectory được so với segment direction_path gần nhất tại midpoint.
            lane_vector = self._direction_vector_for_point(rule=rule, point=obs.midpoint)
            if lane_vector is None:
                continue
            # Do cả hai vector đã normalize nên dot chính là cosine(angle).
            dot = (obs.vector[0] * lane_vector[0]) + (obs.vector[1] * lane_vector[1])
            dots.append(dot)
            dot_weights.append(obs.displacement_px)

            if dot >= self._settings.same_direction_cos_threshold:
                # Segment này ủng hộ cùng chiều.
                same_hits += 1
                same_consecutive += 1
                opposite_consecutive = 0
            elif dot <= self._settings.opposite_direction_cos_threshold:
                # Segment này ủng hộ ngược chiều.
                opposite_hits += 1
                opposite_consecutive += 1
                same_consecutive = 0
                # Cộng dồn displacement opposite để chặn false-positive từ dịch chuyển quá nhỏ.
                opposite_displacement_px += obs.displacement_px
            else:
                # Nằm vùng trung tính -> reset streak cả hai phía.
                same_consecutive = 0
                opposite_consecutive = 0

            if same_consecutive > max_same_consecutive:
                max_same_consecutive = same_consecutive
            if opposite_consecutive > max_opposite_consecutive:
                max_opposite_consecutive = opposite_consecutive

        if not dots:
            # Không có dot hợp lệ thì không thể đánh giá hướng.
            return self._unknown_result(state, reset_candidate=True)

        dot = self._weighted_average(values=dots, weights=dot_weights)
        # Tỉ lệ hit giúp giảm phụ thuộc vào một vài segment ngoại lệ.
        same_ratio = same_hits / len(dots)
        opposite_ratio = opposite_hits / len(dots)
        # Ngưỡng displacement opposite lấy max giữa ngưỡng tuyệt đối và ngưỡng theo kích thước lane.
        opposite_min_displacement_px = max(
            self._settings.opposite_min_displacement_px,
            rule.lane_span_px * self._settings.opposite_min_displacement_lane_ratio,
        )
        # Tail consensus chỉ nhìn dải segment cuối để bắt xu hướng hiện tại.
        tail_same_consecutive, tail_same_dot = self._tail_consensus(
            dots=dots,
            weights=dot_weights,
            comparator=lambda value: value >= self._settings.same_direction_cos_threshold,
        )
        tail_opposite_consecutive, tail_opposite_dot = self._tail_consensus(
            dots=dots,
            weights=dot_weights,
            comparator=lambda value: value <= self._settings.opposite_direction_cos_threshold,
        )
        tail_opposite_displacement_px = sum(dot_weights[-tail_opposite_consecutive:]) if tail_opposite_consecutive > 0 else 0.0

        # Same consensus cho phép qua 2 đường:
        # - trung bình toàn cửa sổ tốt
        # - hoặc tail gần nhất thể hiện đúng chiều rõ ràng.
        is_same_consensus = (
            (
                dot >= self._settings.same_direction_cos_threshold
                and same_ratio >= 0.55
                and max_same_consecutive >= 2
            )
            or (
                tail_same_consecutive >= 2
                and tail_same_dot is not None
                and tail_same_dot >= self._settings.same_direction_cos_threshold
            )
        )
        # Opposite consensus yêu cầu chặt hơn: tỉ lệ, streak, và đủ displacement thực.
        is_opposite_consensus = (
            tail_opposite_dot is not None
            and tail_opposite_dot <= self._settings.opposite_direction_cos_threshold
            and opposite_ratio >= self._settings.opposite_consensus_ratio_min
            and tail_opposite_consecutive >= self._settings.opposite_consensus_min_segments
            and opposite_displacement_px >= opposite_min_displacement_px
            and tail_opposite_displacement_px >= self._settings.segment_min_displacement_px
        )

        if is_same_consensus:
            # Đã xác định cùng chiều thì xóa candidate opposite tích lũy trước đó.
            self._reset_candidate(state)
            same_dot = dot
            if tail_same_dot is not None:
                # Ưu tiên độ mạnh từ đoạn cuối nếu tốt hơn trung bình toàn cửa sổ.
                same_dot = max(same_dot, tail_same_dot)
            return self._set_state_result(state, DIRECTION_STATUS_CORRECT, same_dot, should_emit_violation=False)

        if is_opposite_consensus:
            # Trạng thái opposite phải đi qua candidate window (min_duration_ms)
            # trước khi emit vi phạm.
            opposite_dot = dot
            if tail_opposite_dot is not None:
                opposite_dot = min(opposite_dot, tail_opposite_dot)
            if state.candidate_lane_id != lane_id or state.candidate_started_ts is None:
                # Bắt đầu (hoặc bắt đầu lại) phiên candidate opposite cho lane hiện tại.
                state.candidate_lane_id = lane_id
                state.candidate_started_ts = ts
                return self._unknown_result(state, dot=opposite_dot, reset_candidate=False)

            # Chỉ emit vi phạm khi candidate kéo dài đủ lâu.
            elapsed_ms = int((ts - state.candidate_started_ts).total_seconds() * 1000.0)
            if elapsed_ms >= self._settings.min_duration_ms:
                return self._set_state_result(state, DIRECTION_STATUS_WRONG, opposite_dot, should_emit_violation=True)
            return self._unknown_result(state, dot=opposite_dot, reset_candidate=False)

        return self._unknown_result(state, dot=dot, reset_candidate=True)

    def status_for_vehicle(self, *, vehicle_id: int) -> tuple[str, Optional[float]]:
        # API đọc trạng thái gần nhất cho overlay/debug.
        state = self._states.get(vehicle_id)
        if state is None:
            return (DIRECTION_STATUS_NOT_CONFIGURED, None)
        return (state.last_status, state.last_dot)

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        # Dọn state xe đã mất dấu quá lâu để tránh phình bộ nhớ runtime.
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
        # Ghi lại snapshot state để các lần gọi status_for_vehicle đọc được.
        state.last_status = status
        state.last_dot = dot
        return DirectionEvaluation(status=status, dot=dot, should_emit_violation=should_emit_violation)

    @staticmethod
    def _reset_candidate(state: DirectionState) -> None:
        # Xóa toàn bộ candidate opposite đang chờ xác nhận.
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
        # Unknown luôn không emit vi phạm.
        return cls._set_state_result(state, DIRECTION_STATUS_UNKNOWN, dot, should_emit_violation=False)

    @classmethod
    def _not_configured_result(cls, state: DirectionState) -> DirectionEvaluation:
        # Lane không cấu hình direction rule cũng không emit vi phạm.
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
        # Lọc theo mốc đổi lane để chỉ giữ quỹ đạo cùng lane hiện tại.
        return [sample for sample in trajectory_centers if sample[0] >= lane_started_ts]

    @staticmethod
    def _point_tuple(point: Sequence[float]) -> tuple[float, float]:
        # Ép kiểu đồng nhất float để tránh sai lệch khi input là int/Decimal.
        return (float(point[0]), float(point[1]))

    @staticmethod
    def _lane_span_px(lane: LanePolygon) -> float:
        points = list(getattr(lane, "polygon", None) or [])
        if len(points) < 3:
            return 0.0
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        # Lấy cạnh ngắn hơn như "bề rộng lane" xấp xỉ để scale ngưỡng displacement.
        return max(min(span_x, span_y), 0.0)

    @staticmethod
    def _normalize_vector(vector: tuple[float, float]) -> Optional[tuple[float, float]]:
        vx = float(vector[0])
        vy = float(vector[1])
        mag = hypot(vx, vy)
        # Vector quá ngắn coi như không có hướng đáng tin.
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
                # Bỏ segment direction path suy biến.
                continue

            distance = self._distance_point_to_segment(point=point, start=start, end=end)
            # Chọn segment direction_path gần điểm đang xét nhất để tránh lệch tham chiếu.
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_vector = direction
        return best_vector

    def _is_point_evaluable(self, *, rule: DirectionRule, point: tuple[float, float]) -> bool:
        # Điểm phải nằm trong check_zone.
        if not rule.check_shape.contains_xy(point[0], point[1]):
            return False
        # Và không nằm trong các vùng loại trừ (commit/turn/exit) để tránh bias từ maneuver.
        for shape in rule.excluded_shapes:
            if shape.contains_xy(point[0], point[1]):
                return False
        return True

    def _segment_observations(
        self,
        *,
        rule: DirectionRule,
        lane_samples: Sequence[tuple[datetime, tuple[float, float]]],
    ) -> list[SegmentObservation]:
        # Chỉ giữ segment "hợp lệ": đủ dịch chuyển, không gap thời gian quá lớn, nằm trong vùng eval.
        observations: list[SegmentObservation] = []
        if len(lane_samples) < 2:
            return observations

        for index in range(1, len(lane_samples)):
            prev_ts, prev_point = lane_samples[index - 1]
            curr_ts, curr_point = lane_samples[index]
            if not self._is_point_evaluable(rule=rule, point=prev_point):
                continue
            if not self._is_point_evaluable(rule=rule, point=curr_point):
                continue

            dt_ms = int((curr_ts - prev_ts).total_seconds() * 1000.0)
            # Gap lớn thường do drop frame/reconnect, không nên ghép thành 1 segment.
            if dt_ms > self._settings.segment_max_gap_ms:
                continue

            dx = curr_point[0] - prev_point[0]
            dy = curr_point[1] - prev_point[1]
            displacement_px = hypot(dx, dy)
            # Segment dịch chuyển quá nhỏ dễ là nhiễu detector/tracker.
            if displacement_px < self._settings.segment_min_displacement_px:
                continue

            vector = self._normalize_vector((dx, dy))
            if vector is None:
                continue

            observations.append(
                SegmentObservation(
                    ts=curr_ts,
                    vector=vector,
                    # Midpoint được dùng để lấy hướng lane gần nhất theo không gian.
                    midpoint=((curr_point[0] + prev_point[0]) * 0.5, (curr_point[1] + prev_point[1]) * 0.5),
                    displacement_px=displacement_px,
                )
            )
        return observations

    def _blended_reference_vector(
        self,
        *,
        rule: DirectionRule,
        lane_id: int,
        point: tuple[float, float],
        trajectory_vector: tuple[float, float],
        ts: datetime,
    ) -> Optional[tuple[float, float]]:
        lane_vector = self._direction_vector_for_point(rule=rule, point=point)
        if lane_vector is None:
            return None

        blended = lane_vector
        consensus_vector = self._lane_consensus_vector(lane_id=lane_id, ts=ts)
        if consensus_vector is not None:
            alignment = (blended[0] * consensus_vector[0]) + (blended[1] * consensus_vector[1])
            if alignment >= self._settings.lane_consensus_alignment_min_dot:
                # Blend hướng lane tĩnh với vector consensus động học trong lane.
                blended_candidate = self._normalize_vector(
                    (
                        (blended[0] * (1.0 - self._settings.lane_consensus_blend_weight))
                        + (consensus_vector[0] * self._settings.lane_consensus_blend_weight),
                        (blended[1] * (1.0 - self._settings.lane_consensus_blend_weight))
                        + (consensus_vector[1] * self._settings.lane_consensus_blend_weight),
                    )
                )
                if blended_candidate is not None:
                    blended = blended_candidate

        alignment_to_trajectory = (blended[0] * trajectory_vector[0]) + (blended[1] * trajectory_vector[1])
        if alignment_to_trajectory >= self._settings.trajectory_blend_min_alignment_dot:
            # Chỉ blend thêm trajectory khi đang cùng hướng tương đối để tránh kéo lệch mạnh.
            trajectory_blended = self._normalize_vector(
                (
                    (blended[0] * (1.0 - self._settings.trajectory_blend_weight))
                    + (trajectory_vector[0] * self._settings.trajectory_blend_weight),
                    (blended[1] * (1.0 - self._settings.trajectory_blend_weight))
                    + (trajectory_vector[1] * self._settings.trajectory_blend_weight),
                )
            )
            if trajectory_blended is not None:
                blended = trajectory_blended
        return blended

    def _update_lane_consensus(
        self,
        *,
        lane_id: int,
        ts: datetime,
        trajectory_vector: tuple[float, float],
        rule: DirectionRule,
        point: tuple[float, float],
    ) -> None:
        # Chỉ học consensus từ vector có alignment tốt với hướng lane,
        # tránh self-reinforce từ mẫu ngược chiều.
        lane_vector = self._direction_vector_for_point(rule=rule, point=point)
        if lane_vector is None:
            return
        alignment = (trajectory_vector[0] * lane_vector[0]) + (trajectory_vector[1] * lane_vector[1])
        if alignment < self._settings.same_direction_cos_threshold:
            return
        samples = self._lane_consensus_vectors.get(lane_id)
        if samples is None:
            # Tạo deque lane-consensus khi lane xuất hiện lần đầu trong runtime.
            samples = deque(maxlen=self._settings.lane_consensus_sample_window)
            self._lane_consensus_vectors[lane_id] = samples
        samples.append((ts, trajectory_vector))
        self._prune_lane_consensus_samples(samples=samples, ts=ts)

    def _lane_consensus_vector(
        self,
        *,
        lane_id: int,
        ts: datetime,
    ) -> Optional[tuple[float, float]]:
        samples = self._lane_consensus_vectors.get(lane_id)
        if not samples:
            return None
        self._prune_lane_consensus_samples(samples=samples, ts=ts)
        if len(samples) < self._settings.lane_consensus_min_samples:
            return None
        vectors = [vec for _, vec in samples]
        # Trung bình thô để tìm trục hướng chính trước khi loại outlier.
        coarse = self._weighted_mean_vector(vectors=vectors, weights=[1.0] * len(vectors))
        if coarse is None:
            return None
        inliers = [
            vec
            for vec in vectors
            if ((vec[0] * coarse[0]) + (vec[1] * coarse[1])) >= self._settings.lane_consensus_inlier_dot_min
        ]
        # Cần đủ inlier để tránh consensus bị nhiễu bởi số mẫu quá ít.
        if len(inliers) < self._settings.lane_consensus_min_samples:
            return None
        return self._weighted_mean_vector(vectors=inliers, weights=[1.0] * len(inliers))

    def _prune_lane_consensus_samples(
        self,
        *,
        samples: deque[tuple[datetime, tuple[float, float]]],
        ts: datetime,
    ) -> None:
        while samples:
            oldest_ts, _ = samples[0]
            age_ms = int((ts - oldest_ts).total_seconds() * 1000.0)
            # Cửa sổ tuổi mẫu lane-consensus tránh học từ dữ liệu quá cũ.
            if age_ms <= self._settings.lane_consensus_max_age_ms:
                break
            samples.popleft()

    def _is_warmup_active(self, *, lane_started_ts: Optional[datetime], ts: datetime) -> bool:
        if lane_started_ts is None:
            return False
        elapsed_ms = int((ts - lane_started_ts).total_seconds() * 1000.0)
        # Warmup giữ trạng thái unknown một đoạn ngắn ngay sau đổi lane.
        return elapsed_ms < self._settings.warmup_min_duration_ms

    def _tail_contiguous_samples(
        self,
        *,
        lane_samples: Sequence[tuple[datetime, tuple[float, float]]],
    ) -> list[tuple[datetime, tuple[float, float]]]:
        if len(lane_samples) < 2:
            # 0-1 mẫu thì trả nguyên để nhánh caller tự xử lý thiếu dữ liệu.
            return list(lane_samples)
        start_index = 0
        for index in range(1, len(lane_samples)):
            prev_ts = lane_samples[index - 1][0]
            curr_ts = lane_samples[index][0]
            dt_ms = int((curr_ts - prev_ts).total_seconds() * 1000.0)
            if dt_ms > self._settings.segment_max_gap_ms:
                # Cứ gặp gap lớn thì reset mốc đầu, giữ lại đoạn liên tục gần hiện tại nhất.
                start_index = index
        return list(lane_samples[start_index:])

    def _build_lane_excluded_shapes(
        self,
        *,
        lane: LanePolygon,
    ) -> tuple[PreparedPolygon, ...]:
        # Tập vùng loại trừ để không đánh giá hướng trong khu vực xe đang quay/rẽ.
        polygons: list[list[Sequence[float]]] = []
        if lane.commit_gate and len(lane.commit_gate) >= 3:
            polygons.append(list(lane.commit_gate))
        polygons.extend(self._maneuver_polygons(lane=lane, field="turn_zone"))
        polygons.extend(self._maneuver_polygons(lane=lane, field="exit_zone"))

        prepared: list[PreparedPolygon] = []
        for points in polygons:
            if not isinstance(points, list) or len(points) < 3:
                continue
            # Chuyển sang PreparedPolygon để contains_xy chạy nhanh.
            prepared.append(PreparedPolygon.from_points(points))
        return tuple(prepared)

    @staticmethod
    def _maneuver_polygons(
        *,
        lane: LanePolygon,
        field: str,
    ) -> list[list[Sequence[float]]]:
        maneuvers = getattr(lane, "maneuvers", None)
        if not isinstance(maneuvers, dict):
            return []
        polygons: list[list[Sequence[float]]] = []
        for maneuver_cfg in maneuvers.values():
            if maneuver_cfg is None:
                continue
            if isinstance(maneuver_cfg, dict):
                enabled = bool(maneuver_cfg.get("enabled", True))
                raw_points = maneuver_cfg.get(field)
            else:
                enabled = bool(getattr(maneuver_cfg, "enabled", True))
                raw_points = getattr(maneuver_cfg, field, None)
            if not enabled:
                continue
            if not isinstance(raw_points, list) or len(raw_points) < 3:
                continue
            # Chỉ đưa vào exclude khi maneuver bật và có polygon hợp lệ.
            polygons.append(raw_points)
        return polygons

    @staticmethod
    def _weighted_average(*, values: Sequence[float], weights: Sequence[float]) -> float:
        if not values:
            return 0.0
        # Chặn trọng số âm để tránh làm lệch ý nghĩa trung bình có trọng số.
        total_weight = sum(max(float(weight), 0.0) for weight in weights)
        if total_weight <= 1e-6:
            # Fallback trung bình cộng khi toàn bộ trọng số gần 0.
            return sum(float(value) for value in values) / len(values)
        weighted_sum = sum(float(value) * max(float(weight), 0.0) for value, weight in zip(values, weights))
        return weighted_sum / total_weight

    def _weighted_mean_vector(
        self,
        *,
        vectors: Sequence[tuple[float, float]],
        weights: Sequence[float],
    ) -> Optional[tuple[float, float]]:
        if not vectors:
            return None
        sum_x = 0.0
        sum_y = 0.0
        total_weight = 0.0
        for vec, weight in zip(vectors, weights):
            w = max(float(weight), 0.0)
            # Tính tổng vector có trọng số theo từng trục.
            sum_x += float(vec[0]) * w
            sum_y += float(vec[1]) * w
            total_weight += w
        if total_weight <= 1e-6:
            return None
        # Chuẩn hóa lại để vector đầu ra luôn nằm trên vòng tròn đơn vị.
        return self._normalize_vector((sum_x / total_weight, sum_y / total_weight))

    @staticmethod
    def _tail_consensus(
        *,
        dots: Sequence[float],
        weights: Sequence[float],
        comparator: Callable[[float], bool],
    ) -> tuple[int, Optional[float]]:
        streak = 0
        tail_weight = 0.0
        tail_sum = 0.0
        for idx in range(len(dots) - 1, -1, -1):
            value = float(dots[idx])
            if not comparator(value):
                break
            weight = max(float(weights[idx]), 0.0)
            # Chỉ cộng các phần tử liên tiếp ở đuôi đáp ứng comparator.
            streak += 1
            tail_sum += value * weight
            tail_weight += weight
        if streak <= 0:
            return (0, None)
        if tail_weight <= 1e-6:
            # Nếu tail không có trọng số hợp lệ thì fallback trung bình thường.
            return (streak, sum(float(dots[-streak + k]) for k in range(streak)) / streak)
        return (streak, tail_sum / tail_weight)

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
        # Segment suy biến: khoảng cách tính về điểm đầu.
        if length_sq <= 1e-9:
            return hypot(px - x1, py - y1)

        # Chiếu vuông góc điểm lên đoạn [start, end] bằng hệ số t trong [0,1].
        t = ((px - x1) * dx + (py - y1) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        proj_x = x1 + (dx * t)
        proj_y = y1 + (dy * t)
        return hypot(px - proj_x, py - proj_y)
