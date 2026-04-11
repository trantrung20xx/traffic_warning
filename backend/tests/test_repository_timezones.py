from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import create_engine_and_session
from app.db.repository import insert_violation, query_dashboard_analytics, query_violation_history
from app.schemas.events import ViolationEvent, ViolationLocation


class RepositoryTimezoneTests(unittest.TestCase):
    def test_history_and_hourly_series_are_returned_in_vietnam_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, session_factory = create_engine_and_session(Path(tmp_dir) / "test.sqlite")
            event = ViolationEvent(
                camera_id="cam_01",
                location=ViolationLocation(road_name="Vo Van Kiet", intersection="Ham Nghi"),
                vehicle_id=88,
                vehicle_type="car",
                lane_id=2,
                violation="wrong_lane",
                timestamp=datetime(2026, 4, 10, 9, 30, 0, tzinfo=timezone.utc).isoformat(),
            )

            with session_factory() as session:
                insert_violation(session, event)
                history = query_violation_history(session)
                dashboard = query_dashboard_analytics(session)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["timestamp"], "2026-04-10T16:30:00+07:00")

        self.assertEqual(len(dashboard["hourly_series"]), 1)
        self.assertEqual(dashboard["hourly_series"][0]["bucket"], "2026-04-10T16:00:00+07:00")

    def test_history_returns_all_rows_when_limit_is_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, session_factory = create_engine_and_session(Path(tmp_dir) / "test.sqlite")

            with session_factory() as session:
                for vehicle_id in range(1, 4):
                    insert_violation(
                        session,
                        ViolationEvent(
                            camera_id="cam_01",
                            location=ViolationLocation(road_name="Vo Van Kiet"),
                            vehicle_id=vehicle_id,
                            vehicle_type="car",
                            lane_id=1,
                            violation="wrong_lane",
                            timestamp=datetime(2026, 4, 10, 9, vehicle_id, 0, tzinfo=timezone.utc).isoformat(),
                        ),
                    )
                history = query_violation_history(session)

        self.assertEqual(len(history), 3)


if __name__ == "__main__":
    unittest.main()
