from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.logic.license_plate_logic import LicensePlateTemporalResolver, normalize_license_plate_text


class LicensePlateLogicTests(unittest.TestCase):
    def test_normalize_license_plate_text(self) -> None:
        self.assertEqual(normalize_license_plate_text("51a-123.45"), "51A12345")
        self.assertEqual(normalize_license_plate_text("  30h  9999 "), "30H9999")
        self.assertIsNone(normalize_license_plate_text("..!"))
        self.assertIsNone(normalize_license_plate_text("AB12"))

    def test_temporal_voting_confirms_when_hits_are_enough(self) -> None:
        resolver = LicensePlateTemporalResolver(
            min_ocr_confidence=0.65,
            consensus_min_hits=2,
            max_attempts_before_unreadable=6,
            candidate_window_ms=5000,
        )
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

        resolver.observe_attempt(vehicle_id=1, ts=ts, raw_text="51A-123.45", confidence=0.83)
        snapshot_1 = resolver.snapshot_for(vehicle_id=1)
        self.assertEqual(snapshot_1.status, "pending")

        resolver.observe_attempt(vehicle_id=1, ts=ts + timedelta(milliseconds=500), raw_text="51A12345", confidence=0.87)
        snapshot_2 = resolver.snapshot_for(vehicle_id=1)
        self.assertEqual(snapshot_2.status, "confirmed")
        self.assertEqual(snapshot_2.license_plate, "51A12345")
        self.assertIsNotNone(snapshot_2.confidence)

    def test_conflicting_candidates_become_uncertain(self) -> None:
        resolver = LicensePlateTemporalResolver(
            min_ocr_confidence=0.65,
            consensus_min_hits=2,
            max_attempts_before_unreadable=6,
            candidate_window_ms=5000,
        )
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

        resolver.observe_attempt(vehicle_id=2, ts=ts, raw_text="51A-123.45", confidence=0.88)
        resolver.observe_attempt(vehicle_id=2, ts=ts + timedelta(milliseconds=400), raw_text="51A-128.45", confidence=0.9)
        snapshot = resolver.snapshot_for(vehicle_id=2)
        self.assertEqual(snapshot.status, "uncertain")
        self.assertIsNotNone(snapshot.license_plate)

    def test_competing_consensus_with_close_confidence_stays_uncertain(self) -> None:
        resolver = LicensePlateTemporalResolver(
            min_ocr_confidence=0.65,
            consensus_min_hits=2,
            max_attempts_before_unreadable=6,
            candidate_window_ms=5000,
        )
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

        resolver.observe_attempt(vehicle_id=4, ts=ts, raw_text="51A12345", confidence=0.91)
        resolver.observe_attempt(vehicle_id=4, ts=ts + timedelta(milliseconds=200), raw_text="51A12345", confidence=0.89)
        resolver.observe_attempt(vehicle_id=4, ts=ts + timedelta(milliseconds=400), raw_text="51A12845", confidence=0.88)
        resolver.observe_attempt(vehicle_id=4, ts=ts + timedelta(milliseconds=600), raw_text="51A12845", confidence=0.90)

        snapshot = resolver.snapshot_for(vehicle_id=4)

        self.assertEqual(snapshot.status, "uncertain")
        self.assertEqual(snapshot.consensus_hits, 2)

    def test_dominant_consensus_can_confirm_despite_weaker_competitor(self) -> None:
        resolver = LicensePlateTemporalResolver(
            min_ocr_confidence=0.65,
            consensus_min_hits=2,
            max_attempts_before_unreadable=6,
            candidate_window_ms=5000,
        )
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

        resolver.observe_attempt(vehicle_id=5, ts=ts, raw_text="51A12345", confidence=0.94)
        resolver.observe_attempt(vehicle_id=5, ts=ts + timedelta(milliseconds=200), raw_text="51A12345", confidence=0.92)
        resolver.observe_attempt(vehicle_id=5, ts=ts + timedelta(milliseconds=400), raw_text="51A12345", confidence=0.93)
        resolver.observe_attempt(vehicle_id=5, ts=ts + timedelta(milliseconds=600), raw_text="51A12845", confidence=0.88)
        resolver.observe_attempt(vehicle_id=5, ts=ts + timedelta(milliseconds=800), raw_text="51A12845", confidence=0.89)

        snapshot = resolver.snapshot_for(vehicle_id=5)

        self.assertEqual(snapshot.status, "confirmed")
        self.assertEqual(snapshot.license_plate, "51A12345")

    def test_unreadable_after_max_attempts_without_valid_read(self) -> None:
        resolver = LicensePlateTemporalResolver(
            min_ocr_confidence=0.65,
            consensus_min_hits=2,
            max_attempts_before_unreadable=3,
            candidate_window_ms=5000,
        )
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

        resolver.observe_attempt(vehicle_id=3, ts=ts, raw_text=None, confidence=None)
        resolver.observe_attempt(vehicle_id=3, ts=ts + timedelta(milliseconds=500), raw_text="", confidence=0.0)
        resolver.observe_attempt(vehicle_id=3, ts=ts + timedelta(milliseconds=1000), raw_text="...", confidence=0.2)
        snapshot = resolver.snapshot_for(vehicle_id=3)
        self.assertEqual(snapshot.status, "unreadable")
        self.assertIsNone(snapshot.license_plate)
        self.assertIsNone(snapshot.confidence)


if __name__ == "__main__":
    unittest.main()
