from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import CameraLaneConfig, denormalize_lane_config
from app.logic.lane_logic import LaneLogic, TemporalLaneAssigner
from app.logic.geometry_validator import validate_lane_geometry
from app.logic.violation_logic import ViolationLogic


class LaneFeatureTests(unittest.TestCase):
    @staticmethod
    def _normalize_violation_rows(rows: list[dict]) -> list[dict]:
        return [
            {
                "lane_id": row.get("lane_id"),
                "violation": row.get("violation"),
            }
            for row in rows
        ]

    def assertViolationsEqual(self, actual: list[dict], expected: list[dict]) -> None:
        self.assertEqual(self._normalize_violation_rows(actual), expected)

    @staticmethod
    def _promote_global_turn_geometry(payload: dict) -> dict:
        """
        Chuẩn hóa fixture test cũ về lane-centric maneuver geometry.
        Runtime đã bỏ legacy global collections; helper này chỉ dùng trong test.
        """
        turn_corridors = payload.get("turn_corridors")
        exit_zones = payload.get("exit_zones")
        exit_lines = payload.get("exit_lines")
        if not isinstance(turn_corridors, dict):
            turn_corridors = {}
        if not isinstance(exit_zones, dict):
            exit_zones = {}
        if not isinstance(exit_lines, dict):
            exit_lines = {}
        if not (turn_corridors or exit_zones or exit_lines):
            return payload

        normalized = {
            key: value
            for key, value in payload.items()
            if key not in {"turn_corridors", "exit_zones", "exit_lines"}
        }
        lanes: list[dict] = []
        for lane in normalized.get("lanes", []):
            if not isinstance(lane, dict):
                continue
            lane_payload = dict(lane)
            allowed = set(lane_payload.get("allowed_maneuvers") or [])
            maneuvers = {
                key: dict(value)
                for key, value in (lane_payload.get("maneuvers") or {}).items()
                if isinstance(value, dict)
            }
            maneuver_keys = set(maneuvers.keys()) | set(allowed) | set(turn_corridors) | set(exit_zones) | set(exit_lines)
            for maneuver in maneuver_keys:
                cfg = dict(maneuvers.get(maneuver) or {})
                cfg.setdefault("enabled", True)
                cfg.setdefault("allowed", maneuver in allowed)
                if maneuver in turn_corridors and "turn_corridor" not in cfg:
                    cfg["turn_corridor"] = turn_corridors[maneuver]
                if maneuver in exit_zones and "exit_zone" not in cfg:
                    cfg["exit_zone"] = exit_zones[maneuver]
                if maneuver in exit_lines and "exit_line" not in cfg:
                    cfg["exit_line"] = exit_lines[maneuver]
                maneuvers[maneuver] = cfg
            if maneuvers:
                lane_payload["maneuvers"] = maneuvers
            lanes.append(lane_payload)
        normalized["lanes"] = lanes
        return normalized

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
    def _build_hybrid_logic(
        payload: dict,
        *,
        turn_region_min_hits: int = 2,
        **logic_overrides,
    ) -> ViolationLogic:
        lane_config = CameraLaneConfig.model_validate(LaneFeatureTests._promote_global_turn_geometry(payload))
        runtime_lane_config = denormalize_lane_config(lane_config)
        kwargs = {
            "wrong_lane_min_duration_ms": 1200,
            "turn_region_min_hits": turn_region_min_hits,
            "turn_state_timeout_ms": 3000,
        }
        kwargs.update(logic_overrides)
        return ViolationLogic(
            runtime_lane_config.lanes,
            **kwargs,
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
        logic = ViolationLogic(
            lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            vehicle_type_min_duration_ms=0,
        )
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

        self.assertViolationsEqual(first, [{"lane_id": 1, "violation": "vehicle_type_not_allowed"}])
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

    def test_disabled_maneuver_forces_allowed_false(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    self._lane_payload(
                        maneuvers={
                            "straight": {"enabled": True, "allowed": True},
                            "right": {"enabled": False, "allowed": True},
                        }
                    )
                ],
            }
        )

        self.assertFalse(lane_config.lanes[0].maneuvers["right"].enabled)
        self.assertFalse(lane_config.lanes[0].maneuvers["right"].allowed)

    def test_disabled_maneuver_geometry_is_not_scored(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "approach_zone": [[0.10, 0.45], [0.45, 0.45], [0.45, 0.70], [0.10, 0.70]],
                        "commit_gate": [[0.20, 0.68], [0.45, 0.68], [0.45, 0.82], [0.20, 0.82]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                        "maneuvers": {
                            "straight": {"enabled": True, "allowed": True},
                            "right": {
                                "enabled": False,
                                "allowed": False,
                                "turn_corridor": [[0.20, 0.72], [0.45, 0.72], [0.45, 0.95], [0.20, 0.95]],
                            },
                        },
                    }
                ],
            },
            turn_region_min_hits=2,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        for offset_ms, bbox in (
            (0, [20, 50, 30, 70]),
            (100, [25, 60, 35, 80]),
            (200, [28, 62, 38, 82]),
        ):
            self.assertEqual(
                logic.update_and_maybe_generate_violation(
                    vehicle_id=402,
                    vehicle_type="car",
                    lane_id=1,
                    bbox_xyxy=bbox,
                    ts=ts + timedelta(milliseconds=offset_ms),
                ),
                [],
            )

        turn_state = logic._vehicle_states[402].turn_state
        self.assertIsNone(turn_state.confirmed_maneuver)
        self.assertNotIn("right", turn_state.evidences)

    def test_vehicle_type_violation_fires_for_disallowed_type(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 1280,
                "frame_height": 720,
                "lanes": [self._lane_payload()],
            }
        )
        logic = ViolationLogic(
            lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
            vehicle_type_min_duration_ms=0,
        )
        ts = datetime(2026, 4, 9, 8, 0, 0, tzinfo=timezone.utc)

        result = logic.update_and_maybe_generate_violation(
            vehicle_id=202,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[200, 100, 260, 240],
            ts=ts,
        )

        self.assertViolationsEqual(result, [{"lane_id": 1, "violation": "vehicle_type_not_allowed"}])

    def test_vehicle_type_not_allowed_requires_persistence_with_default_threshold(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 1280,
                "frame_height": 720,
                "lanes": [self._lane_payload()],
            }
        )
        logic = ViolationLogic(lane_config.lanes, wrong_lane_min_duration_ms=1200, turn_region_min_hits=3)
        ts = datetime(2026, 4, 24, 9, 0, 0, tzinfo=timezone.utc)

        first = logic.update_and_maybe_generate_violation(
            vehicle_id=203,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[200, 100, 260, 240],
            ts=ts,
        )
        second = logic.update_and_maybe_generate_violation(
            vehicle_id=203,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[202, 102, 262, 242],
            ts=ts + timedelta(milliseconds=500),
        )
        third = logic.update_and_maybe_generate_violation(
            vehicle_id=203,
            vehicle_type="truck",
            lane_id=1,
            bbox_xyxy=[205, 105, 265, 245],
            ts=ts + timedelta(milliseconds=1000),
        )

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertViolationsEqual(third, [{"lane_id": 1, "violation": "vehicle_type_not_allowed"}])

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

        self.assertViolationsEqual(result, [{"lane_id": 2, "violation": "wrong_lane"}])

    def test_wrong_lane_does_not_fire_when_vehicle_corrects_into_allowed_lane(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 2,
                        "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                        "allowed_lane_changes": [2],
                        "allowed_maneuvers": ["straight"],
                        "allowed_vehicle_types": ["bus", "truck"],
                    },
                    {
                        "lane_id": 3,
                        "polygon": [[0.51, 0.0], [1.0, 0.0], [1.0, 1.0], [0.51, 1.0]],
                        "allowed_lane_changes": [3],
                        "allowed_maneuvers": ["straight"],
                        "allowed_vehicle_types": ["car", "bus", "truck"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=1200,
            turn_region_min_hits=3,
        )
        ts = datetime(2026, 4, 24, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=302,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[10, 10, 20, 20],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=302,
                vehicle_type="car",
                lane_id=3,
                bbox_xyxy=[60, 10, 70, 20],
                ts=ts + timedelta(milliseconds=200),
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=302,
                vehicle_type="car",
                lane_id=3,
                bbox_xyxy=[60, 10, 70, 20],
                ts=ts + timedelta(milliseconds=1600),
            ),
            [],
        )

    def test_wrong_lane_rearms_after_lifecycle_window_and_increments_event_window(self) -> None:
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
                        "allowed_maneuvers": ["straight"],
                        "allowed_vehicle_types": ["car"],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        logic = ViolationLogic(
            runtime_lane_config.lanes,
            wrong_lane_min_duration_ms=600,
            turn_region_min_hits=2,
            violation_rearm_window_ms=500,
        )
        ts = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=303,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[10, 10, 20, 20],
                ts=ts,
            ),
            [],
        )
        self.assertEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=303,
                vehicle_type="car",
                lane_id=2,
                bbox_xyxy=[60, 10, 70, 20],
                ts=ts + timedelta(milliseconds=100),
            ),
            [],
        )
        first_emit = logic.update_and_maybe_generate_violation(
            vehicle_id=303,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[60, 10, 70, 20],
            ts=ts + timedelta(milliseconds=800),
        )
        second_emit = logic.update_and_maybe_generate_violation(
            vehicle_id=303,
            vehicle_type="car",
            lane_id=2,
            bbox_xyxy=[60, 10, 70, 20],
            ts=ts + timedelta(milliseconds=1400),
        )

        self.assertViolationsEqual(first_emit, [{"lane_id": 2, "violation": "wrong_lane"}])
        self.assertViolationsEqual(second_emit, [{"lane_id": 2, "violation": "wrong_lane"}])

        lifecycle = logic._vehicle_states[303].violation_lifecycles["wrong_lane:1->2"]
        self.assertEqual(lifecycle.event_window_id, 2)
        self.assertIsNotNone(lifecycle.emitted_ts)

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

    def test_lane_assignment_keeps_preferred_lane_when_overlap_is_close(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            {
                "camera_id": "cam_test",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [0.66, 0.0], [0.66, 1.0], [0.0, 1.0]],
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.54, 0.0], [1.0, 0.0], [1.0, 1.0], [0.54, 1.0]],
                    },
                ],
            }
        )
        runtime_lane_config = denormalize_lane_config(lane_config)
        lane_logic = LaneLogic(runtime_lane_config.lanes)

        # Bbox nằm ở vùng chồng lấn: overlap lane 2 lớn hơn một ít.
        # Khi đã có lane ổn định trước đó, phải giữ lane preferred để tránh lane drift.
        bbox_xyxy = [50, 10, 74, 20]

        self.assertEqual(lane_logic.assign_lane_id_from_bbox_xyxy(bbox_xyxy), 2)
        self.assertEqual(
            lane_logic.assign_lane_id_from_bbox_xyxy(bbox_xyxy, preferred_lane_id=1),
            1,
        )

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
        self.assertViolationsEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=401,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[28, 62, 38, 82],
                ts=ts + timedelta(milliseconds=200),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

    def test_illegal_turn_dedup_and_rearm_follow_event_lifecycle(self) -> None:
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
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            },
            turn_region_min_hits=2,
            turn_state_timeout_ms=800,
            violation_rearm_window_ms=700,
        )
        ts = datetime(2026, 4, 24, 10, 10, 0, tzinfo=timezone.utc)

        seq_1 = [
            (0, [20, 50, 30, 70]),
            (100, [25, 60, 35, 80]),
            (200, [28, 62, 38, 82]),
        ]
        emitted_first: list[dict] = []
        for offset_ms, bbox in seq_1:
            emitted_first.extend(
                logic.update_and_maybe_generate_violation(
                    vehicle_id=404,
                    vehicle_type="car",
                    lane_id=1,
                    bbox_xyxy=bbox,
                    ts=ts + timedelta(milliseconds=offset_ms),
                )
            )

        self.assertIn(
            {"lane_id": 1, "violation": "turn_right_not_allowed"},
            self._normalize_violation_rows(emitted_first),
        )

        # Cùng lifecycle, thêm frame overlap/corridor tiếp theo không được emit lại.
        no_duplicate = logic.update_and_maybe_generate_violation(
            vehicle_id=404,
            vehicle_type="car",
            lane_id=1,
            bbox_xyxy=[30, 64, 40, 84],
            ts=ts + timedelta(milliseconds=260),
        )
        self.assertEqual(no_duplicate, [])

        seq_2 = [
            (2600, [21, 50, 31, 70]),
            (2700, [26, 60, 36, 80]),
            (2800, [29, 62, 39, 82]),
        ]
        emitted_second: list[dict] = []
        for offset_ms, bbox in seq_2:
            emitted_second.extend(
                logic.update_and_maybe_generate_violation(
                    vehicle_id=404,
                    vehicle_type="car",
                    lane_id=1,
                    bbox_xyxy=bbox,
                    ts=ts + timedelta(milliseconds=offset_ms),
                )
            )

        self.assertIn(
            {"lane_id": 1, "violation": "turn_right_not_allowed"},
            self._normalize_violation_rows(emitted_second),
        )

        lifecycle = logic._vehicle_states[404].violation_lifecycles[
            "turn_right_not_allowed:lane_1:maneuver_right"
        ]
        self.assertEqual(lifecycle.event_window_id, 2)
        self.assertEqual(lifecycle.phase, "active")

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
        self.assertViolationsEqual(
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
        self.assertViolationsEqual(
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
        self.assertViolationsEqual(
            logic.update_and_maybe_generate_violation(
                vehicle_id=608,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=[38, 73, 48, 93],
                ts=ts + timedelta(milliseconds=420),
            ),
            [{"lane_id": 1, "violation": "turn_right_not_allowed"}],
        )

    def test_u_turn_is_detected_with_opposite_direction_evidence(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_uturn",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "left": [[0.10, 0.70], [0.32, 0.70], [0.32, 0.95], [0.10, 0.95]],
                    "u_turn": [[0.35, 0.60], [0.70, 0.60], [0.70, 0.92], [0.35, 0.92]],
                },
                "exit_lines": {
                    "left": [[0.10, 0.62], [0.30, 0.62]],
                    "u_turn": [[0.40, 0.62], [0.64, 0.62]],
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.20, 0.10], [0.55, 0.10], [0.55, 0.95], [0.20, 0.95]],
                        "approach_zone": [[0.22, 0.40], [0.52, 0.40], [0.52, 0.68], [0.22, 0.68]],
                        "commit_gate": [[0.25, 0.66], [0.52, 0.66], [0.52, 0.80], [0.25, 0.80]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            },
            turn_region_min_hits=2,
        )
        ts = datetime(2026, 4, 24, 9, 0, 0, tzinfo=timezone.utc)

        frames = [
            [28, 45, 38, 60],  # approach
            [30, 58, 40, 74],  # commit
            [40, 66, 50, 84],  # inside u_turn corridor
            [46, 62, 56, 80],  # start bending back
            [46, 52, 56, 70],  # heading reversed toward top
            [43, 46, 53, 64],  # cross u_turn exit line
        ]

        emitted: list[dict] = []
        for idx, bbox in enumerate(frames):
            result = logic.update_and_maybe_generate_violation(
                vehicle_id=701,
                vehicle_type="car",
                lane_id=1,
                bbox_xyxy=bbox,
                ts=ts + timedelta(milliseconds=idx * 120),
            )
            emitted.extend(result)

        self.assertIn(
            {"lane_id": 1, "violation": "turn_u_turn_not_allowed"},
            self._normalize_violation_rows(emitted),
        )

    def test_turn_reject_reason_is_recorded_for_rejected_u_turn_candidate(self) -> None:
        logic = self._build_hybrid_logic(
            {
                "camera_id": "cam_reject",
                "frame_width": 100,
                "frame_height": 100,
                "turn_corridors": {
                    "u_turn": [[0.36, 0.64], [0.64, 0.64], [0.64, 0.96], [0.36, 0.96]]
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.20, 0.10], [0.80, 0.10], [0.80, 0.98], [0.20, 0.98]],
                        "approach_zone": [[0.30, 0.42], [0.70, 0.42], [0.70, 0.68], [0.30, 0.68]],
                        "commit_gate": [[0.35, 0.62], [0.65, 0.62], [0.65, 0.78], [0.35, 0.78]],
                        "allowed_maneuvers": ["straight"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
            },
            turn_region_min_hits=2,
        )
        ts = datetime(2026, 4, 24, 9, 15, 0, tzinfo=timezone.utc)

        frames = [
            [42, 45, 52, 62],  # approach
            [42, 58, 52, 74],  # commit
            [42, 66, 52, 82],  # vào vùng u_turn nhưng vẫn đi thẳng xuống
            [42, 72, 52, 90],  # tiếp tục đi thẳng
        ]

        emitted: list[dict] = []
        for idx, bbox in enumerate(frames):
            emitted.extend(
                logic.update_and_maybe_generate_violation(
                    vehicle_id=702,
                    vehicle_type="car",
                    lane_id=1,
                    bbox_xyxy=bbox,
                    ts=ts + timedelta(milliseconds=idx * 120),
                )
            )

        self.assertEqual(emitted, [])
        turn_state = logic._vehicle_states[702].turn_state
        self.assertEqual(turn_state.phase, "committed")
        self.assertEqual(
            turn_state.last_reject_reasons.get("u_turn"),
            "u_turn_heading_change_too_small",
        )

    def test_geometry_validator_reports_semantic_issues(self) -> None:
        lane_config = CameraLaneConfig.model_validate(
            self._promote_global_turn_geometry(
                {
                "camera_id": "cam_semantic",
                "frame_width": 1280,
                "frame_height": 720,
                "turn_corridors": {
                    "left": [[0.10, 0.70], [0.18, 0.70], [0.18, 0.74], [0.10, 0.74]],
                    "u_turn": [[0.11, 0.70], [0.19, 0.70], [0.19, 0.74], [0.11, 0.74]],
                },
                "exit_lines": {
                    "left": [[0.65, 0.65], [0.75, 0.65]],
                },
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.20, 0.20], [0.40, 0.20], [0.40, 0.90], [0.20, 0.90]],
                        "approach_zone": [[0.60, 0.40], [0.78, 0.40], [0.78, 0.56], [0.60, 0.56]],
                        "commit_line": [[0.60, 0.30], [0.78, 0.30]],
                        "allowed_maneuvers": ["left"],
                        "allowed_lane_changes": [1],
                        "allowed_vehicle_types": ["car"],
                    }
                ],
                }
            )
        )

        issues = validate_lane_geometry(lane_config)
        issue_codes = {issue["code"] for issue in issues}

        self.assertIn("COMMIT_LINE_OUTSIDE_LANE", issue_codes)
        self.assertIn("APPROACH_ZONE_MISALIGNED", issue_codes)
        self.assertTrue(
            "UTURN_OVERLAP_HIGH" in issue_codes or "PATH_OVERLAP_AMBIGUOUS" in issue_codes
        )

if __name__ == "__main__":
    unittest.main()
