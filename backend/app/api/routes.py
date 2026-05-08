from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError

from app.core.config import CameraLaneConfig
from app.core.violation_exports import (
    build_violation_export_filename,
    build_violation_export_rows,
    build_violation_history_csv,
    build_violation_history_xlsx,
)
from app.managers.camera_manager import CameraManager
from app.schemas.camera import CameraConfig
from app.db.repository import query_violation_counts


def create_api_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()
    # Danh sách MIME/đuôi file nền được backend chấp nhận.
    allowed_upload_content_types = {"image/jpeg": ".jpg", "image/png": ".png"}

    def _get_upload_suffix(upload: UploadFile) -> str:
        # Ưu tiên kiểm tra MIME; fallback sang đuôi file để tương thích client cũ.
        content_type = (upload.content_type or "").lower()
        if content_type in allowed_upload_content_types:
            return allowed_upload_content_types[content_type]
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix == ".jpeg":
            suffix = ".jpg"
        if suffix in {".jpg", ".png"}:
            return suffix
        raise HTTPException(status_code=400, detail="Only .jpg and .png background images are supported")

    def _query_history_rows(
        *,
        camera_id: Optional[str],
        from_ts: Optional[str],
        to_ts: Optional[str],
        license_plate: Optional[str],
        limit: Optional[int],
    ):
        # Tách helper để history API và export API dùng cùng một logic query.
        return manager.query_history(
            from_ts=from_ts,
            to_ts=to_ts,
            camera_id=camera_id,
            license_plate=license_plate,
            limit=limit,
        )

    @router.get("/api/health")
    def health():
        # Endpoint healthcheck tối giản cho probe/monitoring.
        return {"status": "ok"}

    @router.get("/api/cameras")
    def list_cameras():
        # Trả danh sách camera đã cấu hình (không gồm state runtime chi tiết).
        return {"cameras": manager.list_cameras()}

    @router.get("/api/cameras/{camera_id}")
    def get_camera(camera_id: str):
        try:
            # Bao gồm cả lane_config + cờ runtime_applied cho màn hình cấu hình.
            return manager.get_camera_detail(camera_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.post("/api/cameras")
    async def create_camera(payload: dict):
        try:
            # Validate payload thô bằng schema typed trước khi ghi file config.
            camera = CameraConfig.model_validate(payload.get("camera"))
            lane_config = CameraLaneConfig.model_validate(payload.get("lane_config"))
            return manager.upsert_camera(camera, lane_config)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.put("/api/cameras/{camera_id}")
    async def update_camera(camera_id: str, payload: dict):
        try:
            # camera_id trên path luôn ghi đè payload để tránh lệch định danh.
            camera = CameraConfig.model_validate({**payload.get("camera", {}), "camera_id": camera_id})
            lane_payload = {**payload.get("lane_config", {}), "camera_id": camera_id}
            lane_config = CameraLaneConfig.model_validate(lane_payload)
            return manager.upsert_camera(camera, lane_config)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.delete("/api/cameras/{camera_id}")
    async def delete_camera(camera_id: str):
        try:
            # Xóa camera + lane config + ảnh nền + evidence liên quan.
            manager.delete_camera(camera_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.post("/api/camera/{camera_id}/background-image")
    async def upload_background_image(camera_id: str, file: UploadFile = File(...)):
        try:
            suffix = _get_upload_suffix(file)
            # Đọc toàn bộ bytes ảnh upload từ request body.
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="Background image file is empty")
            # Ghi ảnh nền lên thư mục config/background_images.
            manager.save_background_image(camera_id, suffix=suffix, data=data)
            return {"ok": True, "camera_id": camera_id, "has_background_image": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/camera/{camera_id}/background-image")
    async def get_background_image(camera_id: str):
        try:
            path = manager.get_background_image_path(camera_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")
        if path is None:
            raise HTTPException(status_code=404, detail="Background image not found")
        # Chọn Content-Type theo đuôi file thực tế để trình duyệt render đúng.
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @router.delete("/api/camera/{camera_id}/background-image")
    async def delete_background_image(camera_id: str):
        try:
            # Trả thêm cờ deleted để client biết có ảnh để xóa hay không.
            deleted = manager.delete_background_image(camera_id)
            return {"ok": True, "camera_id": camera_id, "deleted": deleted}
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/cameras/{camera_id}/lanes")
    def get_camera_lanes(camera_id: str):
        try:
            return manager.get_lane_polygons(camera_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/cameras/{camera_id}/trajectories")
    def get_camera_trajectories(
        camera_id: str,
        limit: int = Query(default=30, ge=1, le=200),
        lane_id: Optional[int] = Query(default=None),
        vehicle_type: Optional[str] = Query(default=None),
    ):
        try:
            # Truy vấn snapshot trajectory runtime đã làm mượt theo track hiện tại.
            return manager.get_recent_trajectories(
                camera_id,
                limit=limit,
                lane_id=lane_id,
                vehicle_type=vehicle_type,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/cameras/{camera_id}/preview")
    async def camera_preview_mjpeg(camera_id: str):
        """
        Phát MJPEG cho thẻ `<img src="...">` trên trình duyệt.
        Suy luận AI vẫn chỉ chạy trong vòng lặp xử lý chính; endpoint này chỉ phát ảnh JPEG đã mã hóa sẵn.
        """

        async def frames():
            # Boundary chuẩn của multipart/x-mixed-replace cho MJPEG stream.
            boundary = b"--frame"
            # Dùng cùng nhịp preview_fps cấu hình để tránh spam network.
            preview_fps = max(float(manager.cfg.preview_max_fps), 0.1)
            while True:
                # Chỉ stream JPEG đã encode sẵn từ context, không chạy lại inference tại endpoint.
                jpg = manager.get_camera_preview_jpeg(camera_id)
                if jpg:
                    yield boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                await asyncio.sleep(1.0 / preview_fps)

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @router.get("/api/violations/evidence/{evidence_path:path}")
    async def get_violation_evidence(evidence_path: str):
        # evidence_path có thể là đường dẫn tương đối đã lưu trong DB.
        path = manager.get_violation_evidence_path(evidence_path)
        if path is None:
            raise HTTPException(status_code=404, detail="Violation evidence image not found")
        return FileResponse(path, media_type="image/jpeg", filename=path.name)

    @router.get("/api/violations/history")
    def violation_history(
        camera_id: Optional[str] = Query(default=None),
        license_plate: Optional[str] = Query(default=None),
        from_ts: Optional[str] = Query(default=None),
        to_ts: Optional[str] = Query(default=None),
        limit: Optional[int] = Query(default=None, ge=1),
    ):
        # Query lịch sử vi phạm theo bộ lọc thời gian/camera/biển số.
        rows = _query_history_rows(
            camera_id=camera_id,
            license_plate=license_plate,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
        )
        return {"rows": rows}

    @router.get("/api/violations/export")
    def export_violation_history(
        request: Request,
        format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
        camera_id: Optional[str] = Query(default=None),
        license_plate: Optional[str] = Query(default=None),
        from_ts: Optional[str] = Query(default=None),
        to_ts: Optional[str] = Query(default=None),
    ):
        # Export luôn dùng full kết quả filter (không giới hạn limit).
        rows = _query_history_rows(
            camera_id=camera_id,
            license_plate=license_plate,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=None,
        )
        if not rows:
            format_label = "Excel" if format == "xlsx" else "CSV"
            raise HTTPException(
                status_code=404,
                detail=f"Không có dữ liệu vi phạm trong khoảng thời gian đã chọn để xuất {format_label}.",
            )

        # Chèn URL evidence tuyệt đối vào từng dòng export để mở trực tiếp từ file.
        export_rows = build_violation_export_rows(rows, base_url=str(request.base_url))
        try:
            # Build file ở bộ nhớ tạm rồi stream về client.
            if format == "xlsx":
                payload = build_violation_history_xlsx(export_rows)
                filename = build_violation_export_filename(extension="xlsx", from_ts=from_ts, to_ts=to_ts)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                payload = build_violation_history_csv(export_rows)
                filename = build_violation_export_filename(extension="csv", from_ts=from_ts, to_ts=to_ts)
                media_type = "text/csv; charset=utf-8"
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return StreamingResponse(
            BytesIO(payload),
            media_type=media_type,
            # Content-Disposition giúp trình duyệt tải file thay vì render inline.
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/analytics/dashboard")
    def analytics_dashboard(
        camera_id: Optional[str] = Query(default=None),
        from_ts: Optional[str] = Query(default=None),
        to_ts: Optional[str] = Query(default=None),
    ):
        # Tổng hợp dashboard gồm overview + summary + chuỗi thời gian.
        data = manager.query_dashboard(from_ts=from_ts, to_ts=to_ts, camera_id=camera_id)
        return {
            **data,
            "from_timestamp": from_ts,
            "to_timestamp": to_ts,
            "camera_id": camera_id,
            # Trả kèm chart_config để frontend render trục theo cùng ngưỡng backend.
            "chart_config": manager.cfg.analytics_chart.model_dump(mode="json"),
        }

    @router.get("/api/stats")
    def stats(
        from_ts: Optional[str] = Query(default=None, description="ISO-8601 timestamp string"),
        to_ts: Optional[str] = Query(default=None, description="ISO-8601 timestamp string"),
    ):
        # Session ngắn theo request để query aggregate vi phạm.
        SessionLocal = manager.session_factory
        with SessionLocal() as session:
            rows = query_violation_counts(session, from_ts=from_ts, to_ts=to_ts)
        return {"rows": rows, "from_timestamp": from_ts, "to_timestamp": to_ts}

    return router
