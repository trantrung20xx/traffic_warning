from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.vision.detector import YoloV8VehicleDetector


@dataclass(frozen=True)
class Track:
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
        results = self.detector.model.track(
            frame_bgr,
            device=self.detector.device,
            persist=True,
            conf=self.detector.conf_threshold,
            iou=self.detector.iou_threshold,
            classes=self.detector.vehicle_class_ids if self.detector.vehicle_class_ids else None,
            tracker=self.tracker_config,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]

        if r.boxes is None or r.boxes.id is None:
            return []

        boxes = r.boxes
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else None
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else None
        cls_ids = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else None
        ids = boxes.id.cpu().numpy().astype(int)

        if xyxy is None or conf is None or cls_ids is None:
            return []

        tracks: list[Track] = []
        for i in range(xyxy.shape[0]):
            vehicle_id = int(ids[i])
            cls_id = int(cls_ids[i])
            vehicle_type = self.detector.class_names.get(cls_id, str(cls_id))
            if vehicle_type not in self.detector.ALLOWED_CLASSES:
                continue
            tracks.append(
                Track(
                    vehicle_id=vehicle_id,
                    vehicle_type=vehicle_type,
                    bbox_xyxy=[float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]), float(xyxy[i, 3])],
                    confidence=float(conf[i]),
                )
            )
        return tracks

