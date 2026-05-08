from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ultralytics import YOLO
from app.vision.inference_utils import resolve_inference_backend, resolve_inference_device

class YoloV8VehicleDetector:
    """
    Bộ phát hiện và phân loại phương tiện dùng YOLOv8.
    """

    DEFAULT_ALLOWED_CLASSES = ("motorcycle", "car", "truck", "bus")

    def __init__(
        self,
        weights_path: str = "yolov8n.pt",
        inference_backend: str = "pytorch",
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.7,
        device: str = "auto",
        allowed_classes: Optional[Iterable[str]] = None,
    ):
        self.model = self._load_model_with_fallback(weights_path)
        # Tách requested_* và resolved_* để log/debug khi backend thực chạy khác cấu hình ban đầu.
        self.requested_backend = (inference_backend or "pytorch").strip().lower()
        self.inference_backend = resolve_inference_backend(self.requested_backend, weights_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.requested_device = (device or "auto").strip()
        self.device = resolve_inference_device(
            self.requested_device,
            missing_torch_error=(
                "detector_device requests CUDA but PyTorch is not installed in this environment."
            ),
            cuda_unavailable_error=(
                "detector_device requests CUDA but torch.cuda.is_available() is False."
            ),
        )
        self.allowed_classes = self._normalize_allowed_classes(allowed_classes)
        self.allowed_class_set = set(self.allowed_classes)

        # Ánh xạ id lớp COCO sang tên lớp rồi lọc chỉ giữ các loại xe cần dùng.
        self.class_names: dict[int, str] = dict(self.model.names)
        # vehicle_class_ids là danh sách id truyền trực tiếp vào YOLO để giảm false-positive ngoài phạm vi.
        self.vehicle_class_ids: list[int] = [
            cls_id
            for cls_id, name in self.class_names.items()
            if name in self.allowed_class_set
        ]
        # Nếu bộ weight không theo nhãn COCO thì cần chỉnh lại phần ánh xạ lớp ở đây.

    def _normalize_allowed_classes(
        self,
        allowed_classes: Optional[Iterable[str]],
    ) -> list[str]:
        # Chuẩn hóa danh sách class để loại trùng và chặn cấu hình rỗng.
        # Khi None sẽ fallback bộ class mặc định của hệ thống.
        raw_items = self.DEFAULT_ALLOWED_CLASSES if allowed_classes is None else allowed_classes
        if isinstance(raw_items, str):
            raw_items = [raw_items]

        normalized: list[str] = []
        for item in raw_items:
            class_name = str(item).strip()
            if class_name and class_name not in normalized:
                normalized.append(class_name)
        if not normalized:
            raise ValueError("detector allowed_classes must contain at least one class")
        return normalized

    def _load_model_with_fallback(self, weights_path: str):
        # Thử load theo danh sách candidate để tăng khả năng khởi động
        # khi file weight cấu hình bị thiếu hoặc sai tên.
        candidate_paths = self._build_weight_candidates(weights_path)
        errors: list[str] = []

        for candidate in candidate_paths:
            try:
                return YOLO(candidate)
            except Exception as exc:
                # Lưu lỗi từng candidate để trả thông báo chẩn đoán đầy đủ khi fail toàn bộ.
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
            # Chỉ thử fallback cho weight PyTorch cùng thư mục.
            sibling_names = ["yolov8x.pt", "yolov8l.pt", "yolov8m.pt", "yolov8s.pt", "yolov8n.pt"]
            for name in sibling_names:
                candidate = requested.with_name(name)
                if candidate not in candidates and candidate.exists():
                    # Chỉ append candidate tồn tại để tránh noise do path ảo.
                    candidates.append(candidate)

        return [str(path) for path in candidates]
