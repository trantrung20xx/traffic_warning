# Backend

Backend dùng FastAPI để chạy pipeline:

`Video source -> YOLOv8 -> ByteTrack -> Lane assignment -> Violation logic -> API/WebSocket/DB`

Mô hình cấu hình và suy luận tuân theo hướng `lane-centric + maneuver-centric` với `evidence fusion` và `event lifecycle dedup`.

## Chạy backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

## Mô hình YOLO `.pt`: tải, chọn, cấu hình, chạy

### 1) Tải model `.pt`

Nguồn chính thức: Ultralytics Assets.

- `yolov8n.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt`
- `yolov8s.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt`
- `yolov8m.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt`
- `yolov8x.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt`

Ví dụ tải `yolov8m.pt` vào thư mục backend:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

### 2) Cài PyTorch phù hợp phần cứng

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA (ví dụ CUDA 13.0):

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

### 3) Cấu hình model trong `config/settings.json`

```json
{
  "detection": {
    "weights_path": "backend/yolov8m.pt",
    "device": "auto",
    "confidence_threshold": 0.28,
    "iou_threshold": 0.7
  }
}
```

Giải thích:

- `weights_path`: file model YOLO dùng để detect.
- `device`: `auto` (ưu tiên GPU), hoặc ép `cpu` / `cuda:0`.
- `confidence_threshold`: ngưỡng confidence detector.
- `iou_threshold`: ngưỡng IoU cho NMS.

### 4) Chạy backend với model đã chọn

```powershell
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Khi chạy, backend sẽ log `requested_device` và `resolved_device`.
Nếu sai đường dẫn model hoặc model không đọc được, backend sẽ lỗi ở bước khởi tạo detector.

### 5) Chọn model theo mục tiêu vận hành

- `yolov8n`: nhẹ, FPS cao, phù hợp nhiều camera hoặc máy yếu.
- `yolov8s`: cân bằng tốt cho đa số tình huống thực tế.
- `yolov8m`: ưu tiên độ chính xác hơn tốc độ.
- `yolov8x`: chính xác cao nhưng rất nặng, nên dùng GPU mạnh.

## Luồng xử lý chính

1. `RtspFrameReader` đọc `rtsp/rtsps/http/https` hoặc file local.
2. YOLOv8 phát hiện phương tiện.
3. ByteTrack gán track theo frame.
4. `StableTrackIdAssigner` + `TemporalVehicleTypeAssigner` ổn định ID và loại xe.
5. `LaneLogic` + `TemporalLaneAssigner` xác định lane raw/stable.
6. `ViolationLogic` chạy wrong-lane + turn evidence fusion + lifecycle dedup.
7. Ghi DB, lưu ảnh bằng chứng, đẩy WebSocket, phục vụ dashboard/export.

## Cấu hình backend (đồng bộ với `README.md`)

### 1) `config/cameras.json`

| Trường | Giải thích |
|---|---|
| `camera_id` | ID camera duy nhất. |
| `rtsp_url` | Nguồn video đầu vào. |
| `camera_type` | `roadside` / `overhead` / `intersection`. |
| `view_direction` | Mô tả hướng nhìn camera. |
| `location.*` | Metadata vị trí camera. |
| `monitored_lanes` | Lane ID mà camera giám sát. |
| `frame_width`, `frame_height` | Kích thước frame runtime. |

### 2) `config/lane_configs/<camera_id>.json`

| Trường | Giải thích |
|---|---|
| `lanes[].polygon` | Biên làn để gán xe vào đúng làn. |
| `lanes[].approach_zone` | Vùng xe đi vào trước khi rẽ, dùng để khóa source lane. |
| `lanes[].commit_gate` | Vùng xác nhận xe bắt đầu commit maneuver. |
| `lanes[].commit_line` | Vạch xác nhận xe bắt đầu commit maneuver. |
| `lanes[].allowed_lane_changes` | Danh sách làn được phép chuyển tới (dùng cho lỗi `wrong_lane`). |
| `lanes[].allowed_vehicle_types` | Loại xe được phép chạy trong làn. |
| `lanes[].allowed_maneuvers` | Danh sách maneuver hợp lệ theo luật của lane. |
| `lanes[].maneuvers.<m>.enabled` | Bật/tắt nhận diện maneuver này. |
| `lanes[].maneuvers.<m>.allowed` | Cho phép/cấm maneuver này theo luật. |
| `lanes[].maneuvers.<m>.movement_path` | Quỹ đạo kỳ vọng khi xe thực hiện maneuver này (polyline). |
| `lanes[].maneuvers.<m>.corridor_preset` | Preset corridor (`narrow`, `normal`, `wide`). |
| `lanes[].maneuvers.<m>.corridor_width_px` | Độ rộng corridor (px). |
| `lanes[].maneuvers.<m>.turn_corridor` | Polygon corridor (có thể auto sinh từ `movement_path`). |
| `lanes[].maneuvers.<m>.exit_line` | Vạch xác nhận xe đã đi ra đúng nhánh của maneuver. |
| `lanes[].maneuvers.<m>.exit_zone` | Vùng xác nhận xe đã đi ra đúng nhánh của maneuver. |

### 3) `config/settings.json`

| Key | Giải thích |
|---|---|
| `database.path` | Đường dẫn SQLite. |
| `camera.stream.rtsp_reconnect_delay_s` | Delay reconnect source video khi lỗi. |
| `detection.weights_path` | Model YOLO. |
| `detection.device` | Thiết bị suy luận. |
| `detection.confidence_threshold` | Ngưỡng confidence detector. |
| `detection.iou_threshold` | Ngưỡng IoU detector/NMS. |
| `tracking.tracker_config` | Cấu hình ByteTrack. |
| `tracking.vehicle_type_history.window_ms` | Cửa sổ làm mượt loại xe. |
| `tracking.vehicle_type_history.size` | Số mẫu tối đa làm mượt loại xe. |
| `tracking.stable_track.max_idle_ms` | Idle tối đa để giữ khả năng nối track. |
| `tracking.stable_track.min_iou_for_rebind` | IoU tối thiểu để nối track. |
| `tracking.stable_track.max_normalized_distance` | Khoảng cách tối đa để nối track. |
| `lane_assignment.temporal.observation_window_ms` | Cửa sổ quan sát lane raw. |
| `lane_assignment.temporal.min_majority_hits` | Số hit majority để chốt lane. |
| `lane_assignment.temporal.switch_min_duration_ms` | Thời gian tối thiểu để đổi lane ổn định. |
| `wrong_lane.min_duration_ms` | Thời gian tối thiểu để xác nhận đi sai làn. |
| `turn_detection.turn_region_min_hits` | Số lần xe phải xuất hiện trong corridor/zone khi chưa có bằng chứng đầu ra đủ mạnh. |
| `turn_detection.turn_state_timeout_ms` | Timeout reset turn state. |
| `turn_detection.trajectory_history_window_ms` | Cửa sổ trajectory dùng cho turn inference. |
| `evidence_fusion.line_crossing.side_tolerance_px` | Tolerance phía của line crossing. |
| `evidence_fusion.line_crossing.min_pre_frames` | Frame ổn định tối thiểu trước crossing. |
| `evidence_fusion.line_crossing.min_post_frames` | Frame ổn định tối thiểu sau crossing. |
| `evidence_fusion.line_crossing.min_displacement_px` | Dịch chuyển tối thiểu để confirm crossing. |
| `evidence_fusion.line_crossing.min_displacement_ratio` | Dịch chuyển tối thiểu theo tỷ lệ chiều dài line. |
| `evidence_fusion.line_crossing.max_gap_ms` | Gap tối đa trước khi reset crossing state. |
| `evidence_fusion.line_crossing.cooldown_ms` | Cooldown crossing để tránh double hit. |
| `evidence_fusion.evidence_expire_ms` | TTL của turn evidence trước khi decay/xóa. |
| `evidence_fusion.motion_window_samples` | Số mẫu dùng tính heading/curvature/opposite-direction. |
| `event_lifecycle.violation_rearm_window_ms` | Thời gian `re-arm` trước khi cho phép `emit` vi phạm mới trong lifecycle kế tiếp. |
| `event_lifecycle.state_prune_max_age_s` | Tuổi tối đa của vehicle state trước khi prune. |
| `websocket.track_push_interval_ms` | Chu kỳ push track realtime tối thiểu. |
| `performance.preview.max_fps` | FPS preview MJPEG tối đa. |
| `performance.preview.jpeg_quality` | Chất lượng JPEG preview. |
| `performance.processing.fps_window_s` | Cửa sổ tính `processing_fps`. |
| `geometry.evidence_crop.expand_x_ratio` | Nới ngang ảnh evidence. |
| `geometry.evidence_crop.expand_y_top_ratio` | Nới phía trên ảnh evidence. |
| `geometry.evidence_crop.expand_y_bottom_ratio` | Nới phía dưới ảnh evidence. |
| `geometry.evidence_crop.min_size_px` | Kích thước crop evidence tối thiểu. |
| `geometry.evidence_image.jpeg_quality` | Chất lượng JPEG evidence lưu đĩa. |
| `analytics.chart.minute_granularity_max_range_hours` | Giới hạn range để giữ granularity theo phút. |
| `analytics.chart.hour_granularity_max_range_days` | Giới hạn range để giữ granularity theo giờ. |
| `analytics.chart.day_granularity_max_range_days` | Giới hạn range để giữ granularity theo ngày. |
| `analytics.chart.week_granularity_max_range_days` | Giới hạn range để giữ granularity theo tuần. |
| `analytics.chart.minute_axis_label_interval_minutes` | Khoảng phút giữa các nhãn trục X ở chế độ phút. |
| `analytics.chart.minute_axis_max_ticks` | Tick tối đa của biểu đồ minute. |
| `analytics.chart.hour_axis_max_ticks` | Tick tối đa của biểu đồ hour. |
| `analytics.chart.overview_axis_max_ticks` | Tick tối đa của biểu đồ overview. |
| `analytics.chart.point_markers_max_points` | Số điểm tối đa trước khi giảm marker. |
| `logging.level` | Mức log mong muốn (key dự phòng). |
| `logging.verbose_violation_trace` | Cờ trace vi phạm chi tiết (key dự phòng). |

## API backend (đồng bộ với router hiện tại)

| Method | Endpoint | Giải thích |
|---|---|---|
| `GET` | `/api/health` | Health check backend. |
| `GET` | `/api/cameras` | Danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Chi tiết camera + lane config + validation. |
| `POST` | `/api/cameras` | Tạo camera + lane config. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera + lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera và dữ liệu liên quan. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lane config dạng pixel để UI overlay. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Trajectory gần đây (`limit`, `lane_id`, `vehicle_type`). |
| `GET` | `/api/cameras/{camera_id}/preview` | Stream MJPEG preview. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền hiện tại. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh bằng chứng vi phạm. |
| `GET` | `/api/violations/history` | Lịch sử vi phạm có lọc thời gian/camera/limit. |
| `GET` | `/api/violations/export` | Export lịch sử vi phạm (`format=csv|xlsx`). |
| `GET` | `/api/analytics/dashboard` | Dữ liệu dashboard tổng hợp + chart config. |
| `GET` | `/api/stats` | Thống kê tổng hợp theo thời gian. |

### WebSocket

| Endpoint | Giải thích |
|---|---|
| `WS /ws/tracks?camera_id=...` | Stream track realtime. |
| `WS /ws/violations?camera_id=...` | Stream vi phạm realtime. |

## Kiểm thử

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest backend/tests -q
```

## Tài liệu tổng quan

Xem thêm tài liệu hệ thống ở [README.md](../README.md) để có mô tả end-to-end và workflow frontend.
