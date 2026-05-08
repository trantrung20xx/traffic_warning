from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from app.tracking.tracker import Track


def _bbox_iou(box_a: list[float], box_b: list[float]) -> float:
    """Tính IoU giữa hai bounding box để đo mức độ chồng lấn hình học."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    # Tạo vùng giao nhau theo quy tắc giao hình chữ nhật trục song song.
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    # Diện tích giao bằng width * height (được clamp không âm).
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    # Union = area(A) + area(B) - inter(A,B).
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


def _bbox_center(box: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    # Tâm bbox là trung bình 2 đỉnh chéo.
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _normalized_center_distance(box_a: list[float], box_b: list[float]) -> float:
    """Tính khoảng cách tâm đã chuẩn hóa theo kích thước box để so sánh xe lớn nhỏ công bằng hơn."""
    ax, ay = _bbox_center(box_a)
    bx, by = _bbox_center(box_b)
    # Lấy kích thước mỗi box để chuẩn hóa khoảng cách tâm theo scale đối tượng.
    aw = max(1.0, box_a[2] - box_a[0])
    ah = max(1.0, box_a[3] - box_a[1])
    bw = max(1.0, box_b[2] - box_b[0])
    bh = max(1.0, box_b[3] - box_b[1])
    # Scale dùng cạnh lớn hơn trung bình để robust giữa xe to/nhỏ.
    scale = max((aw + bw) / 2.0, (ah + bh) / 2.0, 1.0)
    return (((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5) / scale


@dataclass
class StableTrackState:
    # stable_vehicle_id là id nội bộ đã làm mượt qua thời gian.
    stable_vehicle_id: int
    # BBox cuối cùng dùng cho bước rebind frame kế tiếp.
    bbox_xyxy: list[float]
    last_seen_ts: datetime


class StableTrackIdAssigner:
    """
    Giữ `vehicle_id` ổn định ngay cả khi tracker bên dưới đổi raw id trong chốc lát.

    Chiến lược:
    - Ưu tiên giữ nguyên ánh xạ raw id -> stable id nếu hình học vẫn hợp lý.
    - Nếu raw id bị đổi, thử nối lại với track ổn định gần nhất bằng IoU và độ liên tục vị trí
      trước khi cấp một stable id mới.
    - Không dùng nhãn loại xe để quyết định identity vì nhãn detector có thể đổi trong lúc
      vẫn là cùng một đối tượng đang được theo dõi.
    - Xóa trạng thái cũ sớm để tránh tái sử dụng nhầm id cho xe khác.
    """

    def __init__(
        self,
        *,
        max_idle_ms: int = 1500,
        min_iou_for_rebind: float = 0.15,
        max_normalized_distance: float = 1.6,
    ):
        # max_idle là khoảng tối đa giữ state khi xe tạm mất track.
        self._max_idle = timedelta(milliseconds=int(max_idle_ms))
        # Điều kiện tối thiểu để nối raw id mới vào stable id cũ.
        self._min_iou_for_rebind = float(min_iou_for_rebind)
        self._max_normalized_distance = float(max_normalized_distance)
        # Bộ đếm phát id mới tăng dần, không tái sử dụng trong phiên runtime.
        self._next_stable_vehicle_id = 1
        # State chính theo stable id.
        self._stable_states: dict[int, StableTrackState] = {}
        # Bản đồ raw id hiện tại -> stable id.
        self._raw_to_stable: dict[int, int] = {}

    def assign(self, *, raw_tracks: Iterable[Track], ts: datetime) -> list[Track]:
        """Gán stable id cho danh sách track hiện tại."""
        # Dọn state cũ trước khi gán để tránh match vào object đã hết hạn.
        self.prune(current_ts=ts)

        # Copy iterable về list để có thể duyệt nhiều lượt.
        remaining_tracks = list(raw_tracks)
        resolved_tracks: list[Track] = []
        used_stable_ids: set[int] = set()

        # Lượt 1: giữ nguyên ánh xạ cũ nếu vị trí/hình dạng vẫn còn khớp.
        next_remaining_tracks: list[Track] = []
        for track in remaining_tracks:
            stable_vehicle_id = self._raw_to_stable.get(track.vehicle_id)
            state = self._stable_states.get(stable_vehicle_id) if stable_vehicle_id is not None else None
            if stable_vehicle_id is None or state is None or stable_vehicle_id in used_stable_ids:
                next_remaining_tracks.append(track)
                continue

            # Đo mức khớp hình học giữa track hiện tại và state stable cũ.
            iou = _bbox_iou(track.bbox_xyxy, state.bbox_xyxy)
            distance = _normalized_center_distance(track.bbox_xyxy, state.bbox_xyxy)
            if iou <= 0.01 and distance > self._max_normalized_distance:
                # Khớp quá yếu thì chưa dám giữ ánh xạ cũ.
                next_remaining_tracks.append(track)
                continue

            resolved_tracks.append(self._update_state(track=track, stable_vehicle_id=stable_vehicle_id, ts=ts))
            used_stable_ids.add(stable_vehicle_id)

        remaining_tracks = next_remaining_tracks

        # Lượt 2: khi raw id bị đổi, thử nối lại với stable track gần nhất còn mới.
        candidate_matches: list[tuple[float, Track, int]] = []
        for track in remaining_tracks:
            for stable_vehicle_id, state in self._stable_states.items():
                if stable_vehicle_id in used_stable_ids:
                    continue
                age = ts - state.last_seen_ts
                if age > self._max_idle:
                    # State quá cũ không dùng để rebind.
                    continue
                iou = _bbox_iou(track.bbox_xyxy, state.bbox_xyxy)
                distance = _normalized_center_distance(track.bbox_xyxy, state.bbox_xyxy)
                if iou < self._min_iou_for_rebind and distance > self._max_normalized_distance:
                    continue
                # Score kết hợp overlap + độ gần để xếp hạng candidate match.
                score = iou * 1.4 + max(0.0, 1.0 - distance)
                candidate_matches.append((score, track, stable_vehicle_id))

        matched_raw_ids: set[int] = set()
        # Greedy theo score giảm dần, đảm bảo 1-1 giữa raw track và stable id.
        for _, track, stable_vehicle_id in sorted(candidate_matches, key=lambda item: item[0], reverse=True):
            if track.vehicle_id in matched_raw_ids or stable_vehicle_id in used_stable_ids:
                continue
            resolved_tracks.append(self._update_state(track=track, stable_vehicle_id=stable_vehicle_id, ts=ts))
            used_stable_ids.add(stable_vehicle_id)
            matched_raw_ids.add(track.vehicle_id)

        # Lượt 3: chỉ cấp stable id mới khi thật sự không ghép được với track trước đó.
        for track in remaining_tracks:
            if track.vehicle_id in matched_raw_ids:
                continue
            # Không ghép được với state nào thì cấp stable id mới.
            stable_vehicle_id = self._next_stable_vehicle_id
            self._next_stable_vehicle_id += 1
            resolved_tracks.append(self._update_state(track=track, stable_vehicle_id=stable_vehicle_id, ts=ts))
            used_stable_ids.add(stable_vehicle_id)

        return resolved_tracks

    def _update_state(self, *, track: Track, stable_vehicle_id: int, ts: datetime) -> Track:
        """Cập nhật trạng thái của stable track và trả về bản track đã đổi sang stable id."""
        # Lưu bbox mới nhất cho vòng match kế tiếp.
        self._stable_states[stable_vehicle_id] = StableTrackState(
            stable_vehicle_id=stable_vehicle_id,
            bbox_xyxy=list(track.bbox_xyxy),
            last_seen_ts=ts,
        )
        # Cập nhật ánh xạ raw id hiện tại.
        self._raw_to_stable[track.vehicle_id] = stable_vehicle_id
        # Trả về object Track mới mang stable id cho downstream logic.
        return Track(
            vehicle_id=stable_vehicle_id,
            vehicle_type=track.vehicle_type,
            bbox_xyxy=list(track.bbox_xyxy),
            confidence=track.confidence,
        )

    def prune(self, *, current_ts: datetime, max_age_s: float | None = None) -> None:
        """Xóa stable track và raw mapping đã quá hạn để bộ nhớ không tăng mãi."""
        # max_age_s nếu được truyền vào sẽ override max_idle mặc định.
        max_age = timedelta(seconds=float(max_age_s)) if max_age_s is not None else self._max_idle
        stale_ids = [
            stable_vehicle_id
            for stable_vehicle_id, state in self._stable_states.items()
            if current_ts - state.last_seen_ts > max_age
        ]
        for stable_vehicle_id in stale_ids:
            del self._stable_states[stable_vehicle_id]

        # Dọn raw->stable trỏ tới state đã bị xóa.
        stale_raw_ids = [
            raw_track_id
            for raw_track_id, stable_vehicle_id in self._raw_to_stable.items()
            if stable_vehicle_id not in self._stable_states
        ]
        for raw_track_id in stale_raw_ids:
            del self._raw_to_stable[raw_track_id]
