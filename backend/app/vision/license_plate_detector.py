from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from ultralytics import YOLO
from app.vision.inference_utils import resolve_inference_backend, resolve_inference_device


@dataclass(frozen=True)
class LicensePlateDetection:
    bbox_xyxy: list[float]
    confidence: float


class YoloV8LicensePlateDetector:
    """
    Bộ phát hiện vùng biển số dùng YOLOv8, chạy trên crop vùng xe.
    """

    def __init__(
        self,
        *,
        weights_path: str,
        inference_backend: str = "pytorch",
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.7,
        device: str = "auto",
        allowed_classes: Optional[Iterable[str]] = None,
    ):
        self.model = self._load_model(weights_path)
        self.requested_backend = (inference_backend or "pytorch").strip().lower()
        self.inference_backend = resolve_inference_backend(self.requested_backend, weights_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.requested_device = (device or "auto").strip()
        self.device = resolve_inference_device(
            self.requested_device,
            missing_torch_error=(
                "license plate detector requests CUDA but PyTorch is not installed in this environment."
            ),
            cuda_unavailable_error=(
                "license plate detector requests CUDA but torch.cuda.is_available() is False."
            ),
        )
        self.class_names: dict[int, str] = dict(self.model.names)
        self.allowed_classes = self._normalize_allowed_classes(allowed_classes)
        self.allowed_class_set = {self._normalize_class_name(item) for item in self.allowed_classes}
        self.allowed_class_ids = [
            cls_id
            for cls_id, name in self.class_names.items()
            if self._normalize_class_name(name) in self.allowed_class_set
        ]

    def detect(self, image_bgr: np.ndarray) -> list[LicensePlateDetection]:
        if image_bgr is None or image_bgr.size == 0:
            return []
        results = self.model.predict(
            image_bgr,
            device=self.device,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            classes=self.allowed_class_ids or None,
            verbose=False,
        )
        if not results:
            return []
        return self._detections_from_result(results[0])

    def detect_batch(self, images_bgr: list[np.ndarray]) -> list[list[LicensePlateDetection]]:
        if not images_bgr:
            return []

        outputs: list[list[LicensePlateDetection]] = [[] for _ in images_bgr]
        valid_indices: list[int] = []
        valid_images: list[np.ndarray] = []
        for index, image in enumerate(images_bgr):
            if image is None or image.size == 0:
                continue
            valid_indices.append(index)
            valid_images.append(image)

        if not valid_images:
            return outputs

        try:
            results = self.model.predict(
                valid_images,
                device=self.device,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                classes=self.allowed_class_ids or None,
                verbose=False,
            )
        except Exception:
            for index in valid_indices:
                outputs[index] = self.detect(images_bgr[index])
            return outputs

        for idx, result in enumerate(results or []):
            if idx >= len(valid_indices):
                break
            outputs[valid_indices[idx]] = self._detections_from_result(result)
        return outputs

    def _detections_from_result(self, result) -> list[LicensePlateDetection]:
        if result is None or result.boxes is None:
            return []

        boxes = result.boxes
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else None
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else None
        cls_ids = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else None
        if xyxy is None or conf is None or cls_ids is None:
            return []

        rows: list[LicensePlateDetection] = []
        for idx in range(xyxy.shape[0]):
            cls_id = int(cls_ids[idx])
            class_name = self.class_names.get(cls_id, str(cls_id))
            if self._normalize_class_name(class_name) not in self.allowed_class_set:
                continue
            rows.append(
                LicensePlateDetection(
                    bbox_xyxy=[
                        float(xyxy[idx, 0]),
                        float(xyxy[idx, 1]),
                        float(xyxy[idx, 2]),
                        float(xyxy[idx, 3]),
                    ],
                    confidence=float(conf[idx]),
                )
            )
        rows.sort(key=lambda row: row.confidence, reverse=True)
        return rows

    def _normalize_allowed_classes(
        self,
        allowed_classes: Optional[Iterable[str]],
    ) -> list[str]:
        raw_items = self.class_names.values() if allowed_classes is None else allowed_classes
        if isinstance(raw_items, str):
            raw_items = [raw_items]

        normalized: list[str] = []
        for item in raw_items:
            class_name = str(item).strip()
            if class_name and class_name not in normalized:
                normalized.append(class_name)
        if not normalized:
            raise ValueError("license plate detector allowed_classes must contain at least one class")
        return normalized

    def _normalize_class_name(self, value: object) -> str:
        return str(value).strip().lower().replace("_", "").replace("-", "").replace(" ", "")

    def _load_model(self, weights_path: str):
        candidate = Path(weights_path)
        if not candidate.exists():
            raise FileNotFoundError(f"license plate detector weights not found: {candidate}")
        return YOLO(str(candidate))
