from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("ultralytics", SimpleNamespace(YOLO=object))

from app.managers.camera_context import CameraContext


def _build_minimal_context_for_queue_tests() -> CameraContext:
    ctx = object.__new__(CameraContext)
    ctx.camera_config = SimpleNamespace(camera_id="cam_test")
    ctx.track_session_id = "session_test"
    ctx._license_plate_jobs_cond = threading.Condition()
    ctx._license_plate_pending_jobs = OrderedDict()
    ctx._license_plate_worker_max_pending_jobs = 3
    ctx._license_plate_worker_batch_size = 8
    ctx._license_plate_worker_stop_event = threading.Event()
    return ctx


def test_license_plate_job_queue_coalesces_by_vehicle_id() -> None:
    ctx = _build_minimal_context_for_queue_tests()
    ts = datetime(2026, 5, 8, 8, 0, 0, tzinfo=timezone.utc)

    ctx._queue_license_plate_job(
        vehicle_id=1,
        ts_dt=ts,
        frame_timestamp_utc_ms=1000,
        bbox_xyxy=[1.0, 1.0, 2.0, 2.0],
        vehicle_crop_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
    )
    ctx._queue_license_plate_job(
        vehicle_id=2,
        ts_dt=ts,
        frame_timestamp_utc_ms=1001,
        bbox_xyxy=[2.0, 2.0, 3.0, 3.0],
        vehicle_crop_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
    )
    ctx._queue_license_plate_job(
        vehicle_id=1,
        ts_dt=ts,
        frame_timestamp_utc_ms=1002,
        bbox_xyxy=[9.0, 9.0, 10.0, 10.0],
        vehicle_crop_bgr=np.ones((10, 10, 3), dtype=np.uint8),
    )

    jobs = ctx._dequeue_license_plate_jobs()

    assert [job.vehicle_id for job in jobs] == [2, 1]
    assert jobs[-1].frame_timestamp_utc_ms == 1002
    assert jobs[-1].bbox_xyxy == [9.0, 9.0, 10.0, 10.0]


def test_license_plate_job_queue_is_bounded() -> None:
    ctx = _build_minimal_context_for_queue_tests()
    ctx._license_plate_worker_max_pending_jobs = 2
    ts = datetime(2026, 5, 8, 8, 0, 0, tzinfo=timezone.utc)

    ctx._queue_license_plate_job(
        vehicle_id=1,
        ts_dt=ts,
        frame_timestamp_utc_ms=1000,
        bbox_xyxy=[1.0, 1.0, 2.0, 2.0],
        vehicle_crop_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
    )
    ctx._queue_license_plate_job(
        vehicle_id=2,
        ts_dt=ts,
        frame_timestamp_utc_ms=1001,
        bbox_xyxy=[2.0, 2.0, 3.0, 3.0],
        vehicle_crop_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
    )
    ctx._queue_license_plate_job(
        vehicle_id=3,
        ts_dt=ts,
        frame_timestamp_utc_ms=1002,
        bbox_xyxy=[3.0, 3.0, 4.0, 4.0],
        vehicle_crop_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
    )

    jobs = ctx._dequeue_license_plate_jobs()

    assert [job.vehicle_id for job in jobs] == [2, 3]
