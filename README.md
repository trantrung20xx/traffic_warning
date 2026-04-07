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

## Dùng GPU cho nhận diện

Nếu máy có GPU NVIDIA và driver hỗ trợ CUDA, backend có thể chạy YOLO trên GPU để tăng tốc suy luận.

### 1. Cài PyTorch đúng bản

Sau khi kích hoạt `backend/.venv`, cài một trong hai lựa chọn:

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA, ví dụ CUDA 13.0:

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

Lưu ý:

- Không chỉ cài driver NVIDIA là đủ, Python backend phải dùng bản `torch` có CUDA.
- Nếu đang cài `torch` bản CPU-only thì model vẫn sẽ chạy trên CPU.
- Cần chọn đúng bản CUDA tương thích với driver và wheel PyTorch đang có.

### 2. Bật GPU trong cấu hình

Trong [config/settings.json](/d:/Personal/DATN/traffic_warning/config/settings.json):

```json
"detector_device": "auto"
```

Các giá trị hỗ trợ:

- `auto`: ưu tiên `cuda:0` nếu có GPU, nếu không thì fallback về `cpu`
- `cuda` hoặc `cuda:0`: ép chạy GPU, nếu không có CUDA backend sẽ báo lỗi khi khởi động
- `cpu`: ép chạy CPU

### 3. Kiểm tra PyTorch đã thấy GPU chưa

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

Nếu cấu hình đúng, sẽ thấy:

- `torch.cuda.is_available()` trả về `True`
- tên GPU NVIDIA được in ra

### 4. Kiểm tra backend đang dùng GPU thật

Khi backend khởi động, log sẽ có dòng tương tự:

```text
[camera_id] detector=... requested_device=auto resolved_device=cuda:0
```

Nếu log ra `resolved_device=cpu` thì có nghĩa là backend chưa dùng được GPU.

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
- `detector_device`: `auto`, `cpu`, `cuda`, `cuda:0`...
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
  "detector_device": "auto",
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

`detector_device` hoạt động như sau:

- `auto`: ưu tiên GPU CUDA nếu PyTorch nhìn thấy GPU, nếu không thì fallback về CPU
- `cuda` hoặc `cuda:0`: ép chạy GPU, nếu không có CUDA sẽ báo lỗi khi khởi động
- `cpu`: ép chạy CPU

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

Nếu muốn ép hẳn GPU:

```json
"detector_device": "cuda:0"
```

Lưu ý: để chạy được GPU, môi trường Python backend phải cài bản `torch` có CUDA tương thích driver/NVIDIA của máy. Nếu cài `torch` bản CPU-only thì hệ thống vẫn phải chạy trên CPU dù có card NVIDIA.

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
