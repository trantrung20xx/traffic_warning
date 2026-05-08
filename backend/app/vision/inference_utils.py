from __future__ import annotations

from pathlib import Path
from typing import Optional

SUPPORTED_INFERENCE_BACKENDS = ("pytorch", "tensorrt", "openvino", "onnxruntime")


def safe_import_torch() -> Optional[object]:
    # Import torch theo kiểu "an toàn": môi trường không có torch vẫn chạy được
    # cho các nhánh chỉ cần CPU hoặc backend khác.
    try:
        import torch
    except Exception:
        return None
    return torch


def resolve_inference_device(
    requested_device: str,
    *,
    missing_torch_error: str,
    cuda_unavailable_error: str,
) -> str:
    # Chuẩn hóa cấu hình device từ settings trước khi truyền xuống YOLO.
    normalized = str(requested_device or "auto").strip().lower()
    if normalized == "auto":
        # Chế độ auto ưu tiên CUDA nếu torch khả dụng và thấy GPU.
        torch = safe_import_torch()
        if torch is not None and torch.cuda.is_available():
            return "cuda:0"
        return "cpu"

    if normalized.startswith("cuda"):
        # Người dùng ép CUDA thì fail-fast nếu môi trường không đáp ứng.
        torch = safe_import_torch()
        if torch is None:
            raise RuntimeError(missing_torch_error)
        if not torch.cuda.is_available():
            raise RuntimeError(cuda_unavailable_error)
        if normalized == "cuda":
            # Chuẩn hóa alias "cuda" về thiết bị cụ thể để nhất quán log/runtime.
            return "cuda:0"
        return normalized

    # Các giá trị khác (cpu, mps...) được giữ nguyên để backend tự xử lý.
    return normalized


def resolve_inference_backend(backend: str, weights_path: str) -> str:
    # Backend chỉ được kích hoạt khi khớp cả cấu hình và đuôi weight tương ứng.
    # Nếu không khớp sẽ fallback về pytorch để tránh crash runtime.
    normalized = str(backend or "pytorch").strip().lower()
    if normalized in {"", "pytorch", "auto"}:
        # auto trong ngữ cảnh backend inference hiện tại map về pytorch.
        return "pytorch"
    if normalized in SUPPORTED_INFERENCE_BACKENDS:
        candidate = Path(weights_path)
        if normalized == "tensorrt" and candidate.suffix.lower() == ".engine":
            return "tensorrt"
        if normalized == "openvino" and (
            candidate.suffix.lower() == ".xml" or candidate.name.endswith("_openvino_model")
        ):
            return "openvino"
        if normalized == "onnxruntime" and candidate.suffix.lower() == ".onnx":
            return "onnxruntime"
        # Cấu hình backend hợp lệ nhưng không khớp loại weight -> fallback an toàn.
        return "pytorch"
    return "pytorch"
