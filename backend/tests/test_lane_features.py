from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import CameraLaneConfig, denormalize_lane_config
from app.logic.lane_logic import LaneLogic, TemporalLaneAssigner
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

    def test_wrong_lane_uses_stable_lane_transition_instead_of_primary_lane(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "allowed_lane_changes": [1],
                        "allowed_maneuvers": ["straight"],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "allowed_lane_changes": [2],
                        "allowed_maneuvers": ["right"],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            turn_candidate_window_ms=500,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=301,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[10, 10, 20, 20],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=301,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 10, 70, 20],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        result = logic.update_and_maybe_generate_violation(
            vehicle_id=301,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[60, 10, 70, 20],
            ts=ts + timedelta(milliseconds=1500),
        )

        self.assertEqual(result, [{"lane_id": 2, "violation": "wrong_lane"}])

    def test_overlap_prefers_current_stable_lane_when_vehicle_already_has_lane(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.7, 0.0], [0.7, 1.0], [0.0, 1.0]],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.3, 0.0], [1.0, 0.0], [1.0, 1.0], [0.3, 1.0]],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        lane_logic = LaneLogic(runtime_lane_config.lanes)
        temporal_assigner = TemporalLaneAssigner(min_majority_hits=1, switch_min_duration_ms=0)
        ts = datetime(2026, 4, 23, 8, 0, 0, tzinfo=timezone.utc)
        bbox_xyxy = [45, 10, 55, 20]

        self.assertEqual(lane_logic.assign_lane_id_from_bbox_xyxy(bbox_xyxy), 1)

        first_lane = lane_logic.assign_lane_id_from_bbox_xyxy(bbox_xyxy)
        resolved_first_lane = temporal_assigner.resolve_lane(vehicle_id=501, raw_lane_id=first_lane, ts=ts)
        self.assertEqual(resolved_first_lane, 1)
        self.assertEqual(temporal_assigner.get_stable_lane(vehicle_id=501), 1)

        raw_lane_with_preference = lane_logic.assign_lane_id_from_bbox_xyxy(
            bbox_xyxy,
            preferred_lane_id=temporal_assigner.get_stable_lane(vehicle_id=501),
        )
        resolved_lane = temporal_assigner.resolve_lane(
            vehicle_id=501,
            raw_lane_id=raw_lane_with_preference,
            ts=ts + timedelta(milliseconds=100),
        )

        self.assertEqual(raw_lane_with_preference, 1)
        self.assertEqual(resolved_lane, 1)

    def test_illegal_turn_fires_for_disallowed_maneuver_from_current_lane(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "turn_regions": {
                            "right": [[0.2, 0.6], [0.45, 0.6], [0.45, 0.95], [0.2, 0.95]],
                        },
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "turn_regions": {
                            "right": [[0.55, 0.6], [0.9, 0.6], [0.9, 0.95], [0.55, 0.95]],
                        },
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            turn_candidate_window_ms=500,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=401,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 50, 30, 70],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=401,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[25, 60, 35, 80],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=401,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[28, 62, 38, 82],
                ts=ts + timedelta(milliseconds=200),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

    def test_turn_from_allowed_lane_is_valid(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "turn_regions": {
                            "right": [[0.2, 0.6], [0.45, 0.6], [0.45, 0.95], [0.2, 0.95]],
                        },
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "turn_regions": {
                            "right": [[0.55, 0.6], [0.9, 0.6], [0.9, 0.95], [0.55, 0.95]],
                        },
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            turn_candidate_window_ms=500,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        first = logic.update_and_maybe_generate_violation(
            vehicle_id=402,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[60, 50, 70, 70],
            ts=ts,
        )
        second = logic.update_and_maybe_generate_violation(
            vehicle_id=402,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[65, 60, 75, 80],
            ts=ts + timedelta(milliseconds=100),
        )
        third = logic.update_and_maybe_generate_violation(
            vehicle_id=402,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[68, 62, 78, 82],
            ts=ts + timedelta(milliseconds=200),
        )

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(third, [])

    def test_lane_change_to_allowed_turn_lane_before_commit_is_valid(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "turn_regions": {
                            "right": [[0.2, 0.6], [0.45, 0.6], [0.45, 0.95], [0.2, 0.95]],
                        },
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "turn_regions": {
                            "right": [[0.55, 0.6], [0.9, 0.6], [0.9, 0.95], [0.55, 0.95]],
                        },
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            turn_candidate_window_ms=500,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=403,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 40, 30, 60],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=403,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 40, 70, 60],
                ts=ts + timedelta(milliseconds=150),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=403,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[62, 58, 72, 78],
                ts=ts + timedelta(milliseconds=250),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=403,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 60, 75, 80],
                ts=ts + timedelta(milliseconds=350),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=403,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[68, 62, 78, 82],
                ts=ts + timedelta(milliseconds=450),
            ),
            [],
        )

if __name__ == "__main__":
    unittest.main()
