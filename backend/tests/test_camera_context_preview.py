from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("ultralytics", SimpleNamespace(YOLO=object))

from app.managers.camera_context import CameraContext


def _build_context_for_preview_tests() -> CameraContext:
    ctx = object.__new__(CameraContext)
    ctx._preview_pending_lock = threading.Lock()
    ctx._preview_pending_event = threading.Event()
    ctx._preview_pending_frame_bgr = None
    ctx._preview_pending_frame_ts_ms = 0
    ctx._preview_condition = threading.Condition(threading.Lock())
    ctx._latest_preview_jpeg = None
    ctx._latest_preview_seq = 0
    ctx._last_preview_encode_ms = 0
    ctx._last_preview_source_ts_ms = 0
    ctx._preview_output_width = 0
    ctx._preview_output_height = 0
    ctx._preview_jpeg_quality = 70
    ctx._preview_min_interval_ms = 0
    return ctx


def test_submit_preview_frame_does_not_copy_on_processing_path() -> None:
    ctx = _build_context_for_preview_tests()
    source = np.zeros((8, 8, 3), dtype=np.uint8)

    ctx._submit_preview_frame(source, 1000)
    source[0, 0] = [255, 255, 255]

    with ctx._preview_pending_lock:
        pending = ctx._preview_pending_frame_bgr
        pending_ts = ctx._preview_pending_frame_ts_ms

    assert pending is None
    assert int(pending_ts) == 1000
    assert ctx._preview_pending_event.is_set()


def test_maybe_update_preview_ignores_out_of_order_frame_timestamp() -> None:
    ctx = _build_context_for_preview_tests()
    frame_a = np.zeros((24, 24, 3), dtype=np.uint8)
    frame_b = np.full((24, 24, 3), 255, dtype=np.uint8)

    ctx._maybe_update_preview(frame_a, source_timestamp_utc_ms=200)
    first_jpeg = ctx._latest_preview_jpeg
    first_seq = ctx._latest_preview_seq

    ctx._maybe_update_preview(frame_b, source_timestamp_utc_ms=150)

    assert ctx._latest_preview_seq == first_seq
    assert ctx._latest_preview_jpeg == first_jpeg
    assert ctx._last_preview_source_ts_ms == 200
