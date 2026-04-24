from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import WebSocket
from app.core.background_images import (
    delete_background_image,
    get_background_image_path,
    save_background_image,
)
from app.core.evidence_images import delete_evidence_images_for_camera, resolve_evidence_image_path
from app.core.config import (
    CameraLaneConfig,
    denormalize_lane_config,
    delete_lane_config_for_camera,
    load_app_config,
    load_cameras,
    load_lane_config_for_camera,
    save_cameras,
    save_lane_config_for_camera,
    validate_no_shared_lanes_across_cameras,
)
from app.db.repository import query_dashboard_analytics, query_violation_history
from app.db.database import create_engine_and_session
from app.managers.camera_context import CameraContext
from app.logic.geometry_validator import validate_lane_geometry
from app.schemas.camera import CameraConfig
from app.schemas.events import TrackMessage, ViolationEvent


class CameraManager:
    """Quản lý danh sách camera, context runtime và các kênh realtime toàn hệ thống."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_app_config(repo_root)

        validate_no_shared_lanes_across_cameras(repo_root)
        self.cameras: list[CameraConfig] = load_cameras(repo_root)

        self._engine, self._SessionLocal = create_engine_and_session(self.cfg.db_path)

        self._contexts: dict[str, CameraContext] = {}
        self._stop_event = asyncio.Event()
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

        # Danh sách queue và websocket đang đăng ký nhận dữ liệu realtime.
        self._track_listeners: set[asyncio.Queue[TrackMessage | None]] = set()
        self._violation_listeners: set[asyncio.Queue[ViolationEvent | None]] = set()
        self._track_websockets: set[WebSocket] = set()
        self._violation_websockets: set[WebSocket] = set()

    @property
    def session_factory(self):
        return self._SessionLocal

    def list_cameras(self) -> list[dict]:
        rows = []
        for cam in self.cameras:
            rows.append(
                {
                    "camera_id": cam.camera_id,
                    "rtsp_url": cam.rtsp_url,
                    "camera_type": cam.camera_type,
                    "view_direction": cam.view_direction,
                    "frame_width": cam.frame_width,
                    "frame_height": cam.frame_height,
                    "location": {
                        "road_name": cam.location.road_name,
                        "intersection": cam.location.intersection_name,
                        "gps_lat": cam.location.gps_lat,
                        "gps_lng": cam.location.gps_lng,
                    },
                    "monitored_lanes": cam.monitored_lanes,
                }
            )
        return rows

    def get_camera_detail(self, camera_id: str) -> dict:
        cam = next((item for item in self.cameras if item.camera_id == camera_id), None)
        if cam is None:
            raise KeyError(camera_id)
        lane_cfg = load_lane_config_for_camera(self.repo_root, camera_id)
        validation = validate_lane_geometry(lane_cfg)
        return {
            "camera": {
                "camera_id": cam.camera_id,
                "rtsp_url": cam.rtsp_url,
                "camera_type": cam.camera_type,
                "view_direction": cam.view_direction,
                "frame_width": cam.frame_width,
                "frame_height": cam.frame_height,
                "location": {
                    "road_name": cam.location.road_name,
                    "intersection_name": cam.location.intersection_name,
                    "gps_lat": cam.location.gps_lat,
                    "gps_lng": cam.location.gps_lng,
                },
                "monitored_lanes": cam.monitored_lanes,
            },
            "lane_config": lane_cfg.model_dump(mode="json", exclude_none=True),
            "runtime_applied": camera_id in self._contexts,
            "has_background_image": self.has_background_image(camera_id),
            "config_validation": validation,
            "ui": self._ui_payload(),
        }

    def upsert_camera(self, camera_config: CameraConfig, lane_config: CameraLaneConfig) -> dict:
        if camera_config.camera_id != lane_config.camera_id:
            raise ValueError("camera_id mismatch between camera_config and lane_config")
        lane_ids = [lane.lane_id for lane in lane_config.lanes]
        if len(set(lane_ids)) != len(lane_ids):
            raise ValueError("lane_config contains duplicate lane_id values")
        if sorted(camera_config.monitored_lanes) != sorted(lane_ids):
            raise ValueError("monitored_lanes must match lane_config lane_id values")

        next_cameras = [cam for cam in self.cameras if cam.camera_id != camera_config.camera_id]
        next_cameras.append(camera_config)
        next_cameras.sort(key=lambda cam: cam.camera_id)

        save_cameras(self.repo_root, next_cameras)
        save_lane_config_for_camera(self.repo_root, lane_config)
        validate_no_shared_lanes_across_cameras(self.repo_root)

        self.cameras = next_cameras
        self._reload_context(camera_config.camera_id)
        return self.get_camera_detail(camera_config.camera_id)

    def delete_camera(self, camera_id: str) -> None:
        if not any(cam.camera_id == camera_id for cam in self.cameras):
            raise KeyError(camera_id)
        self._stop_context(camera_id)
        self.cameras = [cam for cam in self.cameras if cam.camera_id != camera_id]
        save_cameras(self.repo_root, self.cameras)
        delete_lane_config_for_camera(self.repo_root, camera_id)
        delete_background_image(self.repo_root, camera_id)
        delete_evidence_images_for_camera(self.repo_root, camera_id)

    def query_history(
        self,
        *,
        from_ts: Optional[str],
        to_ts: Optional[str],
        camera_id: Optional[str],
        limit: Optional[int],
    ):
        with self._SessionLocal() as session:
            return query_violation_history(
                session,
                from_ts=from_ts,
                to_ts=to_ts,
                camera_id=camera_id,
                limit=limit,
            )

    def query_dashboard(self, *, from_ts: Optional[str], to_ts: Optional[str], camera_id: Optional[str]):
        with self._SessionLocal() as session:
            return query_dashboard_analytics(
                session,
                from_ts=from_ts,
                to_ts=to_ts,
                camera_id=camera_id,
                chart_config=self.cfg.analytics_chart,
            )

    def _on_track(self, msg: TrackMessage) -> None:
        # Hàm này chạy trong event loop của CameraContext nên chỉ dùng thao tác không chặn.
        dead: list[asyncio.Queue] = []
        for q in list(self._track_listeners):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._track_listeners.discard(q)

    def _on_violation(self, ev: ViolationEvent) -> None:
        dead: list[asyncio.Queue] = []
        for q in list(self._violation_listeners):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._violation_listeners.discard(q)

    def create_track_listener(self, *, maxsize: Optional[int] = None) -> asyncio.Queue[TrackMessage | None]:
        queue_size = int(maxsize) if maxsize is not None else int(self.cfg.websocket_listener_queue_maxsize)
        q: asyncio.Queue[TrackMessage | None] = asyncio.Queue(maxsize=max(queue_size, 1))
        self._track_listeners.add(q)
        return q

    def create_violation_listener(self, *, maxsize: Optional[int] = None) -> asyncio.Queue[ViolationEvent | None]:
        queue_size = int(maxsize) if maxsize is not None else int(self.cfg.websocket_listener_queue_maxsize)
        q: asyncio.Queue[ViolationEvent | None] = asyncio.Queue(maxsize=max(queue_size, 1))
        self._violation_listeners.add(q)
        return q

    def remove_track_listener(self, q: asyncio.Queue[TrackMessage | None]) -> None:
        self._track_listeners.discard(q)

    def remove_violation_listener(self, q: asyncio.Queue[ViolationEvent | None]) -> None:
        self._violation_listeners.discard(q)

    def register_track_websocket(self, ws: WebSocket) -> None:
        self._track_websockets.add(ws)

    def unregister_track_websocket(self, ws: WebSocket) -> None:
        self._track_websockets.discard(ws)

    def register_violation_websocket(self, ws: WebSocket) -> None:
        self._violation_websockets.add(ws)

    def unregister_violation_websocket(self, ws: WebSocket) -> None:
        self._violation_websockets.discard(ws)

    def get_lane_polygons(self, camera_id: str) -> dict:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            lane_cfg = load_lane_config_for_camera(self.repo_root, camera_id)
            lane_cfg_pixels = denormalize_lane_config(lane_cfg)
            validation = validate_lane_geometry(lane_cfg)
            return {
                "camera_id": lane_cfg.camera_id,
                "frame_width": lane_cfg.frame_width,
                "frame_height": lane_cfg.frame_height,
                "lanes": [
                    {
                        "lane_id": lane.lane_id,
                        "polygon": lane.polygon,
                        "approach_zone": lane.approach_zone,
                        "commit_gate": lane.commit_gate,
                        "commit_line": lane.commit_line,
                        "allowed_maneuvers": lane.allowed_maneuvers or [],
                        "allowed_lane_changes": lane.allowed_lane_changes or [lane.lane_id],
                        "allowed_vehicle_types": lane.allowed_vehicle_types or ["motorcycle", "car", "truck", "bus"],
                        "maneuvers": lane.maneuvers or {},
                    }
                    for lane in lane_cfg_pixels.lanes
                ],
                "config_validation": validation,
            }
        payload = ctx.get_lane_polygons_for_ui()
        try:
            lane_cfg = load_lane_config_for_camera(self.repo_root, camera_id)
            payload["config_validation"] = validate_lane_geometry(lane_cfg)
        except Exception:
            payload["config_validation"] = []
        return payload

    def get_recent_trajectories(
        self,
        camera_id: str,
        *,
        limit: int = 30,
        lane_id: Optional[int] = None,
        vehicle_type: Optional[str] = None,
    ) -> dict:
        self._require_camera_exists(camera_id)
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return {
                "camera_id": camera_id,
                "limit": int(limit),
                "lane_id": lane_id,
                "vehicle_type": vehicle_type,
                "rows": [],
            }
        return ctx.get_recent_trajectories_for_ui(
            limit=limit,
            lane_id=lane_id,
            vehicle_type=vehicle_type,
        )

    def has_background_image(self, camera_id: str) -> bool:
        self._require_camera_exists(camera_id)
        return get_background_image_path(self.repo_root, camera_id) is not None

    def get_background_image_path(self, camera_id: str) -> Optional[Path]:
        self._require_camera_exists(camera_id)
        return get_background_image_path(self.repo_root, camera_id)

    def save_background_image(self, camera_id: str, *, suffix: str, data: bytes) -> Path:
        self._require_camera_exists(camera_id)
        return save_background_image(self.repo_root, camera_id, suffix=suffix, data=data)

    def delete_background_image(self, camera_id: str) -> bool:
        self._require_camera_exists(camera_id)
        path = get_background_image_path(self.repo_root, camera_id)
        if path is None:
            return False
        delete_background_image(self.repo_root, camera_id)
        return True

    def get_camera_preview_jpeg(self, camera_id: str) -> Optional[bytes]:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return None
        return ctx.get_latest_preview_jpeg()

    def get_violation_evidence_path(self, relative_path: str) -> Optional[Path]:
        return resolve_evidence_image_path(self.repo_root, relative_path)

    async def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        for cam in self.cameras:
            self._start_context(cam.camera_id)
        self._running = True

    async def stop(self) -> None:
        self._stop_event.set()
        self._notify_listener_shutdown()
        await self._close_active_websockets()
        for context in list(self._contexts.values()):
            context.request_shutdown()
        tasks = list(self._tasks.values())
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=6.0,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    pass
        self._tasks.clear()
        self._contexts.clear()
        self._track_listeners.clear()
        self._violation_listeners.clear()
        self._track_websockets.clear()
        self._violation_websockets.clear()
        try:
            self._engine.dispose()
        except Exception:
            pass
        self._running = False

    def _build_context(self, camera_id: str) -> CameraContext:
        cam = next(item for item in self.cameras if item.camera_id == camera_id)
        lane_cfg = load_lane_config_for_camera(self.repo_root, cam.camera_id)
        lane_cfg_pixels = denormalize_lane_config(lane_cfg)
        return CameraContext(
            repo_root=self.repo_root,
            camera_config=cam,
            lane_config=lane_cfg_pixels,
            db_session_factory=self._SessionLocal,
            on_track=self._on_track,
            on_violation=self._on_violation,
            on_log=lambda msg: print(msg, flush=True),
            detector_weights_path=str((self.repo_root / self.cfg.detector_weights_path).resolve()),
            detector_device=self.cfg.detector_device,
            detector_conf_threshold=self.cfg.detector_conf_threshold,
            detector_iou_threshold=self.cfg.detector_iou_threshold,
            tracker_config=self.cfg.tracker_config,
            vehicle_type_history_window_ms=self.cfg.vehicle_type_history_window_ms,
            vehicle_type_history_size=self.cfg.vehicle_type_history_size,
            vehicle_type_history_recency_weight_bias=self.cfg.vehicle_type_history_recency_weight_bias,
            stable_track_max_idle_ms=self.cfg.stable_track_max_idle_ms,
            stable_track_min_iou_for_rebind=self.cfg.stable_track_min_iou_for_rebind,
            stable_track_max_normalized_distance=self.cfg.stable_track_max_normalized_distance,
            temporal_lane_observation_window_ms=self.cfg.temporal_lane_observation_window_ms,
            temporal_lane_min_majority_hits=self.cfg.temporal_lane_min_majority_hits,
            temporal_lane_switch_min_duration_ms=self.cfg.temporal_lane_switch_min_duration_ms,
            lane_assignment_preferred_overlap_ratio=self.cfg.lane_assignment_overlap.preferred_lane_overlap_ratio,
            lane_assignment_preferred_overlap_margin_px=self.cfg.lane_assignment_overlap.preferred_lane_overlap_margin_px,
            track_push_interval_ms=self.cfg.track_push_interval_ms,
            wrong_lane_min_duration_ms=self.cfg.wrong_lane_min_duration_ms,
            turn_region_min_hits=self.cfg.turn_region_min_hits,
            turn_state_timeout_ms=self.cfg.turn_state_timeout_ms,
            trajectory_history_window_ms=self.cfg.trajectory_history_window_ms,
            heading_straight_max_deg=self.cfg.turn_detection_heading.straight_max_deg,
            heading_turn_min_deg=self.cfg.turn_detection_heading.turn_min_deg,
            heading_turn_max_deg=self.cfg.turn_detection_heading.turn_max_deg,
            heading_u_turn_min_change_deg=self.cfg.turn_detection_heading.u_turn_min_change_deg,
            heading_side_sign_tolerance=self.cfg.turn_detection_heading.side_sign_tolerance,
            heading_value_sign_tolerance=self.cfg.turn_detection_heading.value_sign_tolerance,
            heading_straight_curvature_max_for_support=self.cfg.turn_detection_heading.straight_curvature_max_for_heading_support,
            curvature_u_turn_min=self.cfg.turn_detection_curvature.u_turn_min,
            curvature_straight_max=self.cfg.turn_detection_curvature.straight_max,
            curvature_turn_min=self.cfg.turn_detection_curvature.turn_min,
            curvature_fallback_min=self.cfg.turn_detection_curvature.fallback_min,
            opposite_direction_cos_threshold=self.cfg.turn_detection_opposite_direction.cos_threshold,
            line_crossing_side_tolerance_px=self.cfg.line_crossing_side_tolerance_px,
            line_crossing_min_pre_frames=self.cfg.line_crossing_min_pre_frames,
            line_crossing_min_post_frames=self.cfg.line_crossing_min_post_frames,
            line_crossing_min_displacement_px=self.cfg.line_crossing_min_displacement_px,
            line_crossing_min_displacement_ratio=self.cfg.line_crossing_min_displacement_ratio,
            line_crossing_max_gap_ms=self.cfg.line_crossing_max_gap_ms,
            line_crossing_cooldown_ms=self.cfg.line_crossing_cooldown_ms,
            violation_rearm_window_ms=self.cfg.violation_rearm_window_ms,
            evidence_expire_ms=self.cfg.evidence_expire_ms,
            motion_window_samples=self.cfg.motion_window_samples,
            turn_evidence_decay_per_frame=self.cfg.evidence_fusion_turn_scoring.decay_per_frame,
            turn_evidence_score_cap=self.cfg.evidence_fusion_turn_scoring.score_cap,
            turn_evidence_corridor_hit_weight=self.cfg.evidence_fusion_turn_scoring.corridor_hit_weight,
            turn_evidence_exit_zone_hit_weight=self.cfg.evidence_fusion_turn_scoring.exit_zone_hit_weight,
            turn_evidence_exit_line_hit_weight=self.cfg.evidence_fusion_turn_scoring.exit_line_hit_weight,
            turn_evidence_heading_support_weight=self.cfg.evidence_fusion_turn_scoring.heading_support_weight,
            turn_evidence_curvature_support_weight=self.cfg.evidence_fusion_turn_scoring.curvature_support_weight,
            turn_evidence_opposite_direction_weight=self.cfg.evidence_fusion_turn_scoring.opposite_direction_weight,
            turn_evidence_temporal_bonus_weight=self.cfg.evidence_fusion_turn_scoring.temporal_continuity_bonus,
            turn_evidence_no_signal_penalty=self.cfg.evidence_fusion_turn_scoring.no_signal_penalty,
            turn_evidence_temporal_hits_min=self.cfg.evidence_fusion_turn_scoring.temporal_hits_min,
            turn_evidence_strong_exit_min_temporal_hits=self.cfg.evidence_fusion_turn_scoring.strong_exit_min_temporal_hits,
            turn_evidence_strong_exit_min_corridor_hits=self.cfg.evidence_fusion_turn_scoring.strong_exit_min_corridor_hits,
            turn_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_turn,
            turn_score_threshold_with_exit=self.cfg.evidence_fusion_turn_scoring.threshold_turn_with_exit,
            u_turn_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_u_turn,
            u_turn_score_threshold_with_exit=self.cfg.evidence_fusion_turn_scoring.threshold_u_turn_with_exit,
            straight_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_straight,
            trajectory_sample_inside_polygon_min_hits=self.cfg.turn_detection_trajectory.sample_inside_polygon_min_hits,
            trajectory_entry_heading_lookback_points=self.cfg.turn_detection_trajectory.entry_heading_lookback_points,
            trajectory_heading_local_window_points=self.cfg.turn_detection_trajectory.heading_local_window_points,
            state_prune_max_age_s=self.cfg.state_prune_max_age_s,
            rtsp_reconnect_delay_s=self.cfg.rtsp_reconnect_delay_s,
            preview_max_fps=self.cfg.preview_max_fps,
            preview_jpeg_quality=self.cfg.preview_jpeg_quality,
            processing_fps_window_s=self.cfg.processing_fps_window_s,
            evidence_crop_expand_x_ratio=self.cfg.evidence_crop_expand_x_ratio,
            evidence_crop_expand_y_top_ratio=self.cfg.evidence_crop_expand_y_top_ratio,
            evidence_crop_expand_y_bottom_ratio=self.cfg.evidence_crop_expand_y_bottom_ratio,
            evidence_crop_min_size_px=self.cfg.evidence_crop_min_size_px,
            evidence_jpeg_quality=self.cfg.evidence_jpeg_quality,
        )

    def _ui_payload(self) -> dict:
        return self.cfg.ui.model_dump(mode="json")

    def _start_context(self, camera_id: str) -> None:
        ctx = self._build_context(camera_id)
        self._contexts[camera_id] = ctx
        if self._running or not self._stop_event.is_set():
            self._tasks[camera_id] = asyncio.create_task(ctx.run_forever(stop_event=self._stop_event))

    def _stop_context(self, camera_id: str) -> None:
        task = self._tasks.pop(camera_id, None)
        if task is not None:
            task.cancel()
        ctx = self._contexts.pop(camera_id, None)
        if ctx is not None:
            ctx.request_shutdown()

    def _reload_context(self, camera_id: str) -> None:
        was_running = self._running
        self._stop_context(camera_id)
        if was_running:
            # Khi lưu lại cấu hình camera phải thay polygon runtime ngay để màn hình giám sát
            # và logic vi phạm dùng cấu hình mới mà không cần khởi động lại server.
            self._start_context(camera_id)

    def _require_camera_exists(self, camera_id: str) -> None:
        if not any(cam.camera_id == camera_id for cam in self.cameras):
            raise KeyError(camera_id)

    def _notify_listener_shutdown(self) -> None:
        for q in list(self._track_listeners):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        for q in list(self._violation_listeners):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def _close_active_websockets(self) -> None:
        async def close_one(ws: WebSocket) -> None:
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception:
                pass

        sockets = list(self._track_websockets) + list(self._violation_websockets)
        if sockets:
            await asyncio.gather(*(close_one(ws) for ws in sockets), return_exceptions=True)

