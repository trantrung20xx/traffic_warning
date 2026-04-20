# Backend

Backend dùng FastAPI để điều phối toàn bộ pipeline xử lý video, phát hiện vi phạm và cung cấp API/WebSocket cho frontend.

## Vai trò của backend

- Quản lý danh sách camera và cấu hình làn.
- Đọc RTSP hoặc file video bằng OpenCV.
- Chạy YOLOv8 để phát hiện và phân loại phương tiện.
- Chạy ByteTrack để theo dõi theo frame.
- Ổn định `vehicle_id` khi raw id thay đổi ngắn hạn.
- Làm mượt loại xe theo nhiều frame.
- Gán làn bằng polygon và point-in-polygon.
- Phát hiện vi phạm bằng rule engine.
- Lưu vi phạm vào SQLite.
- Lưu ảnh bằng chứng.
- Phát track và vi phạm realtime qua WebSocket.
- Cung cấp dữ liệu dashboard, lịch sử và export.

## Cấu trúc module

- `app/server.py`: khởi tạo FastAPI, CORS, startup/shutdown.
- `app/api/`: REST API và WebSocket.
- `app/managers/`: quản lý camera và pipeline runtime.
- `app/vision/`: YOLOv8 detector.
- `app/tracking/`: ByteTrack wrapper.
- `app/logic/`: gán làn, stable id, smoothing loại xe, phát hiện vi phạm.
- `app/core/`: cấu hình, ảnh nền, ảnh bằng chứng, export.
- `app/db/`: model SQLAlchemy, session, repository.
- `app/schemas/`: schema Pydantic cho API và WebSocket.
- `app/stats/`: thống kê realtime trong bộ nhớ.
- `app/rtsp/`: đọc frame từ RTSP/video.
- `tests/`: test logic và repository.

## Phụ thuộc

Cài trong môi trường ảo:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` hiện dùng:

- `fastapi`
- `uvicorn[standard]`
- `opencv-python`
- `numpy`
- `pydantic`
- `pydantic-settings`
- `SQLAlchemy`
- `sqlalchemy-utils`
- `ultralytics`
- `lap`
- `python-multipart`
- `openpyxl`

PyTorch cài riêng theo CPU/GPU.

## Chạy backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Server mặc định chạy tại `http://localhost:8000`.

## Cấu hình backend

Backend đọc cấu hình từ thư mục `../config`.

### `../config/settings.json`

File này quyết định tham số runtime:

- detector:
  - `detector_weights_path`
  - `detector_device`
  - `detector_conf_threshold`
  - `detector_iou_threshold`
- tracker:
  - `tracker_config`
- ổn định label/track:
  - `vehicle_type_history_window_ms`
  - `vehicle_type_history_size`
  - `stable_track_max_idle_ms`
  - `stable_track_min_iou_for_rebind`
  - `stable_track_max_normalized_distance`
- ổn định gán làn:
  - `temporal_lane_observation_window_ms`
  - `temporal_lane_min_majority_hits`
  - `temporal_lane_switch_min_duration_ms`
- realtime:
  - `track_push_interval_ms`
  - `wrong_lane_min_duration_ms`
  - `turn_region_min_hits`
  - `state_prune_max_age_s`
  - `rtsp_reconnect_delay_s`
- preview và ảnh bằng chứng:
  - `preview_max_fps`
  - `preview_jpeg_quality`
  - `processing_fps_window_s`
  - `evidence_crop_expand_x_ratio`
  - `evidence_crop_expand_y_top_ratio`
  - `evidence_crop_expand_y_bottom_ratio`
  - `evidence_crop_min_size_px`
  - `evidence_jpeg_quality`

### `../config/cameras.json`

Mỗi camera cần:

- `camera_id`
- `rtsp_url`
- `camera_type`
- `view_direction`
- `location`
- `monitored_lanes`
- `frame_width`
- `frame_height`

### `../config/lane_configs/<camera_id>.json`

Tọa độ polygon hiện được lưu chuẩn hóa theo `[0, 1]`:

- `polygon`
- `turn_regions`
- `allowed_maneuvers`
- `allowed_lane_changes`
- `allowed_vehicle_types`

Khi runtime, backend sẽ đổi lại sang hệ pixel đúng với `frame_width/frame_height`.

## Database và dữ liệu sinh ra

- SQLite mặc định: `../config/traffic_warning.sqlite`
- Ảnh nền camera: `../config/background_images/`
- Ảnh bằng chứng: `../config/evidence_images/<camera_id>/<dd-mm-yyyy>/...jpg`

## API hiện có

### Camera

- `GET /api/cameras`
- `GET /api/cameras/{camera_id}`
- `POST /api/cameras`
- `PUT /api/cameras/{camera_id}`
- `DELETE /api/cameras/{camera_id}`
- `GET /api/cameras/{camera_id}/lanes`
- `GET /api/cameras/{camera_id}/preview`

### Ảnh nền

- `POST /api/camera/{camera_id}/background-image`
- `GET /api/camera/{camera_id}/background-image`
- `DELETE /api/camera/{camera_id}/background-image`

### Vi phạm và thống kê

- `GET /api/violations/evidence/{evidence_path}`
- `GET /api/violations/history`
- `GET /api/violations/export?format=csv|xlsx`
- `GET /api/analytics/dashboard`
- `GET /api/stats`
- `GET /api/health`

### WebSocket

- `WS /ws/tracks?camera_id=...`
- `WS /ws/violations`
- `WS /ws/violations?camera_id=...`

## Ghi chú xử lý

- Lane detection hiện không dùng AI; làn được gán bằng polygon thủ công.
- Điểm đại diện của xe để gán làn là tâm cạnh đáy của bounding box.
- Vi phạm sai làn được tính theo thời gian thực, không theo số frame.
- Hướng rẽ được suy luận từ `turn_regions` và số lần hit tối thiểu.
- Backend có endpoint preview MJPEG riêng; suy luận AI không chạy ở endpoint này.
- Trên Windows, `server.py` dùng `WindowsSelectorEventLoopPolicy` để giảm lỗi stack trace khi websocket bị ngắt đột ngột.

## Kiểm thử

Các test hiện có:

- `tests/test_lane_features.py`
- `tests/test_repository_timezones.py`
- `tests/test_vehicle_type_logic.py`

Chạy:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m unittest discover tests
```
