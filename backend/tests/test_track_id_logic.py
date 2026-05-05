from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.logic.track_id_logic import StableTrackIdAssigner
from app.tracking.tracker import Track


class StableTrackIdLogicTests(unittest.TestCase):
    def _track(
        self,
        *,
        raw_id: int,
        vehicle_type: str,
        bbox_xyxy: list[float],
        confidence: float = 0.9,
    ) -> Track:
        return Track(
            vehicle_id=raw_id,
            vehicle_type=vehicle_type,
            bbox_xyxy=bbox_xyxy,
            confidence=confidence,
        )

    def test_same_raw_track_keeps_stable_id_when_vehicle_type_changes(self) -> None:
        assigner = StableTrackIdAssigner()
        ts = datetime(2026, 5, 5, 8, 0, 0, tzinfo=timezone.utc)

        first = assigner.assign(
            raw_tracks=[
                self._track(raw_id=17, vehicle_type="car", bbox_xyxy=[10.0, 10.0, 90.0, 90.0]),
            ],
            ts=ts,
        )
        second = assigner.assign(
            raw_tracks=[
                self._track(raw_id=17, vehicle_type="truck", bbox_xyxy=[12.0, 11.0, 92.0, 91.0]),
            ],
            ts=ts + timedelta(milliseconds=100),
        )

        self.assertEqual(first[0].vehicle_id, 1)
        self.assertEqual(second[0].vehicle_id, 1)
        self.assertEqual(second[0].vehicle_type, "truck")

    def test_rebind_keeps_stable_id_when_raw_id_and_vehicle_type_change(self) -> None:
        assigner = StableTrackIdAssigner()
        ts = datetime(2026, 5, 5, 8, 15, 0, tzinfo=timezone.utc)

        first = assigner.assign(
            raw_tracks=[
                self._track(raw_id=31, vehicle_type="car", bbox_xyxy=[100.0, 100.0, 200.0, 200.0]),
            ],
            ts=ts,
        )
        second = assigner.assign(
            raw_tracks=[
                self._track(raw_id=902, vehicle_type="bus", bbox_xyxy=[104.0, 102.0, 204.0, 202.0]),
            ],
            ts=ts + timedelta(milliseconds=120),
        )

        self.assertEqual(first[0].vehicle_id, 1)
        self.assertEqual(second[0].vehicle_id, 1)
        self.assertEqual(second[0].vehicle_type, "bus")

    def test_rebind_prefers_closer_geometry_over_matching_vehicle_type(self) -> None:
        assigner = StableTrackIdAssigner()
        ts = datetime(2026, 5, 5, 8, 30, 0, tzinfo=timezone.utc)

        initial = assigner.assign(
            raw_tracks=[
                self._track(raw_id=1, vehicle_type="car", bbox_xyxy=[0.0, 0.0, 100.0, 100.0]),
                self._track(raw_id=2, vehicle_type="truck", bbox_xyxy=[5.0, 0.0, 105.0, 100.0]),
            ],
            ts=ts,
        )
        rebound = assigner.assign(
            raw_tracks=[
                self._track(raw_id=99, vehicle_type="truck", bbox_xyxy=[2.0, 0.0, 102.0, 100.0]),
            ],
            ts=ts + timedelta(milliseconds=100),
        )

        self.assertEqual([track.vehicle_id for track in initial], [1, 2])
        self.assertEqual(rebound[0].vehicle_id, 1)
        self.assertEqual(rebound[0].vehicle_type, "truck")


if __name__ == "__main__":
    unittest.main()
