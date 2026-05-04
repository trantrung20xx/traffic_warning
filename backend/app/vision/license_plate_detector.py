from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from ultralytics import YOLO


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
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.7,
        device: str = "auto",
    ):
        self.model = self._load_model(weights_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.requested_device = (device or "auto").strip()
        self.device = self._resolve_inference_device(self.requested_device)

    def detect(self, image_bgr: np.ndarray) -> list[LicensePlateDetection]:
        if image_bgr is None or image_bgr.size == 0:
            return []
        results = self.model.predict(
            image_bgr,
            device=self.device,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            verbose=False,
        )
        if not results:
            return []
        result = results[0]
        if result.boxes is None:
            return []

        boxes = result.boxes
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else None
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else None
        if xyxy is None or conf is None:
            return []

        rows: list[LicensePlateDetection] = []
        for idx in range(xyxy.shape[0]):
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

    def _resolve_inference_device(self, requested_device: str) -> str:
        normalized = requested_device.lower()
        if normalized == "auto":
            torch = self._safe_import_torch()
            if torch is not None and torch.cuda.is_available():
                return "cuda:0"
            return "cpu"

        if normalized.startswith("cuda"):
            torch = self._safe_import_torch()
            if torch is None:
                raise RuntimeError(
                    "license plate detector requests CUDA but PyTorch is not installed in this environment."
                )
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "license plate detector requests CUDA but torch.cuda.is_available() is False."
                )
            if normalized == "cuda":
                return "cuda:0"
            return normalized
        return normalized

    def _safe_import_torch(self) -> Optional[object]:
        try:
            import torch
        except Exception:
            return None
        return torch

    def _load_model(self, weights_path: str):
        candidate = Path(weights_path)
        if not candidate.exists():
            raise FileNotFoundError(f"license plate detector weights not found: {candidate}")
        return YOLO(str(candidate))
