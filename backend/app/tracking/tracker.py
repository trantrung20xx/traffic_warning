from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.vision.detector import YoloV8VehicleDetector


@dataclass(frozen=True)
class Track:
    # Bản ghi track tối giản dùng xuyên suốt pipeline business logic.
    vehicle_id: int
    vehicle_type: str
    bbox_xyxy: list[float]
    confidence: float


class YoloByteTrackVehicleTracker:
    """
    Lớp theo dõi phương tiện dùng ByteTrack thông qua API `track` của Ultralytics.
    """

    def __init__(
        self,
        detector: YoloV8VehicleDetector,
        *,
        tracker_config: str = "bytetrack.yaml",
    ):
        self.detector = detector
        self.tracker_config = tracker_config

    def track(self, frame_bgr: np.ndarray) -> list[Track]:
        """Chạy detector + tracker của Ultralytics và trả về danh sách track hiện tại."""
        if not self.detector.vehicle_class_ids:
            # Không có class xe hợp lệ trong mapping thì bỏ qua toàn bộ frame.
            return []

        # Ultralytics giữ state ByteTrack nội bộ khi persist=True,
        # nhờ đó track_id được duy trì giữa các frame.
        results = self.detector.model.track(
            frame_bgr,
            device=self.detector.device,
            persist=True,
            conf=self.detector.conf_threshold,
            iou=self.detector.iou_threshold,
            classes=self.detector.vehicle_class_ids,
            tracker=self.tracker_config,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]

        # ByteTrack chưa cấp id (thường ở frame đầu hoặc detection không ổn định) thì chưa emit track.
        if r.boxes is None or r.boxes.id is None:
            return []

        # Quy đổi tensor GPU/torch về numpy CPU để logic phía sau xử lý nhanh và đơn giản.
        boxes = r.boxes
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else None
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else None
        cls_ids = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else None
        ids = boxes.id.cpu().numpy().astype(int)

        if xyxy is None or conf is None or cls_ids is None:
            # Thiếu tensor thiết yếu thì bỏ frame để tránh lỗi downstream.
            return []

        tracks: list[Track] = []
        for i in range(xyxy.shape[0]):
            vehicle_id = int(ids[i])
            cls_id = int(cls_ids[i])
            vehicle_type = self.detector.class_names.get(cls_id, str(cls_id))
            # Lọc thêm một lớp ở đây để phòng trường hợp model trả nhãn ngoài cấu hình.
            if vehicle_type not in self.detector.allowed_class_set:
                continue
            tracks.append(
                Track(
                    vehicle_id=vehicle_id,
                    vehicle_type=vehicle_type,
                    # Ép kiểu float thuần Python để payload JSON/Pydantic xử lý ổn định.
                    bbox_xyxy=[float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]), float(xyxy[i, 3])],
                    confidence=float(conf[i]),
                )
            )
        return tracks

