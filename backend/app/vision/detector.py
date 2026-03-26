from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from ultralytics import YOLO


@dataclass(frozen=True)
class Detection:
    bbox_xyxy: list[float]  # [x1,y1,x2,y2]
    confidence: float
    class_name: str
    class_id: int


class YoloV8VehicleDetector:
    """
    YOLOv8 vehicle detector/classifier.
    """

    ALLOWED_CLASSES = {"motorcycle", "car", "truck", "bus"}

    def __init__(self, weights_path: str = "yolov8n.pt", conf_threshold: float = 0.35, iou_threshold: float = 0.7):
        self.model = YOLO(weights_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)

        # Map COCO class indices -> names and filter to required vehicle types.
        self.class_names: dict[int, str] = dict(self.model.names)
        self.vehicle_class_ids: list[int] = [
            cls_id
            for cls_id, name in self.class_names.items()
            if name in self.ALLOWED_CLASSES
        ]
        # If weights aren't COCO-compatible, the user will need to adjust.

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            frame_bgr,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            classes=self.vehicle_class_ids if self.vehicle_class_ids else None,
            verbose=False,
        )
        dets: list[Detection] = []
        if not results:
            return dets
        r = results[0]

        if r.boxes is None:
            return dets

        boxes = r.boxes
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else None
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else None
        cls_ids = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else None

        if xyxy is None or conf is None or cls_ids is None:
            return dets

        for i in range(xyxy.shape[0]):
            cls_id = int(cls_ids[i])
            class_name = self.class_names.get(cls_id, str(cls_id))
            if class_name not in self.ALLOWED_CLASSES:
                continue
            dets.append(
                Detection(
                    bbox_xyxy=[float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]), float(xyxy[i, 3])],
                    confidence=float(conf[i]),
                    class_name=class_name,
                    class_id=cls_id,
                )
            )
        return dets

