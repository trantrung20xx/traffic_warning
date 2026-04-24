from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import create_engine_and_session
from app.core.config import AnalyticsChartConfig
from app.db.repository import insert_violation, query_dashboard_analytics, query_violation_history
from app.schemas.events import ViolationEvent, ViolationLocation


class RepositoryTimezoneTests(unittest.TestCase):
    @staticmethod
    def _create_session_factory():
        return create_engine_and_session(":memory:")

    def test_history_and_time_series_are_returned_in_vietnam_time(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()
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
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["timestamp"], "2026-04-10T16:30:00+07:00")

        self.assertEqual(dashboard["time_series_granularity"], "minute")
        self.assertEqual(len(dashboard["time_series"]), 1)
        self.assertEqual(dashboard["time_series"][0]["bucket"], "2026-04-10T16:30:00+07:00")
        self.assertEqual(dashboard["time_series"][0]["bucket_end"], "2026-04-10T16:31:00+07:00")

        self.assertEqual(len(dashboard["hourly_series"]), 1)
        self.assertEqual(dashboard["hourly_series"][0]["bucket"], "2026-04-10T16:00:00+07:00")

    def test_history_returns_all_rows_when_limit_is_not_provided(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()

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
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(len(history), 3)

    def test_time_series_fills_missing_minute_buckets_for_short_ranges(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()

            with session_factory() as session:
                insert_violation(
                    session,
                    ViolationEvent(
                        camera_id="cam_01",
                        location=ViolationLocation(road_name="Vo Van Kiet"),
                        vehicle_id=1,
                        vehicle_type="car",
                        lane_id=1,
                        violation="wrong_lane",
                        timestamp=datetime(2026, 4, 10, 9, 30, 15, tzinfo=timezone.utc).isoformat(),
                    ),
                )
                dashboard = query_dashboard_analytics(
                    session,
                    from_ts=datetime(2026, 4, 10, 9, 29, 0, tzinfo=timezone.utc).isoformat(),
                    to_ts=datetime(2026, 4, 10, 9, 31, 0, tzinfo=timezone.utc).isoformat(),
                )
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(dashboard["time_series_granularity"], "minute")
        self.assertEqual(
            [row["bucket"] for row in dashboard["time_series"]],
            [
                "2026-04-10T16:29:00+07:00",
                "2026-04-10T16:30:00+07:00",
                "2026-04-10T16:31:00+07:00",
            ],
        )
        self.assertEqual([row["total"] for row in dashboard["time_series"]], [0, 1, 0])

    def test_time_series_granularity_uses_chart_config_thresholds(self) -> None:
        engine = None
        try:
            engine, session_factory = self._create_session_factory()

            with session_factory() as session:
                insert_violation(
                    session,
                    ViolationEvent(
                        camera_id="cam_01",
                        location=ViolationLocation(road_name="Vo Van Kiet"),
                        vehicle_id=1,
                        vehicle_type="car",
                        lane_id=1,
                        violation="wrong_lane",
                        timestamp=datetime(2026, 4, 10, 9, 30, 15, tzinfo=timezone.utc).isoformat(),
                    ),
                )
                dashboard = query_dashboard_analytics(
                    session,
                    from_ts=datetime(2026, 4, 10, 9, 0, 0, tzinfo=timezone.utc).isoformat(),
                    to_ts=datetime(2026, 4, 11, 8, 0, 0, tzinfo=timezone.utc).isoformat(),
                    chart_config=AnalyticsChartConfig(minute_granularity_max_range_hours=12),
                )
        finally:
            if engine is not None:
                engine.dispose()

        self.assertEqual(dashboard["time_series_granularity"], "hour")


if __name__ == "__main__":
    unittest.main()
