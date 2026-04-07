# Traffic Warning

Hệ thống giám sát giao thông đa camera với:

- backend FastAPI
- nhận luồng RTSP từ camera hoặc Raspberry Pi 5
- YOLOv8 + ByteTrack để phát hiện, phân loại và theo dõi xe
- logic polygon để gán làn và phát hiện vi phạm
- frontend React hiển thị realtime, quản lý camera và thống kê

## Tính năng chính

- Theo dõi nhiều camera độc lập
- Vẽ và chỉnh sửa polygon làn đường thủ công trên giao diện quản lý
- Hiển thị xe đang theo dõi theo thời gian thực
- Hiển thị vi phạm theo thời gian thực
- Lưu lịch sử vi phạm vào SQLite
- Dashboard thống kê theo camera, khu vực và toàn hệ thống
- Ổn định `vehicle_id` tốt hơn so với việc dùng raw track id trực tiếp
- Làm mượt phân loại loại xe theo nhiều frame để giảm nhảy nhãn

## Kiến trúc tổng quát

```text
Camera / Pi 5 (RTSP)
        |
        v
  OpenCV VideoCapture
        |
        v
  YOLOv8 detector
        |
        v
  ByteTrack tracker
        |
        v
  Stable track id + temporal vehicle type smoothing
        |
        v
  Lane logic + violation logic
        |
        +--> WebSocket realtime cho frontend
        |
        +--> SQLite / analytics
```

## Cấu trúc thư mục

- `backend/`: FastAPI, AI, tracker, lane logic, violation logic, DB
- `frontend/`: React + Vite + Canvas
- `config/`: camera config, lane config, settings runtime, SQLite

## Yêu cầu môi trường

- Windows PowerShell
- Python 3.11+ hoặc tương đương
- Node.js 18+
- Nên có GPU nếu dùng model YOLOv8 lớn hơn `yolov8n`

## Cài đặt backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Cài đặt frontend

```powershell
cd frontend
npm install
```

## Chạy hệ thống

### 1. Chạy backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend mặc định chạy tại:

- `http://localhost:8000`

### 2. Chạy frontend

```powershell
cd frontend
npm run dev
```

Frontend mặc định chạy tại:

- `http://localhost:5173`

## Cấu hình chính

### `config/cameras.json`

Khai báo danh sách camera:

- `camera_id`
- `rtsp_url`
- `camera_type`
- `view_direction`
- `frame_width`
- `frame_height`
- `location`

### `config/lane_configs/<camera_id>.json`

Khai báo:

- polygon làn đường
- `allowed_maneuvers`
- `allowed_lane_changes`
- `turn_regions`

### `config/settings.json`

Cấu hình runtime quan trọng:

- `detector_weights_path`: model YOLO đang dùng
- `detector_conf_threshold`: confidence threshold
- `detector_iou_threshold`: IoU threshold
- `vehicle_type_history_window_ms`
- `vehicle_type_history_size`
- `stable_track_max_idle_ms`
- `stable_track_min_iou_for_rebind`
- `stable_track_max_normalized_distance`

Ví dụ:

```json
{
  "db_path": "config/traffic_warning.sqlite",
  "detector_weights_path": "backend/yolov8m.pt",
  "detector_conf_threshold": 0.28,
  "detector_iou_threshold": 0.7,
  "vehicle_type_history_window_ms": 4000,
  "vehicle_type_history_size": 12,
  "stable_track_max_idle_ms": 1500,
  "stable_track_min_iou_for_rebind": 0.15,
  "stable_track_max_normalized_distance": 1.6,
  "resize_frame": true,
  "track_push_interval_ms": 200,
  "wrong_lane_min_duration_ms": 1200,
  "turn_region_min_hits": 3
}
```

## Model YOLO khuyên dùng

Theo mức cân bằng giữa tốc độ và độ chính xác:

- `yolov8n.pt`: nhẹ nhất, nhanh nhất, độ chính xác thấp nhất
- `yolov8s.pt`: nhẹ hơn đáng kể so với `m`, chính xác hơn `n`
- `yolov8m.pt`: cân bằng tốt giữa hiệu năng và độ chính xác
- `yolov8x.pt`: chính xác cao hơn nhưng nặng hơn nhiều

Với máy đang chạy tốt `yolov8s`, có thể dùng `yolov8m` để tăng chất lượng nhận diện.

## Link tải model YOLOv8

Nguồn chính thức:

- `yolov8n.pt`: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt
- `yolov8s.pt`: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt
- `yolov8m.pt`: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt
- `yolov8x.pt`: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt

## Lệnh tải model trên PowerShell

### Tải `yolov8n.pt`

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt" -OutFile ".\backend\yolov8n.pt"
```

### Tải `yolov8s.pt`

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt" -OutFile ".\backend\yolov8s.pt"
```

### Tải `yolov8m.pt`

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

### Tải `yolov8x.pt`

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt" -OutFile ".\backend\yolov8x.pt"
```

## Cách đổi model

Chỉ cần sửa trong `config/settings.json`:

```json
"detector_weights_path": "backend/yolov8m.pt"
```

Sau đó restart backend.

## API và realtime chính

- `GET /api/health`
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}`
- `GET /api/cameras/{camera_id}/lanes`
- `GET /api/cameras/{camera_id}/preview`
- `GET /api/violations/history`
- `GET /api/analytics/dashboard`
- `WS /ws/tracks`
- `WS /ws/violations`

## Lưu ý khi dùng Raspberry Pi 5

- Pi 5 chỉ cần phát RTSP, xử lý AI vẫn chạy ở máy chủ backend
- đảm bảo mạng ổn định để hạn chế drop frame
- băng thông mạng
- độ trễ RTSP
- độ phân giải stream
- FPS thực tế

## Lưu ý về GitHub và file model

Không nên commit trực tiếp các file model lớn như:

- `backend/yolov8x.pt`
- `backend/yolov8m.pt`
- `backend/yolov8s.pt`

Lý do:

- GitHub chặn file lớn hơn 100 MB nếu không dùng Git LFS

Khuyến nghị:

- thêm các file `.pt` vào `.gitignore`
- giữ model ở máy local hoặc tải bằng lệnh trong README

## Cách build frontend production

```powershell
cd frontend
npm run build
```

## Tài liệu liên quan

- `backend/README.md`
- `frontend/README.md`
