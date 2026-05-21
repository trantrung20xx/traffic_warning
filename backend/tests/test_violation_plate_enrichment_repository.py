from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import create_engine_and_session
from app.db.repository import (
    insert_violation,
    query_violation_history,
    update_pending_violation_plate,
    update_violation_evidence_image_if_better,
)
from app.schemas.events import ViolationEvent, ViolationLocation


def _event(
    *,
    camera_id: str = "cam_01",
    vehicle_id: int = 10,
    track_session_id: str = "cam_01-session",
    license_plate: str | None = None,
    license_plate_status: str | None = "pending",
    license_plate_confidence: float | None = None,
    license_plate_image_path: str | None = None,
    image_path: str | None = None,
    ts: datetime | None = None,
) -> ViolationEvent:
    timestamp = ts or datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
    return ViolationEvent(
        camera_id=camera_id,
        location=ViolationLocation(road_name="Road A"),
        vehicle_id=vehicle_id,
        vehicle_type="car",
        lane_id=1,
        violation="wrong_lane",
        image_path=image_path,
        license_plate=license_plate,
        license_plate_status=license_plate_status,
        license_plate_confidence=license_plate_confidence,
        license_plate_image_path=license_plate_image_path,
        track_session_id=track_session_id,
        timestamp=timestamp.isoformat(),
    )


class ViolationPlateEnrichmentRepositoryTests(unittest.TestCase):
    @staticmethod
    def _create_session_factory():
        return create_engine_and_session(":memory:")

    def test_updates_pending_violation_when_keys_match(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event())
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="config/evidence_images/cam_01/plate.jpg",
                    min_confidence=0.8,
                    allowed_current_statuses=("pending", "unreadable"),
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 1)
        self.assertEqual(rows[0]["license_plate"], "51A12345")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")
        self.assertAlmostEqual(rows[0]["license_plate_confidence"], 0.9, places=3)

    def test_rejects_update_when_key_scope_does_not_match(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event(camera_id="cam_a", vehicle_id=10, track_session_id="session_a"))
                updated_camera = update_pending_violation_plate(
                    session,
                    camera_id="cam_b",
                    track_session_id="session_a",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                updated_session = update_pending_violation_plate(
                    session,
                    camera_id="cam_a",
                    track_session_id="session_b",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                updated_vehicle = update_pending_violation_plate(
                    session,
                    camera_id="cam_a",
                    track_session_id="session_a",
                    vehicle_id=999,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated_camera, 0)
        self.assertEqual(updated_session, 0)
        self.assertEqual(updated_vehicle, 0)
        self.assertEqual(rows[0]["license_plate_status"], "pending")

    def test_rejects_low_confidence_or_insufficient_status_scope(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event(license_plate_status="pending"))
                updated_low_conf = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.7,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                updated_wrong_status = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="candidate",
                    license_plate_confidence=0.95,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated_low_conf, 0)
        self.assertEqual(updated_wrong_status, 0)
        self.assertEqual(rows[0]["license_plate_status"], "pending")

    def test_does_not_overwrite_confirmed_violation_with_different_plate(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate="30H99876",
                        license_plate_status="confirmed",
                        license_plate_confidence=0.88,
                        license_plate_image_path="config/evidence_images/cam_01/original.jpg",
                    ),
                )
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.95,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 0)
        self.assertEqual(rows[0]["license_plate"], "30H99876")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")

    def test_respects_update_time_window(self) -> None:
        engine = None
        ts = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event(ts=ts))
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="a.jpg",
                    min_confidence=0.8,
                    violation_not_before_ts=ts + timedelta(seconds=1),
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 0)
        self.assertEqual(rows[0]["license_plate_status"], "pending")

    def test_backfills_missing_plate_image_for_confirmed_same_text(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate="51A12345",
                        license_plate_status="confirmed",
                        license_plate_confidence=0.83,
                        track_session_id="cam_01-session",
                    ),
                )
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.91,
                    license_plate_image_path="config/evidence_images/cam_01/late_lp.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 1)
        self.assertEqual(rows[0]["license_plate"], "51A12345")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")
        self.assertEqual(rows[0]["license_plate_image_path"], "config/evidence_images/cam_01/late_lp.jpg")
        self.assertAlmostEqual(rows[0]["license_plate_confidence"], 0.91, places=3)

    def test_replaces_plate_image_for_confirmed_same_text(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate="51A12345",
                        license_plate_status="confirmed",
                        license_plate_confidence=0.9,
                        track_session_id="cam_01-session",
                    ),
                )
                row = query_violation_history(session)[0]
                self.assertIsNone(row["license_plate_image_path"])

                # Lần 1: gắn ảnh đầu tiên.
                updated_first = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="config/evidence_images/cam_01/plate_v1.jpg",
                    min_confidence=0.8,
                )
                # Lần 2: thay bằng ảnh tốt hơn (gate chất lượng nằm ở tầng camera context).
                updated_second = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.9,
                    license_plate_image_path="config/evidence_images/cam_01/plate_v2.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated_first, 1)
        self.assertEqual(updated_second, 1)
        self.assertEqual(rows[0]["license_plate"], "51A12345")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")
        self.assertEqual(rows[0]["license_plate_image_path"], "config/evidence_images/cam_01/plate_v2.jpg")
        self.assertAlmostEqual(rows[0]["license_plate_confidence"], 0.9, places=3)

    def test_rejects_update_when_new_plate_image_is_missing(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event(license_plate_status="pending"))
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.88,
                    license_plate_image_path=None,
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 0)
        self.assertIsNone(rows[0]["license_plate"])
        self.assertEqual(rows[0]["license_plate_status"], "pending")
        self.assertIsNone(rows[0]["license_plate_image_path"])

    def test_payload_hides_confirmed_plate_without_crop_image(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate="51A12345",
                        license_plate_status="confirmed",
                        license_plate_confidence=0.93,
                        license_plate_image_path=None,
                    ),
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["license_plate"])
        self.assertEqual(rows[0]["license_plate_status"], "pending")

    def test_updates_pending_violation_with_uncertain_plate_candidate(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(session, _event(license_plate_status="pending"))
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="uncertain",
                    license_plate_confidence=0.91,
                    license_plate_image_path="config/evidence_images/cam_01/plate_uncertain.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 1)
        self.assertEqual(rows[0]["license_plate"], "51A12345")
        self.assertEqual(rows[0]["license_plate_status"], "uncertain")
        self.assertEqual(rows[0]["license_plate_image_path"], "config/evidence_images/cam_01/plate_uncertain.jpg")

    def test_uncertain_plate_candidate_does_not_overwrite_confirmed_different_plate(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate="30H99876",
                        license_plate_status="confirmed",
                        license_plate_confidence=0.88,
                        license_plate_image_path="config/evidence_images/cam_01/original.jpg",
                    ),
                )
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="uncertain",
                    license_plate_confidence=0.95,
                    license_plate_image_path="config/evidence_images/cam_01/uncertain.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 0)
        self.assertEqual(rows[0]["license_plate"], "30H99876")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")
        self.assertEqual(rows[0]["license_plate_image_path"], "config/evidence_images/cam_01/original.jpg")

    def test_updates_evidence_image_path_when_new_evidence_is_provided(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate_status="pending",
                        image_path="config/evidence_images/cam_01/evidence_old.jpg",
                    ),
                )
                updated = update_pending_violation_plate(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    license_plate="51A12345",
                    license_plate_status="confirmed",
                    license_plate_confidence=0.91,
                    license_plate_image_path="config/evidence_images/cam_01/plate_v2.jpg",
                    evidence_image_path="config/evidence_images/cam_01/evidence_new.jpg",
                    min_confidence=0.8,
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 1)
        self.assertEqual(rows[0]["license_plate"], "51A12345")
        self.assertEqual(rows[0]["license_plate_status"], "confirmed")
        self.assertEqual(rows[0]["license_plate_image_path"], "config/evidence_images/cam_01/plate_v2.jpg")
        self.assertEqual(rows[0]["image_path"], "config/evidence_images/cam_01/evidence_new.jpg")
        self.assertEqual(rows[0]["evidence_image_path"], "config/evidence_images/cam_01/evidence_new.jpg")
        self.assertEqual(rows[0]["evidence_image_url"], rows[0]["image_url"])

    def test_independent_evidence_update_does_not_require_plate_confirmation(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        license_plate=None,
                        license_plate_status="pending",
                        license_plate_image_path=None,
                        image_path="config/evidence_images/cam_01/evidence_old.jpg",
                    ),
                )
                updated = update_violation_evidence_image_if_better(
                    session,
                    camera_id="cam_01",
                    track_session_id="cam_01-session",
                    vehicle_id=10,
                    evidence_image_path="config/evidence_images/cam_01/evidence_newer.jpg",
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated, 1)
        self.assertEqual(rows[0]["image_path"], "config/evidence_images/cam_01/evidence_newer.jpg")
        self.assertEqual(rows[0]["license_plate_status"], "pending")
        self.assertIsNone(rows[0]["license_plate"])

    def test_independent_evidence_update_respects_scope_and_skips_same_path(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
            with session_factory() as session:
                insert_violation(
                    session,
                    _event(
                        camera_id="cam_a",
                        vehicle_id=10,
                        track_session_id="session_a",
                        image_path="config/evidence_images/cam_a/evidence_keep.jpg",
                    ),
                )
                updated_same = update_violation_evidence_image_if_better(
                    session,
                    camera_id="cam_a",
                    track_session_id="session_a",
                    vehicle_id=10,
                    evidence_image_path="config/evidence_images/cam_a/evidence_keep.jpg",
                )
                updated_camera = update_violation_evidence_image_if_better(
                    session,
                    camera_id="cam_b",
                    track_session_id="session_a",
                    vehicle_id=10,
                    evidence_image_path="config/evidence_images/cam_b/evidence_new.jpg",
                )
                updated_session = update_violation_evidence_image_if_better(
                    session,
                    camera_id="cam_a",
                    track_session_id="session_b",
                    vehicle_id=10,
                    evidence_image_path="config/evidence_images/cam_a/evidence_new.jpg",
                )
                updated_vehicle = update_violation_evidence_image_if_better(
                    session,
                    camera_id="cam_a",
                    track_session_id="session_a",
                    vehicle_id=999,
                    evidence_image_path="config/evidence_images/cam_a/evidence_new.jpg",
                )
                rows = query_violation_history(session)
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(updated_same, 0)
        self.assertEqual(updated_camera, 0)
        self.assertEqual(updated_session, 0)
        self.assertEqual(updated_vehicle, 0)
        self.assertEqual(rows[0]["image_path"], "config/evidence_images/cam_a/evidence_keep.jpg")


if __name__ == "__main__":
    unittest.main()
