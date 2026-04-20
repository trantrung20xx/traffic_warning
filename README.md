# Traffic Warning

Hệ thống giám sát và cảnh báo vi phạm giao thông theo thời gian thực, gồm:

- `backend` FastAPI đọc RTSP/video, chạy YOLOv8 + ByteTrack, gán làn bằng polygon và phát hiện vi phạm theo luật.
- `frontend` React hiển thị giám sát realtime, thống kê lịch sử và màn hình quản lý camera.
- `config` chứa cấu hình camera, cấu hình làn, ảnh nền, ảnh bằng chứng và SQLite.

## Chức năng hiện có

- Theo dõi nhiều camera độc lập.
- Hỗ trợ nguồn RTSP hoặc file video cục bộ.
- Phát hiện 4 nhóm phương tiện: `motorcycle`, `car`, `truck`, `bus`.
- Ổn định `vehicle_id` khi raw track id của tracker bị đổi ngắn hạn.
- Làm mượt nhãn loại xe theo nhiều frame để giảm nhảy nhãn.
- Gán `lane_id` bằng polygon thủ công, không dùng AI lane detection.
- Phát hiện:
  - đi sai làn
  - loại phương tiện không đúng làn
  - hướng đi không đúng quy định qua `turn_regions`
- Lưu lịch sử vi phạm vào SQLite.
- Lưu ảnh bằng chứng theo camera/ngày.
- Xem preview MJPEG trực tiếp trên frontend.
- Upload/xóa ảnh nền để căn polygon trên màn hình quản lý.
- Thống kê dashboard theo camera, khu vực, loại xe, loại vi phạm và chuỗi thời gian theo giờ.
- Export lịch sử vi phạm ra `CSV` hoặc `XLSX`.

## Luồng xử lý

```text
RTSP / file video
        |
        v
OpenCV VideoCapture
        |
        v
YOLOv8 detector
        |
        v
ByteTrack
        |
        v
Stable track id + smoothing vehicle type
        |
        v
Lane polygon logic + temporal lane assigner
        |
        v
Violation logic
        |
        +--> WebSocket / preview MJPEG
        +--> SQLite + analytics + export
        +--> Ảnh bằng chứng
```

## Cấu trúc thư mục

- `backend/`: API, xử lý AI, tracker, logic vi phạm, DB, test.
- `frontend/`: giao diện React + Vite.
- `config/cameras.json`: danh sách camera.
- `config/lane_configs/*.json`: polygon làn và rule vi phạm cho từng camera.
- `config/settings.json`: tham số runtime.
- `config/background_images/`: ảnh nền để căn polygon trong màn hình quản lý.
- `config/evidence_images/`: ảnh bằng chứng của vi phạm.
- `config/traffic_warning.sqlite`: cơ sở dữ liệu SQLite mặc định.

## Yêu cầu môi trường

- Python 3.11 trở lên.
- Node.js 18 trở lên.
- Windows PowerShell là môi trường đang được project dùng nhiều nhất.
- Có thể chạy CPU; nếu có GPU NVIDIA thì có thể cấu hình YOLO chạy CUDA.

## Cài đặt

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

PyTorch được cài riêng theo phần cứng:

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA, ví dụ CUDA 13.0:

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

### Frontend

```powershell
cd frontend
npm install
```

## Chạy hệ thống

### Chạy backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend mặc định: `http://localhost:8000`

### Chạy frontend

```powershell
cd frontend
npm run dev
```

Frontend mặc định: `http://localhost:5173`

Frontend sẽ dùng `VITE_API_BASE` nếu được cấu hình; nếu không sẽ mặc định gọi `http://localhost:8000`.

## Cấu hình quan trọng

### `config/cameras.json`

Mỗi camera gồm:

- `camera_id`
- `rtsp_url`
- `camera_type`: `roadside`, `overhead`, `intersection`
- `view_direction`
- `location`
- `monitored_lanes`
- `frame_width`
- `frame_height`

### `config/lane_configs/<camera_id>.json`

Dữ liệu hiện tại được lưu theo tọa độ chuẩn hóa `[0, 1]`:

- `polygon`
- `turn_regions`
- `allowed_maneuvers`
- `allowed_lane_changes`
- `allowed_vehicle_types`

### `config/settings.json`

Các tham số runtime đang được dùng:

- detector/tracker:
  - `detector_weights_path`
  - `detector_device`
  - `detector_conf_threshold`
  - `detector_iou_threshold`
  - `tracker_config`
- ổn định tracking/nhãn:
  - `vehicle_type_history_window_ms`
  - `vehicle_type_history_size`
  - `stable_track_max_idle_ms`
  - `stable_track_min_iou_for_rebind`
  - `stable_track_max_normalized_distance`
- ổn định gán làn:
  - `temporal_lane_observation_window_ms`
  - `temporal_lane_min_majority_hits`
  - `temporal_lane_switch_min_duration_ms`
- realtime/vi phạm:
  - `track_push_interval_ms`
  - `wrong_lane_min_duration_ms`
  - `turn_region_min_hits`
  - `state_prune_max_age_s`
- preview/ảnh bằng chứng:
  - `preview_max_fps`
  - `preview_jpeg_quality`
  - `processing_fps_window_s`
  - `evidence_crop_expand_x_ratio`
  - `evidence_crop_expand_y_top_ratio`
  - `evidence_crop_expand_y_bottom_ratio`
  - `evidence_crop_min_size_px`
  - `evidence_jpeg_quality`

## API và realtime

### REST API

- `GET /api/health`
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}`
- `POST /api/cameras`
- `PUT /api/cameras/{camera_id}`
- `DELETE /api/cameras/{camera_id}`
- `GET /api/cameras/{camera_id}/lanes`
- `GET /api/cameras/{camera_id}/preview`
- `POST /api/camera/{camera_id}/background-image`
- `GET /api/camera/{camera_id}/background-image`
- `DELETE /api/camera/{camera_id}/background-image`
- `GET /api/violations/evidence/{evidence_path}`
- `GET /api/violations/history`
- `GET /api/violations/export?format=csv|xlsx`
- `GET /api/analytics/dashboard`
- `GET /api/stats`

### WebSocket

- `WS /ws/tracks?camera_id=...`
- `WS /ws/violations`
- `WS /ws/violations?camera_id=...`

## Các màn hình frontend

- `Giám sát`
  - xem preview MJPEG
  - overlay polygon làn
  - xem xe đang track
  - xem vi phạm realtime và mở modal chi tiết
- `Thống kê`
  - lọc theo camera và khoảng thời gian
  - biểu đồ theo camera, loại xe, loại vi phạm, khu vực
  - biểu đồ chuỗi thời gian theo giờ
  - lịch sử vi phạm và export CSV/Excel
- `Quản lý camera`
  - thêm/sửa/xóa camera
  - vẽ polygon làn và turn region
  - chỉnh loại xe cho phép, làn được phép chuyển, hướng được phép
  - upload/xóa ảnh nền

## Kiểm thử backend

Project hiện có các test:

- `backend/tests/test_lane_features.py`
- `backend/tests/test_repository_timezones.py`
- `backend/tests/test_vehicle_type_logic.py`

Chạy test:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m unittest discover tests
```

## Model YOLO

Project đang kèm sẵn các file:

- `backend/yolov8n.pt`
- `backend/yolov8s.pt`
- `backend/yolov8m.pt`
- `backend/yolov8x.pt`

Chọn model bằng `detector_weights_path` trong `config/settings.json`.

`detector_device` hỗ trợ:

- `auto`: ưu tiên `cuda:0` nếu khả dụng, nếu không thì dùng CPU
- `cuda` hoặc `cuda:0`: ép dùng GPU
- `cpu`: ép dùng CPU

## Tài liệu con

- [backend/README.md](backend/README.md)
- [frontend/README.md](frontend/README.md)
