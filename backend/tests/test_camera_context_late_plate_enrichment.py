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
    ctx._license_plate_violation_track_max_gap_ms = 900
    ctx._license_plate_image_jpeg_quality = 90
    ctx._evidence_jpeg_quality = 90
    ctx._license_plate_enabled = True
    ctx._license_plate_last_read_ms = {}
    ctx._license_plate_resolver = None
    ctx._license_plate_resolver_lock = threading.Lock()
    ctx.on_violation = lambda event: None
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
    assert vehicle_id in ctx._pending_violation_vehicle_ids
    assert ctx._pending_violation_plate_states[vehicle_id].has_committed_plate is True


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
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 0),
    )

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0
    assert vehicle_id in ctx._pending_violation_vehicle_ids


def test_late_plate_enrichment_emits_realtime_update_event(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 11
    emitted = []
    ctx.on_violation = lambda event: emitted.append(event)

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
        confidence=0.93,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=ts,
    )
    ctx._save_late_plate_evidence_from_crop = lambda **_: "config/evidence_images/cam_test/lp.jpg"
    ctx._save_late_violation_evidence_from_vehicle_crop = (
        lambda **_: "config/evidence_images/cam_test/veh.jpg"
    )

    def _fake_update(*args, **kwargs):
        ids = kwargs.get("updated_violation_ids_out")
        if isinstance(ids, list):
            ids.append(101)
        return 1

    monkeypatch.setattr(camera_context_module, "update_pending_violation_plate", _fake_update)
    monkeypatch.setattr(
        camera_context_module,
        "query_violation_payloads_by_ids",
        lambda *args, **kwargs: [
            {
                "id": 101,
                "camera_id": "cam_test",
                "location": {
                    "road_name": "Road A",
                    "intersection": None,
                    "gps_lat": None,
                    "gps_lng": None,
                },
                "vehicle_id": vehicle_id,
                "vehicle_type": "car",
                "lane_id": 2,
                "violation": "wrong_lane",
                "image_path": "config/evidence_images/cam_test/veh.jpg",
                "image_url": None,
                "license_plate": "51A12345",
                "license_plate_status": "confirmed",
                "license_plate_confidence": 0.93,
                "license_plate_image_path": "config/evidence_images/cam_test/lp.jpg",
                "license_plate_image_url": None,
                "track_session_id": "cam_test-session",
                "timestamp": "2026-05-21T19:00:00+07:00",
            }
        ],
    )

    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.ones((20, 80, 3), dtype=np.uint8),
    )

    assert len(emitted) == 1
    assert emitted[0].id == 101
    assert emitted[0].license_plate == "51A12345"
    assert emitted[0].image_path == "config/evidence_images/cam_test/veh.jpg"


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
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 0),
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
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 0),
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


def test_late_plate_enrichment_skips_update_when_crop_quality_not_better(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 25

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
        has_committed_plate=True,
        best_plate_image_quality=9.5,
        best_plate_image_path="config/evidence_images/cam_test/old.jpg",
        best_violation_image_quality=99.0,
        best_violation_image_path="config/evidence_images/cam_test/evidence_v1.jpg",
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
    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_pending_violation_plate",
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 0),
    )

    # Crop nhỏ/mờ => score thấp hơn best_plate_image_quality hiện tại.
    ctx._attempt_late_plate_enrichment(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        plate_crop_bgr=np.zeros((8, 20, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0


def test_evidence_upgrade_independent_of_plate_enrichment(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 26

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
        has_committed_plate=True,
        best_plate_image_quality=8.6,
        best_plate_image_path="config/evidence_images/cam_test/plate_v1.jpg",
        best_violation_image_quality=0.0,
        best_violation_image_path="config/evidence_images/cam_test/evidence_v1.jpg",
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=80),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=7,
        dirty=False,
    )
    ctx._save_late_violation_evidence_from_vehicle_crop = (
        lambda **_: "config/evidence_images/cam_test/evidence_v2.jpg"
    )
    captured = {"calls": 0, "evidence_image_path": None, "license_plate_image_path": None}

    def _fake_evidence_update(*args, **kwargs):
        captured["calls"] += 1
        captured["evidence_image_path"] = kwargs.get("evidence_image_path")
        ids = kwargs.get("updated_violation_ids_out")
        if isinstance(ids, list):
            ids.append(301)
        return 1

    monkeypatch.setattr(
        camera_context_module,
        "update_violation_evidence_image_if_better",
        _fake_evidence_update,
    )
    monkeypatch.setattr(
        camera_context_module,
        "query_violation_payloads_by_ids",
        lambda *args, **kwargs: [],
    )

    high_detail_vehicle_crop = np.zeros((80, 160, 3), dtype=np.uint8)
    high_detail_vehicle_crop[:, ::2] = 255

    ctx._attempt_evidence_upgrade(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        vehicle_crop_bgr=high_detail_vehicle_crop,
    )

    assert captured["calls"] == 1
    assert captured["evidence_image_path"] == "config/evidence_images/cam_test/evidence_v2.jpg"


def test_evidence_upgrade_skips_when_quality_is_not_better(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 27

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=400),
        last_pending_ts=ts - timedelta(milliseconds=200),
        pending_count=1,
        last_lane_id=2,
        has_committed_plate=True,
        best_plate_image_quality=8.6,
        best_plate_image_path="config/evidence_images/cam_test/plate_v1.jpg",
        best_violation_image_quality=99.0,
        best_violation_image_path="config/evidence_images/cam_test/evidence_v1.jpg",
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=80),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=7,
        dirty=False,
    )
    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_violation_evidence_image_if_better",
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 0),
    )

    ctx._attempt_evidence_upgrade(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        vehicle_crop_bgr=np.ones((80, 160, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0


def test_evidence_upgrade_does_not_require_plate_confirmation(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 28
    emitted = []
    ctx.on_violation = lambda event: emitted.append(event)

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=300),
        last_pending_ts=ts - timedelta(milliseconds=120),
        pending_count=1,
        last_lane_id=2,
        has_committed_plate=False,
        best_violation_image_quality=0.0,
        best_violation_image_path=None,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=80),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=7,
        dirty=False,
    )
    ctx._save_late_violation_evidence_from_vehicle_crop = (
        lambda **_: "config/evidence_images/cam_test/evidence_v3.jpg"
    )

    captured = {"calls": 0}

    def _fake_evidence_update(*args, **kwargs):
        captured["calls"] += 1
        ids = kwargs.get("updated_violation_ids_out")
        if isinstance(ids, list):
            ids.append(501)
        return 1

    monkeypatch.setattr(
        camera_context_module,
        "update_violation_evidence_image_if_better",
        _fake_evidence_update,
    )
    monkeypatch.setattr(
        camera_context_module,
        "query_violation_payloads_by_ids",
        lambda *args, **kwargs: [
            {
                "id": 501,
                "camera_id": "cam_test",
                "location": {
                    "road_name": "Road A",
                    "intersection": None,
                    "gps_lat": None,
                    "gps_lng": None,
                },
                "vehicle_id": vehicle_id,
                "vehicle_type": "car",
                "lane_id": 2,
                "violation": "wrong_lane",
                "image_path": "config/evidence_images/cam_test/evidence_v3.jpg",
                "image_url": None,
                "license_plate": None,
                "license_plate_status": "pending",
                "license_plate_confidence": None,
                "license_plate_image_path": None,
                "license_plate_image_url": None,
                "track_session_id": "cam_test-session",
                "timestamp": "2026-05-21T19:00:00+07:00",
            }
        ],
    )

    high_detail_vehicle_crop = np.zeros((80, 160, 3), dtype=np.uint8)
    high_detail_vehicle_crop[:, ::2] = 255

    ctx._attempt_evidence_upgrade(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        vehicle_crop_bgr=high_detail_vehicle_crop,
    )

    assert captured["calls"] == 1
    assert len(emitted) == 1
    assert emitted[0].id == 501


def test_evidence_upgrade_rejects_dirty_track(monkeypatch) -> None:
    ctx = _build_context_for_late_plate_tests()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 29

    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(milliseconds=300),
        last_pending_ts=ts - timedelta(milliseconds=120),
        pending_count=1,
        last_lane_id=2,
        has_committed_plate=False,
        best_violation_image_quality=0.0,
        best_violation_image_path=None,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=1),
        last_seen_ts=ts - timedelta(milliseconds=80),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=7,
        dirty=True,
    )

    captured = {"calls": 0}
    monkeypatch.setattr(
        camera_context_module,
        "update_violation_evidence_image_if_better",
        lambda *args, **kwargs: (captured.__setitem__("calls", captured["calls"] + 1) or 1),
    )

    ctx._attempt_evidence_upgrade(
        vehicle_id=vehicle_id,
        ts_dt=ts,
        vehicle_crop_bgr=np.ones((80, 160, 3), dtype=np.uint8),
    )

    assert captured["calls"] == 0


def test_late_plate_enrichment_keeps_pending_without_plate_crop(monkeypatch) -> None:
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


def test_track_lost_without_crop_cancels_runtime_plate_state() -> None:
    class _Resolver:
        def __init__(self):
            self.discarded = []

        def discard(self, *, vehicle_id: int):
            self.discarded.append(int(vehicle_id))

    ctx = _build_context_for_late_plate_tests()
    resolver = _Resolver()
    ctx._license_plate_resolver = resolver
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    vehicle_id = 31
    ctx._pending_violation_plate_states[vehicle_id] = _PendingViolationPlateState(
        first_pending_ts=ts - timedelta(seconds=2),
        last_pending_ts=ts - timedelta(seconds=2),
        pending_count=1,
        last_lane_id=1,
        has_committed_plate=False,
    )
    ctx._pending_violation_vehicle_ids.add(vehicle_id)
    ctx._track_continuity_states[vehicle_id] = _TrackContinuityState(
        first_seen_ts=ts - timedelta(seconds=2),
        last_seen_ts=ts - timedelta(seconds=2),
        last_bbox_xyxy=[10.0, 10.0, 30.0, 30.0],
        observation_count=3,
        dirty=False,
    )

    ctx._update_late_plate_track_continuity(tracks=[], ts_dt=ts)

    assert vehicle_id not in ctx._pending_violation_vehicle_ids
    assert resolver.discarded == [vehicle_id]


def test_violation_event_with_confirmed_plate_still_registers_for_evidence_upgrade() -> None:
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
    ctx._create_violation_evidence = lambda *args, **kwargs: "config/evidence_images/cam_test/evidence_v1.jpg"
    ctx._create_license_plate_evidence = lambda *args, **kwargs: "config/evidence_images/cam_test/lp_v1.jpg"
    ctx._save_event_to_db = lambda event: setattr(event, "id", 3)
    ctx._license_plate_snapshot_for = lambda **_: LicensePlateSnapshot(
        license_plate="51A12345",
        status="confirmed",
        confidence=0.95,
        consensus_hits=3,
        attempt_count=6,
        confirmed_ts=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    ctx._crop_violation_evidence = lambda *args, **kwargs: np.ones((30, 60, 3), dtype=np.uint8)
    ctx._vehicle_evidence_quality_score = lambda *_args, **_kwargs: 1.0
    registered = []
    emitted = []
    ctx._register_pending_violation_for_late_plate = lambda **kwargs: registered.append(kwargs)
    ctx.stats = SimpleNamespace(update_realtime=lambda event: None)
    ctx.on_violation = lambda event: emitted.append(event)

    asyncio.run(
        ctx._handle_violations(
            violation_candidates=[
                (
                    103,
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
    assert emitted[0].license_plate_status == "confirmed"
    assert len(registered) == 1
    assert registered[0]["has_committed_plate"] is True


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

