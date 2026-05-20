from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("ultralytics", SimpleNamespace(YOLO=object))

import app.managers.camera_context as camera_context_module
from app.managers.camera_context import CameraContext


def _build_context_for_evidence_tests() -> CameraContext:
    ctx = object.__new__(CameraContext)
    ctx.camera_config = SimpleNamespace(camera_id="cam_test")
    ctx.repo_root = Path(".")
    ctx._evidence_crop_expand_x_ratio = 0.2
    ctx._evidence_crop_expand_y_top_ratio = 0.2
    ctx._evidence_crop_expand_y_bottom_ratio = 0.2
    ctx._evidence_crop_min_size_px = 20
    ctx._evidence_jpeg_quality = 92
    return ctx


def test_create_violation_evidence_draws_red_highlight(monkeypatch) -> None:
    ctx = _build_context_for_evidence_tests()
    frame = np.zeros((140, 220, 3), dtype=np.uint8)
    bbox = [70.0, 45.0, 150.0, 110.0]
    captured = {}

    def _fake_save_evidence_image(*args, **kwargs):
        captured["image_bgr"] = kwargs["image_bgr"].copy()
        return "config/evidence_images/cam_test/highlight.jpg"

    monkeypatch.setattr(camera_context_module, "save_evidence_image", _fake_save_evidence_image)

    result_path = ctx._create_violation_evidence(
        frame,
        bbox,
        frame_timestamp_utc_ms=1716292800000,
        vehicle_id=1,
        lane_id=2,
        violation="wrong_lane",
    )

    assert result_path == "config/evidence_images/cam_test/highlight.jpg"
    evidence = captured.get("image_bgr")
    assert evidence is not None
    red_mask = (evidence[:, :, 2] > 200) & (evidence[:, :, 1] < 80) & (evidence[:, :, 0] < 80)
    assert int(red_mask.sum()) > 0
