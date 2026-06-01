from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

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
from app.core.camera_runtime import has_camera_runtime_source
from app.db.repository import (
    query_dashboard_analytics,
    query_violation_detail_by_id,
    query_violation_history,
)
from app.db.database import create_engine_and_session
from app.managers.camera_context import CameraContext
from app.logic.direction_logic import DirectionDetectionSettings
from app.logic.geometry_validator import validate_lane_geometry
from app.schemas.camera import CameraConfig
from app.schemas.events import TrackMessage, ViolationEvent


class CameraManager:
    """Quản lý danh sách camera, context runtime và các kênh realtime toàn hệ thống."""

    def __init__(self, repo_root: Path):
        self._logger = logging.getLogger(__name__)
        # Root thư mục dự án để resolve toàn bộ path config/evidence.
        self.repo_root = repo_root
        # Load settings typed ngay khi khởi tạo manager.
        self.cfg = load_app_config(repo_root)

        # Validate tính toàn vẹn giữa cameras.json và lane_configs/* trước khi chạy.
        validate_no_shared_lanes_across_cameras(repo_root)
        self.cameras: list[CameraConfig] = load_cameras(repo_root)

        # Engine/session dùng chung cho query lịch sử và analytics.
        self._engine, self._SessionLocal = create_engine_and_session(self.cfg.db_path)

        # Runtime map camera_id -> context xử lý realtime.
        self._contexts: dict[str, CameraContext] = {}
        # Cờ stop dùng chung cho toàn bộ camera task.
        self._stop_event = asyncio.Event()
        # Map camera_id -> asyncio task đang chạy run_forever.
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

        # Danh sách queue và websocket đang đăng ký nhận dữ liệu realtime.
        self._track_listeners: set[asyncio.Queue[TrackMessage | None]] = set()
        self._violation_listeners: set[asyncio.Queue[ViolationEvent | None]] = set()
        self._track_websockets: set[WebSocket] = set()
        self._violation_websockets: set[WebSocket] = set()
        self._edge_discovery: Any = None

    def bind_edge_discovery(self, edge_discovery: Any) -> None:
        # Injection nhẹ để runtime có thể lấy fallback IP cho RTSP .local khi cần.
        self._edge_discovery = edge_discovery

    @property
    def session_factory(self):
        # Expose session factory cho API layer cần query DB.
        return self._SessionLocal

    def list_cameras(self) -> list[dict]:
        # Trả danh sách camera ở dạng dict JSON-friendly cho API.
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
        # Tìm camera theo id; không thấy thì ném KeyError để route map thành 404.
        cam = next((item for item in self.cameras if item.camera_id == camera_id), None)
        if cam is None:
            raise KeyError(camera_id)
        lane_cfg = load_lane_config_for_camera(self.repo_root, camera_id)
        # Validate geometry để UI biết cảnh báo cấu hình hiện tại.
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
        # Guard nghiệp vụ: danh sách monitored_lanes phải khớp tuyệt đối với lane_config.
        if camera_config.camera_id != lane_config.camera_id:
            raise ValueError("camera_id mismatch between camera_config and lane_config")
        lane_ids = [lane.lane_id for lane in lane_config.lanes]
        if len(set(lane_ids)) != len(lane_ids):
            raise ValueError("lane_config contains duplicate lane_id values")
        if sorted(camera_config.monitored_lanes) != sorted(lane_ids):
            raise ValueError("monitored_lanes must match lane_config lane_id values")

        next_cameras = [cam for cam in self.cameras if cam.camera_id != camera_config.camera_id]
        # Upsert: thay camera cũ cùng id bằng camera mới.
        next_cameras.append(camera_config)
        # Giữ thứ tự cố định theo camera_id để file config dễ review diff.
        next_cameras.sort(key=lambda cam: cam.camera_id)

        # Ghi file camera và lane config xuống disk.
        save_cameras(self.repo_root, next_cameras)
        save_lane_config_for_camera(self.repo_root, lane_config)
        # Re-validate sau ghi để chặn trạng thái shared lane ngoài ý muốn.
        validate_no_shared_lanes_across_cameras(self.repo_root)

        self.cameras = next_cameras
        # Reload context runtime để áp dụng cấu hình mới ngay.
        self._reload_context(camera_config.camera_id)
        return self.get_camera_detail(camera_config.camera_id)

    def delete_camera(self, camera_id: str) -> None:
        if not any(cam.camera_id == camera_id for cam in self.cameras):
            raise KeyError(camera_id)
        # Dừng context trước khi xóa metadata/config để tránh đọc dữ liệu mồ côi.
        self._stop_context(camera_id)
        self.cameras = [cam for cam in self.cameras if cam.camera_id != camera_id]
        save_cameras(self.repo_root, self.cameras)
        delete_lane_config_for_camera(self.repo_root, camera_id)
        delete_background_image(self.repo_root, camera_id)
        # Dọn evidence ảnh vi phạm để không còn dữ liệu mồ côi theo camera đã xóa.
        delete_evidence_images_for_camera(self.repo_root, camera_id)

    def query_history(
        self,
        *,
        from_ts: Optional[str],
        to_ts: Optional[str],
        camera_id: Optional[str],
        license_plate: Optional[str],
        limit: Optional[int],
    ):
        # Query lịch sử qua repository, session scope theo từng request.
        with self._SessionLocal() as session:
            return query_violation_history(
                session,
                from_ts=from_ts,
                to_ts=to_ts,
                camera_id=camera_id,
                license_plate=license_plate,
                limit=limit,
            )

    def query_dashboard(self, *, from_ts: Optional[str], to_ts: Optional[str], camera_id: Optional[str]):
        # Dashboard aggregate được tính theo filter và chart_config hiện tại.
        with self._SessionLocal() as session:
            return query_dashboard_analytics(
                session,
                from_ts=from_ts,
                to_ts=to_ts,
                camera_id=camera_id,
                chart_config=self.cfg.analytics_chart,
            )

    def query_violation_detail(self, *, violation_id: int) -> dict | None:
        with self._SessionLocal() as session:
            return query_violation_detail_by_id(session, violation_id=int(violation_id))

    def _on_track(self, msg: TrackMessage) -> None:
        # Hàm này chạy trong event loop của CameraContext nên chỉ dùng thao tác không chặn.
        self._broadcast_to_listeners(listeners=self._track_listeners, message=msg)

    def _on_violation(self, ev: ViolationEvent) -> None:
        # Broadcast sự kiện vi phạm tới toàn bộ listener queue đã đăng ký.
        self._broadcast_to_listeners(listeners=self._violation_listeners, message=ev)

    def create_track_listener(self, *, maxsize: Optional[int] = None) -> asyncio.Queue[TrackMessage | None]:
        # Mỗi client/consumer được cấp một queue riêng để tránh tranh chấp dữ liệu.
        q: asyncio.Queue[TrackMessage | None] = self._create_listener_queue(maxsize=maxsize)
        self._track_listeners.add(q)
        return q

    def create_violation_listener(self, *, maxsize: Optional[int] = None) -> asyncio.Queue[ViolationEvent | None]:
        q: asyncio.Queue[ViolationEvent | None] = self._create_listener_queue(maxsize=maxsize)
        self._violation_listeners.add(q)
        return q

    def remove_track_listener(self, q: asyncio.Queue[TrackMessage | None]) -> None:
        # discard an toàn ngay cả khi q không còn trong set.
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

    def has_background_image(self, camera_id: str) -> bool:
        self._require_camera_exists(camera_id)
        # Chỉ cần kiểm tra path tồn tại hay không.
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
        # Có path thì thực hiện xóa và trả True.
        delete_background_image(self.repo_root, camera_id)
        return True

    def get_camera_preview_jpeg(self, camera_id: str) -> Optional[bytes]:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return None
        return ctx.get_latest_preview_jpeg()

    def get_camera_preview_snapshot(self, camera_id: str) -> tuple[Optional[bytes], int]:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return None, 0
        return ctx.get_latest_preview_snapshot()

    def wait_camera_preview_after(
        self,
        *,
        camera_id: str,
        last_seq: int,
        timeout_s: float,
    ) -> tuple[Optional[bytes], int]:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return None, int(last_seq)
        return ctx.wait_for_preview_after(last_seq=int(last_seq), timeout_s=float(timeout_s))

    def get_camera_stream_endpoints(self, camera_id: str) -> dict[str, Any]:
        """
        Trả endpoint phát video thật cho browser (WebRTC/HLS) và fallback MJPEG.
        Endpoint này chỉ cung cấp URL; luồng AI nhận diện hiện tại không thay đổi.
        """
        cam = next((item for item in self.cameras if item.camera_id == camera_id), None)
        if cam is None:
            raise KeyError(camera_id)

        runtime_rtsp_url = self._resolve_runtime_rtsp_url(
            camera_id=cam.camera_id,
            rtsp_url=cam.rtsp_url,
        )
        browser_rtsp_url = self._resolve_browser_rtsp_url(
            camera_id=cam.camera_id,
            rtsp_url=runtime_rtsp_url,
        )
        stream_urls = self._build_browser_stream_urls(browser_rtsp_url)
        fallback_preview_path = f"/api/cameras/{camera_id}/preview"
        return {
            "camera_id": camera_id,
            "rtsp_url": cam.rtsp_url,
            "runtime_rtsp_url": runtime_rtsp_url,
            "browser_rtsp_url": browser_rtsp_url,
            "stream_path": stream_urls["stream_path"],
            "webrtc": stream_urls["webrtc"],
            "hls": stream_urls["hls"],
            # Giữ fallback MJPEG cho trường hợp browser/network không mở được WebRTC/HLS.
            "mjpeg": {
                "enabled": True,
                "preview_url": fallback_preview_path,
            },
        }

    def get_violation_evidence_path(self, relative_path: str) -> Optional[Path]:
        return resolve_evidence_image_path(self.repo_root, relative_path)

    async def start(self) -> None:
        if self._running:
            # Idempotent: start lại khi đang chạy sẽ bỏ qua.
            return
        self._stop_event.clear()
        for cam in self.cameras:
            self._start_context(cam.camera_id)
        self._running = True

    async def stop(self) -> None:
        self._stop_event.set()
        # Đẩy sentinel trước để consumer thoát vòng lặp đọc queue nhanh.
        self._notify_listener_shutdown()
        await self._close_active_websockets()
        for context in list(self._contexts.values()):
            context.request_shutdown()
        tasks = list(self._tasks.values())
        if tasks:
            try:
                # Chờ các context thoát mềm trong timeout đầu tiên.
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=6.0,
                )
            except asyncio.TimeoutError:
                # Quá timeout thì cancel cứng phần task còn treo.
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
            # Đóng engine SQLAlchemy khi manager dừng hẳn.
            self._engine.dispose()
        except Exception:
            pass
        self._running = False

    def refresh_runtime_sources_from_discovery(self, *, camera_ids: set[str] | None = None) -> list[str]:
        """
        Reload các context đang chạy nếu URL runtime có thể được cải thiện nhờ edge discovery.
        Trường hợp chính: host `.local` không resolve được trên máy backend, nhưng discovery có IP fallback.
        """
        if not self._running:
            return []

        selected_ids = {camera_id for camera_id in (camera_ids or set()) if camera_id}
        reloaded: list[str] = []
        for cam in self.cameras:
            if selected_ids and cam.camera_id not in selected_ids:
                continue
            ctx = self._contexts.get(cam.camera_id)
            if ctx is None:
                continue
            runtime_rtsp_url = self._resolve_runtime_rtsp_url(
                camera_id=cam.camera_id,
                rtsp_url=cam.rtsp_url,
            )
            current_rtsp_url = str(ctx.camera_config.rtsp_url or "").strip()
            if runtime_rtsp_url and runtime_rtsp_url != current_rtsp_url:
                self._logger.info(
                    "Reload camera %s runtime source: %s -> %s",
                    cam.camera_id,
                    current_rtsp_url,
                    runtime_rtsp_url,
                )
                self._reload_context(cam.camera_id)
                reloaded.append(cam.camera_id)
        return reloaded

    def _build_context(self, camera_id: str) -> CameraContext:
        # Context lấy camera config + lane config hiện hành để dựng pipeline runtime.
        cam = next(item for item in self.cameras if item.camera_id == camera_id)
        runtime_rtsp_url = self._resolve_runtime_rtsp_url(
            camera_id=cam.camera_id,
            rtsp_url=cam.rtsp_url,
        )
        runtime_camera = cam
        if runtime_rtsp_url != cam.rtsp_url:
            runtime_camera = cam.model_copy(update={"rtsp_url": runtime_rtsp_url})
        lane_cfg = load_lane_config_for_camera(self.repo_root, cam.camera_id)
        lane_cfg_pixels = denormalize_lane_config(lane_cfg)
        # Context nhận full runtime config đã map từ settings + lane config đã denormalize.
        return CameraContext(
            repo_root=self.repo_root,
            camera_config=runtime_camera,
            lane_config=lane_cfg_pixels,
            db_session_factory=self._SessionLocal,
            on_track=self._on_track,
            on_violation=self._on_violation,
            detector_weights_path=str((self.repo_root / self.cfg.detector_weights_path).resolve()),
            detector_backend=self.cfg.detector_backend,
            detector_device=self.cfg.detector_device,
            detector_conf_threshold=self.cfg.detector_conf_threshold,
            detector_iou_threshold=self.cfg.detector_iou_threshold,
            detector_allowed_classes=self.cfg.detector_allowed_classes,
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
            turn_evidence_turn_zone_hit_weight=self.cfg.evidence_fusion_turn_scoring.turn_zone_hit_weight,
            turn_evidence_exit_zone_hit_weight=self.cfg.evidence_fusion_turn_scoring.exit_zone_hit_weight,
            turn_evidence_exit_line_hit_weight=self.cfg.evidence_fusion_turn_scoring.exit_line_hit_weight,
            turn_evidence_heading_support_weight=self.cfg.evidence_fusion_turn_scoring.heading_support_weight,
            turn_evidence_curvature_support_weight=self.cfg.evidence_fusion_turn_scoring.curvature_support_weight,
            turn_evidence_opposite_direction_weight=self.cfg.evidence_fusion_turn_scoring.opposite_direction_weight,
            turn_evidence_temporal_bonus_weight=self.cfg.evidence_fusion_turn_scoring.temporal_continuity_bonus,
            turn_evidence_no_signal_penalty=self.cfg.evidence_fusion_turn_scoring.no_signal_penalty,
            turn_evidence_temporal_hits_min=self.cfg.evidence_fusion_turn_scoring.temporal_hits_min,
            turn_evidence_strong_exit_min_temporal_hits=self.cfg.evidence_fusion_turn_scoring.strong_exit_min_temporal_hits,
            turn_evidence_strong_exit_min_turn_zone_hits=self.cfg.evidence_fusion_turn_scoring.strong_exit_min_turn_zone_hits,
            turn_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_turn,
            turn_score_threshold_with_exit=self.cfg.evidence_fusion_turn_scoring.threshold_turn_with_exit,
            u_turn_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_u_turn,
            u_turn_score_threshold_with_exit=self.cfg.evidence_fusion_turn_scoring.threshold_u_turn_with_exit,
            straight_score_threshold=self.cfg.evidence_fusion_turn_scoring.threshold_straight,
            trajectory_sample_inside_polygon_min_hits=self.cfg.turn_detection_trajectory.sample_inside_polygon_min_hits,
            trajectory_entry_heading_lookback_points=self.cfg.turn_detection_trajectory.entry_heading_lookback_points,
            trajectory_entry_heading_min_displacement_px=(
                self.cfg.turn_detection_trajectory.entry_heading_min_displacement_px
            ),
            trajectory_heading_local_window_points=self.cfg.turn_detection_trajectory.heading_local_window_points,
            lane_fallback_reference_sample_window=(
                self.cfg.turn_detection_trajectory.fallback_reference.sample_window
            ),
            lane_fallback_reference_min_samples=self.cfg.turn_detection_trajectory.fallback_reference.min_samples,
            lane_fallback_reference_consensus_min=self.cfg.turn_detection_trajectory.fallback_reference.consensus_min,
            lane_fallback_reference_inlier_dot_min=self.cfg.turn_detection_trajectory.fallback_reference.inlier_dot_min,
            lane_fallback_reference_inlier_ratio_min=(
                self.cfg.turn_detection_trajectory.fallback_reference.inlier_ratio_min
            ),
            lane_fallback_reference_max_age_ms=self.cfg.turn_detection_trajectory.fallback_reference.max_age_ms,
            lane_fallback_reference_trajectory_blend_max_weight=(
                self.cfg.turn_detection_trajectory.fallback_reference.trajectory_blend_max_weight
            ),
            lane_fallback_reference_trajectory_blend_min_alignment_dot=(
                self.cfg.turn_detection_trajectory.fallback_reference.trajectory_blend_min_alignment_dot
            ),
            direction_detection_settings=DirectionDetectionSettings.from_values(
                **self.cfg.direction_detection_defaults.model_dump()
            ),
            state_prune_max_age_s=self.cfg.state_prune_max_age_s,
            rtsp_reconnect_delay_s=self.cfg.rtsp_reconnect_delay_s,
            preview_max_fps=self.cfg.preview_max_fps,
            preview_jpeg_quality=self.cfg.preview_jpeg_quality,
            preview_output_width=self.cfg.preview_output_width,
            preview_output_height=self.cfg.preview_output_height,
            processing_fps_window_s=self.cfg.processing_fps_window_s,
            processing_prune_interval_ms=self.cfg.processing_prune_interval_ms,
            license_plate_worker_max_pending_jobs=self.cfg.license_plate_worker_max_pending_jobs,
            license_plate_worker_batch_size=self.cfg.license_plate_worker_batch_size,
            evidence_crop_expand_x_ratio=self.cfg.evidence_crop_expand_x_ratio,
            evidence_crop_expand_y_top_ratio=self.cfg.evidence_crop_expand_y_top_ratio,
            evidence_crop_expand_y_bottom_ratio=self.cfg.evidence_crop_expand_y_bottom_ratio,
            evidence_crop_min_size_px=self.cfg.evidence_crop_min_size_px,
            evidence_jpeg_quality=self.cfg.evidence_jpeg_quality,
            license_plate_enabled=self.cfg.license_plate.enabled,
            license_plate_detector_weights_path=str(
                (self.repo_root / self.cfg.license_plate.detector_weights_path).resolve()
            ),
            license_plate_detector_conf_threshold=self.cfg.license_plate.detector_confidence_threshold,
            license_plate_detector_allowed_classes=self.cfg.license_plate.detector_allowed_classes,
            license_plate_detector_backend=self.cfg.license_plate.detector_backend,
            license_plate_ocr_backend=self.cfg.license_plate.ocr_backend,
            license_plate_easyocr_lang=self.cfg.license_plate.easyocr_lang,
            license_plate_easyocr_use_gpu=self.cfg.license_plate.easyocr_use_gpu,
            license_plate_paddle_ocr_version=self.cfg.license_plate.paddle_ocr_version,
            license_plate_paddle_text_detection_model_name=self.cfg.license_plate.paddle_text_detection_model_name,
            license_plate_paddle_text_recognition_model_name=self.cfg.license_plate.paddle_text_recognition_model_name,
            license_plate_paddle_lang=self.cfg.license_plate.paddle_lang,
            license_plate_paddle_use_gpu=self.cfg.license_plate.paddle_use_gpu,
            license_plate_read_interval_ms=self.cfg.license_plate.read_interval_ms,
            license_plate_min_ocr_confidence=self.cfg.license_plate.min_ocr_confidence,
            license_plate_consensus_min_hits=self.cfg.license_plate.consensus_min_hits,
            license_plate_candidate_window_ms=self.cfg.license_plate.candidate_window_ms,
            license_plate_max_attempts_before_unreadable=self.cfg.license_plate.max_attempts_before_unreadable,
            license_plate_crop_expand_x_ratio=self.cfg.license_plate.crop_expand_x_ratio,
            license_plate_crop_expand_y_ratio=self.cfg.license_plate.crop_expand_y_ratio,
            license_plate_image_jpeg_quality=self.cfg.license_plate.image_jpeg_quality,
            license_plate_violation_update_enabled=self.cfg.license_plate.violation_update_enabled,
            license_plate_violation_update_min_confidence=self.cfg.license_plate.violation_update_min_confidence,
            license_plate_violation_update_consensus_min_hits=(
                self.cfg.license_plate.violation_update_consensus_min_hits
            ),
            license_plate_violation_update_window_ms=self.cfg.license_plate.violation_update_window_ms,
            license_plate_violation_require_clean_track=(
                self.cfg.license_plate.require_clean_track_for_violation_update
            ),
            license_plate_prioritize_pending_violation_ocr=(
                self.cfg.license_plate.prioritize_pending_violation_ocr
            ),
            license_plate_violation_track_max_gap_ms=self.cfg.license_plate.violation_update_track_max_gap_ms,
            license_plate_violation_track_min_observations=(
                self.cfg.license_plate.violation_update_track_min_observations
            ),
            license_plate_violation_track_max_center_jump=(
                self.cfg.license_plate.violation_update_track_max_center_jump
            ),
            license_plate_violation_track_overlap_risk_iou=(
                self.cfg.license_plate.violation_update_track_overlap_risk_iou
            ),
            license_plate_violation_track_overlap_risk_distance=(
                self.cfg.license_plate.violation_update_track_overlap_risk_distance
            ),
        )

    def _ui_payload(self) -> dict:
        # Payload UI typed trả thẳng từ settings hiện hành.
        return self.cfg.ui.model_dump(mode="json")

    def _resolve_runtime_rtsp_url(self, *, camera_id: str, rtsp_url: str) -> str:
        raw_url = str(rtsp_url or "").strip()
        if not raw_url:
            return raw_url

        try:
            parsed = urlsplit(raw_url)
        except Exception:
            return raw_url
        if parsed.scheme.lower() not in {"rtsp", "rtsps"}:
            return raw_url
        if not parsed.hostname:
            return raw_url

        host = str(parsed.hostname).strip().lower().rstrip(".")
        if not host.endswith(".local"):
            return raw_url

        try:
            socket.gethostbyname(host)
            return raw_url
        except OSError:
            pass

        fallback_ip = self._find_rtsp_host_fallback_ip(
            camera_id=camera_id,
            unresolved_host=host,
            stream_path=parsed.path or "",
        )
        if not fallback_ip:
            self._logger.warning(
                "Camera %s RTSP host %s is not resolvable and no edge fallback IP matched.",
                camera_id,
                host,
            )
            return raw_url

        netloc = ""
        if parsed.username:
            netloc += parsed.username
            if parsed.password:
                netloc += f":{parsed.password}"
            netloc += "@"
        netloc += fallback_ip
        if parsed.port:
            netloc += f":{parsed.port}"
        resolved_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        self._logger.warning(
            "Camera %s RTSP host %s is not resolvable on this machine. Using fallback %s.",
            camera_id,
            host,
            fallback_ip,
        )
        return resolved_url

    def _resolve_browser_rtsp_url(self, *, camera_id: str, rtsp_url: str) -> str:
        """
        Chuẩn hóa URL cho client browser:
        - Nếu host là `.local`, ưu tiên đổi sang IPv4 fallback trong edge registry.
        - Lý do: backend có thể resolve mDNS được, nhưng máy chạy browser (nhất là Windows)
          có thể không resolve `.local`, khiến WebRTC/HLS fail dù pipeline AI vẫn chạy.
        """
        raw_url = str(rtsp_url or "").strip()
        if not raw_url:
            return raw_url
        try:
            parsed = urlsplit(raw_url)
        except Exception:
            return raw_url
        scheme = str(parsed.scheme or "").lower()
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        if scheme not in {"rtsp", "rtsps"} or not host.endswith(".local"):
            return raw_url

        fallback_ip = self._find_rtsp_host_fallback_ip(
            camera_id=camera_id,
            unresolved_host=host,
            stream_path=parsed.path or "",
        )
        if not fallback_ip:
            return raw_url

        netloc = ""
        if parsed.username:
            netloc += parsed.username
            if parsed.password:
                netloc += f":{parsed.password}"
            netloc += "@"
        netloc += fallback_ip
        if parsed.port:
            netloc += f":{parsed.port}"
        resolved_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        logger = getattr(self, "_logger", None)
        if logger is not None:
            logger.info(
                "Using browser stream fallback host for camera %s: %s -> %s",
                camera_id,
                host,
                fallback_ip,
            )
        return resolved_url

    def _find_rtsp_host_fallback_ip(
        self,
        *,
        camera_id: str,
        unresolved_host: str,
        stream_path: str,
    ) -> str | None:
        discovery = self._edge_discovery
        if discovery is None:
            return None

        try:
            registry = discovery.list_registry()
        except Exception:
            return None
        if not registry:
            return None

        target_path = str(stream_path or "").strip()
        normalized_target_path = target_path.strip("/").lower()
        unresolved_host_root = unresolved_host.split(".", 1)[0]
        for item in registry:
            item_camera_id = str(item.get("camera_id") or "")
            item_host = str(item.get("host") or "").strip().lower().rstrip(".")
            item_mdns = str(item.get("mdns_host") or "").strip().lower().rstrip(".")
            item_stream = str(item.get("stream_path") or "").strip()
            normalized_item_stream = item_stream.strip("/").lower()
            item_host_root = item_host.split(".", 1)[0]
            item_mdns_root = item_mdns.split(".", 1)[0]
            ip_candidate = str(item.get("ip_address") or "").strip()
            if not ip_candidate and self._is_ipv4_address(item_host):
                ip_candidate = item_host
            if not self._is_ipv4_address(ip_candidate):
                continue

            if item_mdns == unresolved_host:
                return ip_candidate
            if item_host == unresolved_host:
                return ip_candidate
            if unresolved_host_root and (
                item_host_root == unresolved_host_root
                or item_mdns_root == unresolved_host_root
            ):
                return ip_candidate
            if item_camera_id and item_camera_id == camera_id:
                return ip_candidate
            if target_path and item_stream and item_stream == target_path:
                return ip_candidate
            if normalized_target_path and normalized_item_stream and (
                normalized_item_stream == normalized_target_path
                or normalized_item_stream.endswith(normalized_target_path)
                or normalized_target_path.endswith(normalized_item_stream)
            ):
                return ip_candidate
        return None

    @staticmethod
    def _is_ipv4_address(value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        except ValueError:
            return False

    @staticmethod
    def _build_browser_stream_urls(rtsp_url: str) -> dict[str, Any]:
        parsed = urlsplit(str(rtsp_url or "").strip())
        scheme = str(parsed.scheme or "").lower()
        host = str(parsed.hostname or "").strip()
        stream_path = str(parsed.path or "").strip("/")

        disabled = {
            "enabled": False,
            "whep_url": None,
            "player_url": None,
        }
        disabled_hls = {
            "enabled": False,
            "m3u8_url": None,
        }
        if scheme not in {"rtsp", "rtsps"} or not host or not stream_path:
            return {
                "stream_path": None,
                "webrtc": disabled,
                "hls": disabled_hls,
            }

        http_scheme = "https" if scheme == "rtsps" else "http"
        # IPv6 literal khi ghép URL HTTP cần bọc dấu [].
        host_for_http = f"[{host}]" if ":" in host and not host.startswith("[") else host
        safe_stream_path = quote(stream_path, safe="/")
        webrtc_base = f"{http_scheme}://{host_for_http}:8889/{safe_stream_path}"
        hls_base = f"{http_scheme}://{host_for_http}:8888/{safe_stream_path}"
        return {
            "stream_path": f"/{stream_path}",
            "webrtc": {
                "enabled": True,
                "whep_url": f"{webrtc_base}/whep",
                "player_url": webrtc_base,
            },
            "hls": {
                "enabled": True,
                "m3u8_url": f"{hls_base}/index.m3u8",
            },
        }

    def _start_context(self, camera_id: str) -> None:
        cam = next((item for item in self.cameras if item.camera_id == camera_id), None)
        if cam is None:
            raise KeyError(camera_id)
        if not has_camera_runtime_source(cam):
            self._logger.warning(
                "Skipping camera %s runtime startup because rtsp_url is empty.",
                camera_id,
            )
            return
        ctx = self._build_context(camera_id)
        self._contexts[camera_id] = ctx
        if self._running or not self._stop_event.is_set():
            # Mỗi camera chạy một task riêng, cô lập lỗi/độ trễ giữa các camera.
            self._tasks[camera_id] = asyncio.create_task(ctx.run_forever(stop_event=self._stop_event))

    def _stop_context(self, camera_id: str) -> None:
        task = self._tasks.pop(camera_id, None)
        if task is not None:
            # Cancel task event-loop trước rồi mới request shutdown resource nền.
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
        # Guard thống nhất cho các API thao tác theo camera_id.
        if not any(cam.camera_id == camera_id for cam in self.cameras):
            raise KeyError(camera_id)

    def _notify_listener_shutdown(self) -> None:
        # Gửi sentinel None để consumer thoát vòng lặp đọc queue.
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

        # Đóng toàn bộ socket track + violation còn mở.
        sockets = list(self._track_websockets) + list(self._violation_websockets)
        if sockets:
            await asyncio.gather(*(close_one(ws) for ws in sockets), return_exceptions=True)

    def _create_listener_queue(self, *, maxsize: Optional[int] = None) -> asyncio.Queue[Any]:
        # maxsize request-level có thể override cấu hình mặc định.
        queue_size = int(maxsize) if maxsize is not None else int(self.cfg.websocket_listener_queue_maxsize)
        return asyncio.Queue(maxsize=max(queue_size, 1))

    @staticmethod
    def _broadcast_to_listeners(*, listeners: set[asyncio.Queue[Any]], message: Any) -> None:
        # Queue full thì loại listener để tránh lan truyền backpressure toàn hệ thống.
        dead: list[asyncio.Queue[Any]] = []
        for queue in list(listeners):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            listeners.discard(queue)



