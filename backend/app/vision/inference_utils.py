from __future__ import annotations

from pathlib import Path
from typing import Optional

SUPPORTED_INFERENCE_BACKENDS = ("pytorch", "tensorrt", "openvino", "onnxruntime")


def safe_import_torch() -> Optional[object]:
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
    normalized = str(requested_device or "auto").strip().lower()
    if normalized == "auto":
        torch = safe_import_torch()
        if torch is not None and torch.cuda.is_available():
            return "cuda:0"
        return "cpu"

    if normalized.startswith("cuda"):
        torch = safe_import_torch()
        if torch is None:
            raise RuntimeError(missing_torch_error)
        if not torch.cuda.is_available():
            raise RuntimeError(cuda_unavailable_error)
        if normalized == "cuda":
            return "cuda:0"
        return normalized

    return normalized


def resolve_inference_backend(backend: str, weights_path: str) -> str:
    normalized = str(backend or "pytorch").strip().lower()
    if normalized in {"", "pytorch", "auto"}:
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
        return "pytorch"
    return "pytorch"
