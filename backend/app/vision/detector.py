from __future__ import annotations

from pathlib import Path
from typing import Optional

from ultralytics import YOLO

class YoloV8VehicleDetector:
    """
    Bộ phát hiện và phân loại phương tiện dùng YOLOv8.
    """

    ALLOWED_CLASSES = {"motorcycle", "car", "truck", "bus"}

    def __init__(
        self,
        weights_path: str = "yolov8n.pt",
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.7,
        device: str = "auto",
    ):
        self.model = self._load_model_with_fallback(weights_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.requested_device = (device or "auto").strip()
        self.device = self._resolve_inference_device(self.requested_device)

        # Ánh xạ id lớp COCO sang tên lớp rồi lọc chỉ giữ các loại xe cần dùng.
        self.class_names: dict[int, str] = dict(self.model.names)
        self.vehicle_class_ids: list[int] = [
            cls_id
            for cls_id, name in self.class_names.items()
            if name in self.ALLOWED_CLASSES
        ]
        # Nếu bộ weight không theo nhãn COCO thì cần chỉnh lại phần ánh xạ lớp ở đây.

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
                    "detector_device requests CUDA but PyTorch is not installed in this environment."
                )
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "detector_device requests CUDA but torch.cuda.is_available() is False."
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

    def _load_model_with_fallback(self, weights_path: str):
        candidate_paths = self._build_weight_candidates(weights_path)
        errors: list[str] = []

        for candidate in candidate_paths:
            try:
                return YOLO(candidate)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        joined_errors = "\n".join(errors)
        raise RuntimeError(
            "Unable to load any YOLO weights. Checked these candidates:\n"
            f"{joined_errors}"
        )

    def _build_weight_candidates(self, weights_path: str) -> list[str]:
        requested = Path(weights_path)
        candidates: list[Path] = [requested]

        if requested.suffix == ".pt":
            sibling_names = ["yolov8x.pt", "yolov8l.pt", "yolov8m.pt", "yolov8s.pt", "yolov8n.pt"]
            for name in sibling_names:
                candidate = requested.with_name(name)
                if candidate not in candidates and candidate.exists():
                    candidates.append(candidate)

        return [str(path) for path in candidates]

