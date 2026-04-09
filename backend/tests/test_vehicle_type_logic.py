from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.logic.vehicle_type_logic import TemporalVehicleTypeAssigner


class VehicleTypeLogicTests(unittest.TestCase):
    def test_confidence_weighted_majority_wins(self) -> None:
        assigner = TemporalVehicleTypeAssigner(history_window_ms=4000, history_size=12)
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)
        resolved = None
        for index, (vehicle_type, confidence) in enumerate(
            [("bus", 0.82), ("truck", 0.61), ("bus", 0.79), ("truck", 0.58)]
        ):
            resolved = assigner.resolve_type(
                vehicle_id=1,
                predicted_type=vehicle_type,
                confidence=confidence,
                ts=ts + timedelta(milliseconds=index * 200),
            )
        self.assertEqual(resolved, "bus")

    def test_recent_history_expires_outside_window(self) -> None:
        assigner = TemporalVehicleTypeAssigner(history_window_ms=1000, history_size=12)
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)
        assigner.resolve_type(vehicle_id=2, predicted_type="car", confidence=0.9, ts=ts)
        resolved = assigner.resolve_type(
            vehicle_id=2,
            predicted_type="truck",
            confidence=0.92,
            ts=ts + timedelta(milliseconds=1400),
        )
        self.assertEqual(resolved, "truck")


if __name__ == "__main__":
    unittest.main()
