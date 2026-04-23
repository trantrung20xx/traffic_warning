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

    @staticmethod
    def _build_hybrid_logic(payload: dict, *, turn_region_min_hits: int = 2) -> ViolationLogic:
        lane_config = CameraLaneConfig.model_validate(payload)
        runtime_lane_config = denormalize_lane_config(lane_config)
        return ViolationLogic(
            runtime_lane_config.lanes,
            turn_corridors=runtime_lane_config.turn_corridors,
            exit_zones=runtime_lane_config.exit_zones,
            exit_lines=runtime_lane_config.exit_lines,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=turn_region_min_hits,
            turn_candidate_window_ms=500,
            turn_state_timeout_ms=3000,
        )

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

    def test_lane_assignment_prefers_lane_with_larger_bottom_overlap_for_wide_vehicle(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.52, 0.0], [0.52, 1.0], [0.0, 1.0]],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.40, 0.0], [1.0, 0.0], [1.0, 1.0], [0.40, 1.0]],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        lane_logic = LaneLogic(runtime_lane_config.lanes)

        # Điểm giữa đáy bbox nằm trong vùng overlap của cả 2 làn.
        # Cách cũ sẽ chọn lane 1 chỉ vì xuất hiện trước.
        # Cách mới chọn lane 2 vì đoạn đáy bbox nằm trong lane 2 dài hơn rõ rệt.
        bbox_xyxy = [35, 10, 65, 20]

        self.assertEqual(lane_logic.assign_lane_id_from_bbox_xyxy(bbox_xyxy), 2)

    def test_illegal_turn_fires_for_disallowed_maneuver_from_current_lane(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.95], [0.20, 0.95]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.10, 0.45], [0.45, 0.45], [0.45, 0.70], [0.10, 0.70]],
                        "commit_gate": [[0.20, 0.68], [0.45, 0.68], [0.45, 0.82], [0.20, 0.82]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.90, 0.45], [0.90, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.68], [0.90, 0.68], [0.90, 0.82], [0.55, 0.82]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            },
            turn_region_min_hits=2,
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
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.55, 0.72], [0.90, 0.72], [0.90, 0.95], [0.55, 0.95]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.10, 0.45], [0.45, 0.45], [0.45, 0.70], [0.10, 0.70]],
                        "commit_gate": [[0.20, 0.68], [0.45, 0.68], [0.45, 0.82], [0.20, 0.82]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.90, 0.45], [0.90, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.68], [0.90, 0.68], [0.90, 0.82], [0.55, 0.82]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            },
            turn_region_min_hits=2,
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
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.55, 0.72], [0.90, 0.72], [0.90, 0.95], [0.55, 0.95]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.10, 0.45], [0.45, 0.45], [0.45, 0.70], [0.10, 0.70]],
                        "commit_gate": [[0.20, 0.68], [0.45, 0.68], [0.45, 0.82], [0.20, 0.82]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.90, 0.45], [0.90, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.68], [0.90, 0.68], [0.90, 0.82], [0.55, 0.82]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            },
            turn_region_min_hits=2,
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

    def test_hybrid_turn_uses_source_lane_locked_before_lane_drift(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.18, 0.76], [0.48, 0.76], [0.48, 0.98], [0.18, 0.98]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_gate": [[0.25, 0.72], [0.48, 0.72], [0.48, 0.86], [0.25, 0.86]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.95, 0.45], [0.95, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.72], [0.82, 0.72], [0.82, 0.86], [0.55, 0.86]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2, 1],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        ts = datetime(2026, 4, 23, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=601,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 50, 70, 60],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=601,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[68, 62, 78, 78],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=601,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[25, 70, 35, 88],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=601,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[27, 72, 37, 90],
                ts=ts + timedelta(milliseconds=300),
            ),
            [],
        )

    def test_hybrid_turn_uses_source_lane_even_when_corridor_is_on_other_lane(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.60, 0.76], [0.92, 0.76], [0.92, 0.98], [0.60, 0.98]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_gate": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.86], [0.20, 0.86]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.95, 0.45], [0.95, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.72], [0.82, 0.72], [0.82, 0.86], [0.55, 0.86]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        ts = datetime(2026, 4, 23, 8, 10, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=602,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 50, 30, 60],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=602,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[24, 62, 34, 78],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=602,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 70, 75, 88],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=602,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[67, 72, 77, 90],
                ts=ts + timedelta(milliseconds=300),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

    def test_hybrid_turn_allows_lane_change_before_entering_new_approach_zone(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.60, 0.76], [0.92, 0.76], [0.92, 0.98], [0.60, 0.98]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_gate": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.86], [0.20, 0.86]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.95, 0.45], [0.95, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.72], [0.82, 0.72], [0.82, 0.86], [0.55, 0.86]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        ts = datetime(2026, 4, 23, 8, 20, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 25, 30, 35],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 25, 70, 35],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 50, 70, 60],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[68, 62, 78, 78],
                ts=ts + timedelta(milliseconds=300),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 70, 75, 88],
                ts=ts + timedelta(milliseconds=400),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=603,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[67, 72, 77, 90],
                ts=ts + timedelta(milliseconds=500),
            ),
            [],
        )

    def test_exit_line_confirms_turn_without_waiting_for_corridor_hits(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.60, 0.70], [0.92, 0.70], [0.92, 0.98], [0.60, 0.98]]
                },
                "exit_lines": {
                    "right": [[0.60, 0.80], [0.92, 0.80]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_gate": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.86], [0.20, 0.86]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.95, 0.45], [0.95, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.72], [0.82, 0.72], [0.82, 0.86], [0.55, 0.86]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            },
            turn_region_min_hits=3,
        )
        ts = datetime(2026, 4, 24, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=604,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 50, 30, 60],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=604,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[24, 62, 34, 78],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=604,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 74, 75, 79],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=604,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 77, 75, 83],
                ts=ts + timedelta(milliseconds=300),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=604,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 80, 75, 86],
                ts=ts + timedelta(milliseconds=400),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

    def test_commit_line_requires_stable_post_crossing(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_line": [[0.10, 0.74], [0.45, 0.74]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            }
        )
        ts = datetime(2026, 4, 24, 8, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=605,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 48, 30, 68],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=605,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 52, 30, 72],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=605,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 55, 30, 76],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )

        turn_state = logic._vehicle_states[605].turn_state
        self.assertEqual(turn_state.phase, "approach")

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=605,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 58, 30, 80],
                ts=ts + timedelta(milliseconds=300),
            ),
            [],
        )
        turn_state = logic._vehicle_states[605].turn_state
        self.assertEqual(turn_state.phase, "committed")

    def test_commit_line_ignores_single_frame_jump_and_reverse(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_line": [[0.10, 0.74], [0.45, 0.74]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            }
        )
        ts = datetime(2026, 4, 24, 8, 20, 0, tzinfo=timezone.utc)

        logic.update_and_maybe_generate_violation(
            vehicle_id=606,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[20, 48, 30, 68],
            ts=ts,
        )
        logic.update_and_maybe_generate_violation(
            vehicle_id=606,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[20, 52, 30, 72],
            ts=ts + timedelta(milliseconds=100),
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=606,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 57, 30, 80],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=606,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[20, 53, 30, 72],
                ts=ts + timedelta(milliseconds=300),
            ),
            [],
        )

        turn_state = logic._vehicle_states[606].turn_state
        self.assertNotEqual(turn_state.phase, "committed")

    def test_exit_line_ignores_crossing_after_long_tracking_gap(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.60, 0.70], [0.92, 0.70], [0.92, 0.98], [0.60, 0.98]]
                },
                "exit_lines": {
                    "right": [[0.60, 0.80], [0.92, 0.80]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.05, 0.45], [0.45, 0.45], [0.45, 0.70], [0.05, 0.70]],
                        "commit_gate": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.86], [0.20, 0.86]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1, 2],
                        "allowed_vehicle_types": ["car"],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "approach_zone": [[0.55, 0.45], [0.95, 0.45], [0.95, 0.70], [0.55, 0.70]],
                        "commit_gate": [[0.55, 0.72], [0.82, 0.72], [0.82, 0.86], [0.55, 0.86]],
                        "allowed_maneuvers": ["right"],
                        "allowed_lane_changes": [2],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            },
            turn_region_min_hits=3,
        )
        ts = datetime(2026, 4, 24, 8, 25, 0, tzinfo=timezone.utc)

        logic.update_and_maybe_generate_violation(
            vehicle_id=607,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[20, 50, 30, 60],
            ts=ts,
        )
        logic.update_and_maybe_generate_violation(
            vehicle_id=607,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[24, 62, 34, 78],
            ts=ts + timedelta(milliseconds=100),
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=607,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 77, 75, 83],
                ts=ts + timedelta(milliseconds=700),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=607,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[65, 80, 75, 86],
                ts=ts + timedelta(milliseconds=800),
            ),
            [],
        )

    def test_turn_corridor_can_confirm_with_progress_when_frame_count_is_low(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "right": [[0.20, 0.72], [0.55, 0.72], [0.55, 0.98], [0.20, 0.98]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.10, 0.45], [0.45, 0.45], [0.45, 0.70], [0.10, 0.70]],
                        "commit_gate": [[0.20, 0.68], [0.45, 0.68], [0.45, 0.82], [0.20, 0.82]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            },
            turn_region_min_hits=3,
        )
        ts = datetime(2026, 4, 24, 8, 30, 0, tzinfo=timezone.utc)

        logic.update_and_maybe_generate_violation(
            vehicle_id=608,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[20, 50, 30, 70],
            ts=ts,
        )
        logic.update_and_maybe_generate_violation(
            vehicle_id=608,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[25, 60, 35, 80],
            ts=ts + timedelta(milliseconds=100),
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=608,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[28, 63, 38, 83],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=608,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[38, 73, 48, 93],
                ts=ts + timedelta(milliseconds=420),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

if __name__ == "__main__":
    unittest.main()
