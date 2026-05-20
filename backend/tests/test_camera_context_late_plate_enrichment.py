from __future__ import annotations

import sys
import threading
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("ultralytics", SimpleNamespace(YOLO=object))

import app.managers.camera_context as camera_context_module
from app.logic.license_plate_logic import LicensePlateSnapshot
from app.managers.camera_context import (
    CameraContext,
    _PendingViolationPlateState,
    _TrackContinuityState,
)


class _DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _build_context_for_late_plate_tests() -> CameraContext:
    ctx = object.__new__(CameraContext)
    ctx.camera_config = SimpleNamespace(camera_id="cam_test")
    ctx.track_session_id = "cam_test-session"
    ctx.repo_root = Path(".")
    ctx._db_session_factory = lambda: _DummySession()
    ctx._late_plate_state_lock = threading.Lock()
    ctx._pending_violation_plate_states = {}
    ctx._pending_violation_vehicle_ids = set()
    ctx._track_continuity_states = {}
    ctx._license_plate_violation_update_enabled = True
    ctx._license_plate_violation_update_min_confidence = 0.8
    ctx._license_plate_violation_update_consensus_min_hits = 2
    ctx._license_plate_violation_update_window_ms = 5000
    ctx._license_plate_violation_require_clean_track = True
    ctx._license_plate_violation_track_min_observations = 3
    ctx._license_plate_image_jpeg_quality = 90
    ctx._license_plate_enabled = True
    return ctx


def test_late_plate_enrichment_updates_pending_violation(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 7

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=100),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=6,
        dirty=False,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.91,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: "config/evidence_images/cam_test/lp.jpg"

    captured = {"calls": 0}

    def _fake_update(*args, **kwargs):
        captured["calls"] += 1
        captured["kwargs"] = kwargs
        return 1

    monkeypatch.setattr(camera_context_module, "update_pending_violation_plate", _fake_update)

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 1
    assert captured["kwargs"]["camera_id"] == "cam_test"
    assert captured["kwargs"]["track_session_id"] == "cam_test-session"
    assert captured["kwargs"]["vehicle_id"] == vehicle_id
    assert vehicle_id not in ctx._pending_violation_vehicle_ids


def test_late_plate_enrichment_rejects_dirty_track(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 9

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=500),
        last_pending_ts=ts - timedelta(milliseconds=300),
        pending_count=1,
        last_lane_id=1,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=50),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=6,
        dirty=True,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.95,
        consensus_hits=3,
        attempt_count=5,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: "config/evidence_images/cam_test/lp.jpg"

    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_pending_violation_plate",
        lambda *args, **kwargs: captured.__setitem__("calls", captured["calls"] + 1),
    )

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0
    assert vehicle_id in ctx._pending_violation_vehicle_ids


def test_late_plate_enrichment_rejects_outside_update_window(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ctx._license_plate_violation_update_window_ms = 1000
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 12

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(seconds=5),
        last_pending_ts=ts - timedelta(seconds=2),
        pending_count=1,
        last_lane_id=1,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=5),
        last_seen_ts=ts - timedelta(seconds=2),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=10,
        dirty=False,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.95,
        consensus_hits=3,
        attempt_count=8,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: "config/evidence_images/cam_test/lp.jpg"

    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_pending_violation_plate",
        lambda *args, **kwargs: captured.__setitem__("calls", captured["calls"] + 1),
    )

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0


def test_late_plate_enrichment_rejects_when_consensus_hits_are_insufficient(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 15

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=500),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=1,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=100),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=6,
        dirty=False,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.95,
        consensus_hits=1,
        attempt_count=5,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: "config/evidence_images/cam_test/lp.jpg"

    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_pending_violation_plate",
        lambda *args, **kwargs: captured.__setitem__("calls", captured["calls"] + 1),
    )

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0


def test_late_plate_enrichment_keeps_pending_when_repository_updates_zero_rows(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 18

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=100),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=6,
        dirty=False,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.91,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: None
    monkeypatch.setattr(camera_context_module, "update_pending_violation_plate", lambda *args, **kwargs: 0)

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert vehicle_id in ctx._pending_violation_vehicle_ids


def test_late_plate_enrichment_can_update_without_new_plate_crop(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 19

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=100),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=6,
        dirty=False,
    )
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.91,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=ts,
    )
    captured = {"calls": 0, "image_path": "unset"}

    def _fake_update(*args, **kwargs):
        captured["calls"] += 1
        captured["image_path"] = kwargs.get("license_plate_image_path")
        return 1

    monkeypatch.setattr(camera_context_module, "update_pending_violation_plate", _fake_update)

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=None,
    )

    assert captured["calls"] == 0
    assert captured["image_path"] == "unset"
    assert vehicle_id in ctx._pending_violation_vehicle_ids


def test_violation_event_does_not_confirm_plate_without_plate_image() -> None:
    ctx = object.__new__(CameraContext)
    ctx.camera_config = SimpleNamespace(
        camera_id="cam_test",
        location=SimpleNamespace(
            road_name="Road A",
            intersection_name=None,
            gps_lat=None,
            gps_lng=None,
        ),
    )
    ctx.track_session_id = "cam_test-session"
    ctx._create_violation_evidence = lambda *args, **kwargs: None
    ctx._create_license_plate_evidence = lambda *args, **kwargs: None
    ctx._save_event_to_db = lambda event: setattr(event, "id", 2)
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.95,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    registered = []
    emitted = []
    ctx._register_pending_violation_for_late_plate = lambda **kwargs: registered.append(kwargs)
    ctx.stats = SimpleNamespace(update_realtime=lambda event: None)
    ctx.on_violation = lambda event: emitted.append(event)

    asyncio.run(
        ctx._handle_violations(
            violation_candidates=[
                (
                    102,
                    "car",
                    {"lane_id": 1, "violation": "wrong_lane"},
                    [10.0, 10.0, 40.0, 40.0],
                )
            ],
            ts_dt=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
            frame_bgr=np.zeros((100, 100, 3), dtype=np.uint8),
            frame_timestamp_utc_ms=1716292800000,
        )
    )

    assert len(emitted) == 1
    assert emitted[0].license_plate is None
    assert emitted[0].license_plate_status == "pending"
    assert emitted[0].license_plate_image_path is None
    assert len(registered) == 1


def test_violation_is_emitted_immediately_even_when_plate_is_pending() -> None:
    ctx = object.__new__(CameraContext)
    ctx.camera_config = SimpleNamespace(
        camera_id="cam_test",
        location=SimpleNamespace(
            road_name="Road A",
            intersection_name=None,
            gps_lat=None,
            gps_lng=None,
        ),
    )
    ctx.track_session_id = "cam_test-session"
    ctx._create_violation_evidence = lambda *args, **kwargs: None
    ctx._create_license_plate_evidence = lambda *args, **kwargs: None
    ctx._save_event_to_db = lambda event: setattr(event, "id", 1)
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate=None,
        status="pending",
        confidence=None,
        consensus_hits=0,
        attempt_count=0,
        confirmed_ts=None,
    )
    registered = []
    emitted = []
    ctx._register_pending_violation_for_late_plate = lambda **kwargs: registered.append(kwargs)
    ctx.stats = SimpleNamespace(update_realtime=lambda event: None)
    ctx.on_violation = lambda event: emitted.append(event)

    asyncio.run(
        ctx._handle_violations(
            violation_candidates=[
                (
                    101,
                    "car",
                    {"lane_id": 1, "violation": "wrong_lane"},
                    [10.0, 10.0, 40.0, 40.0],
                )
            ],
            ts_dt=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
            frame_bgr=np.zeros((100, 100, 3), dtype=np.uint8),
            frame_timestamp_utc_ms=1716292800000,
        )
    )

    assert len(emitted) == 1
    assert emitted[0].license_plate_status == "pending"
    assert len(registered) == 1
