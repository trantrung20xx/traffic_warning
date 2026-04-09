from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import CameraLaneConfig
from app.logic.violation_logic import ViolationLogic


class LaneFeatureTests(unittest.TestCase):
    @staticmethod
    def _lane_payload(**overrides):
        payload = {
            "lane_id": 1,
            "polygon": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]],
            "allowed_maneuvers": ["straight"],
            "allowed_lane_changes": [1],
            "allowed_vehicle_types": ["car"],
        }
        payload.update(overrides)
        return payload

    def test_vehicle_type_not_allowed_emits_violation_once(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 1280,
                "frame_height": 720,
                "lanes": [self._lane_payload()],
            }
        )
        logic = ViolationLogic(lane_config.lanes, wrong_lane_min_duration_ms=1200, turn_region_min_hits=3)
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        first = logic.update_and_maybe_generate_violation(
            vehicle_id=101,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[200, 100, 260, 240],
            ts=ts,
        )
        second = logic.update_and_maybe_generate_violation(
            vehicle_id=101,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[204, 104, 264, 244],
            ts=ts + timedelta(milliseconds=200),
        )

        self.assertEqual(first, [{"lane_id": 1, "violation": "vehicle_type_not_allowed"}])
        self.assertEqual(second, [])

    def test_lane_config_rejects_empty_allowed_vehicle_types(self) -> None:
        with self.assertRaises(ValueError):
            CameraLaneConfig.model_validate(
                {
                    "camera_id": "cam_test",
                    "frame_width": 1280,
                    "frame_height": 720,
                    "lanes": [
                        {
                            **self._lane_payload(allowed_vehicle_types=[]),
                        }
                    ],
                }
            )

    def test_vehicle_type_violation_fires_for_disallowed_type(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 1280,
                "frame_height": 720,
                "lanes": [self._lane_payload()],
            }
        )
        logic = ViolationLogic(lane_config.lanes, wrong_lane_min_duration_ms=1200, turn_region_min_hits=3)
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        result = logic.update_and_maybe_generate_violation(
            vehicle_id=202,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[200, 100, 260, 240],
            ts=ts,
        )

        self.assertEqual(result, [{"lane_id": 1, "violation": "vehicle_type_not_allowed"}])

if __name__ == "__main__":
    unittest.main()
