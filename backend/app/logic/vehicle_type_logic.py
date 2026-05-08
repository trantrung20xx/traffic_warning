from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class VehicleTypeState:
    # Lưu lịch sử ngắn: (thời điểm, nhãn dự đoán, độ tin cậy) cho từng xe.
    recent_observations: deque[tuple[datetime, str, float]] = field(default_factory=deque)


class TemporalVehicleTypeAssigner:
    """
    Làm mượt nhãn loại phương tiện trong một cửa sổ track ngắn.

    Một frame đơn lẻ rất dễ nhầm ô tô, xe tải, xe buýt khi bị che khuất hoặc nhòe chuyển động.
    Vì vậy cần gom các dự đoán gần đây của cùng `vehicle_id` rồi bỏ phiếu theo độ tin cậy,
    đồng thời ưu tiên nhẹ cho các quan sát mới hơn.
    """

    def __init__(
        self,
        *,
        history_window_ms: int = 4000,
        history_size: int = 12,
        recency_weight_bias: float = 0.15,
    ):
        # Cửa sổ thời gian tối đa giữ lại quan sát.
        self._history_window = timedelta(milliseconds=int(history_window_ms))
        # Giới hạn số phần tử để state mỗi xe không tăng vô hạn.
        self._history_size = max(int(history_size), 1)
        # Hệ số thiên vị quan sát mới (recency).
        self._recency_weight_bias = float(recency_weight_bias)
        # Map vehicle_id -> trạng thái lịch sử nhãn.
        self._vehicle_states: dict[int, VehicleTypeState] = {}

    def resolve_type(self, *, vehicle_id: int, predicted_type: str, confidence: float, ts: datetime) -> str:
        """Trả về loại phương tiện ổn định hơn cho xe đang theo dõi."""
        # Lấy state đã có; nếu chưa có thì khởi tạo mới cho vehicle_id này.
        state = self._vehicle_states.get(vehicle_id)
        if state is None:
            state = VehicleTypeState()
            self._vehicle_states[vehicle_id] = state

        # Ghi nhận dự đoán frame hiện tại vào cuối deque (mới nhất).
        state.recent_observations.append((ts, predicted_type, float(confidence)))
        # Cắt theo kích thước tối đa để giới hạn RAM và độ trễ vote.
        while len(state.recent_observations) > self._history_size:
            state.recent_observations.popleft()

        # Cắt các quan sát cũ hơn cửa sổ thời gian cấu hình.
        cutoff = ts - self._history_window
        while state.recent_observations and state.recent_observations[0][0] < cutoff:
            state.recent_observations.popleft()

        # Bảng điểm tích lũy theo từng nhãn vehicle_type.
        scores: dict[str, float] = {}
        # Tổng số quan sát còn lại sau khi prune.
        total = len(state.recent_observations)
        for index, (_, observed_type, observed_confidence) in enumerate(state.recent_observations):
            # Quan sát mới được cộng điểm nhỉnh hơn để nhãn đổi nhanh khi tracker đã ổn định.
            recency_weight = 1.0 + (index / max(total - 1, 1)) * self._recency_weight_bias
            # Điểm = confidence * trọng số recency, cộng dồn theo nhãn.
            scores[observed_type] = scores.get(observed_type, 0.0) + observed_confidence * recency_weight

        # Chọn nhãn có điểm cao nhất; fallback về predicted_type nếu không còn quan sát.
        return max(scores.items(), key=lambda item: item[1])[0] if scores else predicted_type

    def prune(self, *, current_ts: datetime, max_age_s: float = 10.0) -> None:
        """Dọn lịch sử nhãn của các xe đã biến mất khỏi khung hình."""
        # Mốc cutoff chung để xóa dữ liệu cũ.
        cutoff = current_ts - timedelta(seconds=float(max_age_s))
        stale_vehicle_ids = []
        for vehicle_id, state in self._vehicle_states.items():
            # Xóa các observation quá tuổi trong từng state.
            while state.recent_observations and state.recent_observations[0][0] < cutoff:
                state.recent_observations.popleft()
            # Nếu state rỗng hoàn toàn thì đánh dấu xóa cả vehicle_id.
            if not state.recent_observations:
                stale_vehicle_ids.append(vehicle_id)

        # Xóa state rỗng khỏi map chính.
        for vehicle_id in stale_vehicle_ids:
            del self._vehicle_states[vehicle_id]
