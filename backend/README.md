# Backend

Backend là dịch vụ FastAPI xử lý video, chạy AI, phát hiện vi phạm, lưu dữ liệu và cung cấp API/WebSocket cho frontend.

## Mục Lục

- [Vai trò backend](#vai-trò-backend)
- [Chạy backend](#chạy-backend)
- [Model AI](#model-ai)
- [Cấu hình backend](#cấu-hình-backend)
- [Luồng xử lý](#luồng-xử-lý)
- [API và WebSocket](#api-và-websocket)
- [Kiểm thử](#kiểm-thử)

## Vai Trò Backend

Backend chịu trách nhiệm cho toàn bộ phần xử lý nghiệp vụ:

- Đọc video từ RTSP/HTTP/file local.
- Chạy YOLOv8 để phát hiện phương tiện.
- Chạy ByteTrack để theo dõi phương tiện qua các frame.
- Làm mượt `vehicle_id`, loại xe và lane.
- Phát hiện sai làn, sai hướng, sai loại phương tiện và maneuver bị cấm.
- Nhận diện biển số nếu `license_plate.enabled = true`.
- Lưu vi phạm vào SQLite, lưu ảnh bằng chứng và ảnh biển số.
- Cung cấp REST API, MJPEG preview và WebSocket realtime.

Pipeline khái quát:

```text
Video source
  -> YOLOv8 vehicle detector
  -> ByteTrack
  -> lane assignment
  -> violation logic
  -> license plate OCR
  -> DB / evidence images / API / WebSocket
```

## Chạy Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend mặc định chạy tại `http://localhost:8000`.

Các dependency chính:

| Nhóm | Thư viện |
|---|---|
| API | FastAPI, Starlette, Uvicorn |
| AI/video | Ultralytics, OpenCV, NumPy, PyTorch |
| Tracking | ByteTrack qua Ultralytics, `lap` |
| Geometry | Shapely |
| Database/export | SQLAlchemy, SQLite, OpenPyXL |
| OCR biển số | PaddleOCR/PaddlePaddle, EasyOCR |

## Model AI

### Model phương tiện YOLOv8

Nguồn chính thức: Ultralytics Assets trên GitHub Releases.

| Model | Link tải | Gợi ý |
|---|---|---|
| `yolov8n.pt` | [Tải yolov8n.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) | Nhẹ nhất, chạy nhanh nhất. |
| `yolov8s.pt` | [Tải yolov8s.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt) | Cân bằng tốc độ và chất lượng. |
| `yolov8m.pt` | [Tải yolov8m.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt) | Chất lượng tốt hơn, nặng hơn. |
| `yolov8l.pt` | [Tải yolov8l.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8l.pt) | Nặng hơn `m`, hợp khi cần độ chính xác cao. |
| `yolov8x.pt` | [Tải yolov8x.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt) | Nặng nhất, nên dùng GPU mạnh. |

Tải nhanh `yolov8m.pt`:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

Khai báo trong `config/settings.json`:

```json
{
  "detection": {
    "weights_path": "backend/yolov8m.pt",
    "device": "auto",
    "confidence_threshold": 0.25,
    "iou_threshold": 0.7,
    "allowed_classes": ["motorcycle", "car", "truck", "bus"]
  }
}
```

Trong source hiện tại:

- `YoloV8VehicleDetector` load model từ `detection.weights_path`.
- `YoloByteTrackVehicleTracker` gọi `self.detector.model.track(...)`, đây là API có sẵn của Ultralytics.
- `detection.allowed_classes` được đổi sang class id của YOLO và truyền vào `classes=...` để chỉ track các class được bật.

### Model biển số

`license_plate.detector_weights_path` mặc định trỏ tới:

```text
backend/license_plate_yolov8.pt
```

Đây là model detector biển số của dự án. OCR text sau đó do `paddleocr` hoặc `easyocr` xử lý tùy `license_plate.ocr_backend`.
Nếu model detector biển số có nhiều class, cấu hình `license_plate.detector_allowed_classes` để backend chỉ giữ bbox biển số và bỏ qua bbox khác như xe.

Nếu thay model biển số:

1. Đặt file `.pt` mới vào thư mục mong muốn.
2. Sửa `license_plate.detector_weights_path`.
3. Restart backend.

### PyTorch theo phần cứng

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA, ví dụ CUDA 13.0:

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

## Cấu Hình Backend

Backend đọc cấu hình từ thư mục `config`.

| File | Vai trò |
|---|---|
| `config/cameras.json` | Danh sách camera và nguồn video. |
| `config/lane_configs/<camera_id>.json` | Cấu hình lane, polygon, hướng đúng chiều và maneuver. |
| `config/settings.json` | Tham số runtime của detector, tracker, vi phạm, OCR, UI và analytics. |

### `cameras.json`

| Trường | Giải thích |
|---|---|
| `camera_id` | ID duy nhất, dùng cho API và tên file lane config. |
| `rtsp_url` | Nguồn video đầu vào. Có thể là RTSP/HTTP/file local. |
| `camera_type` | `roadside`, `overhead` hoặc `intersection`. |
| `view_direction` | Mô tả hướng nhìn camera. |
| `location.*` | Metadata vị trí: đường, nút giao, GPS. |
| `monitored_lanes` | Lane ID camera giám sát. |
| `frame_width`, `frame_height` | Kích thước frame backend dùng khi xử lý. |

### `lane_configs/<camera_id>.json`

| Trường | Giải thích |
|---|---|
| `lanes[].polygon` | Biên lane để gán xe vào lane. |
| `lanes[].approach_zone` | Vùng xe đi vào trước maneuver, giúp khóa lane nguồn. |
| `lanes[].commit_gate` | Vùng xác nhận bắt đầu maneuver. |
| `lanes[].commit_line` | Vạch xác nhận bắt đầu maneuver. |
| `lanes[].allowed_lane_changes` | Lane được phép chuyển tới. |
| `lanes[].allowed_vehicle_types` | Loại xe được phép trong lane. |
| `lanes[].direction_rule` | Quy tắc đi đúng chiều gồm `enabled`, `direction_path`, `check_zone`. |
| `lanes[].maneuvers.straight/left/right/u_turn` | Cấu hình hướng đi gồm `enabled`, `allowed`, `movement_path`, `exit_line`, `exit_zone`, corridor. |

Tọa độ trong file lane config là tọa độ chuẩn hóa `[0, 1]`. Backend sẽ đổi sang pixel theo `frame_width`/`frame_height` khi chạy.

### `settings.json`

| Nhóm | Các trường chính | Ý nghĩa |
|---|---|---|
| `database` | `path` | Đường dẫn SQLite. |
| `camera.stream` | `rtsp_reconnect_delay_s` | Thời gian chờ trước khi reconnect nguồn video. |
| `detection` | `weights_path`, `device`, `confidence_threshold`, `iou_threshold`, `allowed_classes` | Cấu hình YOLO phát hiện phương tiện và class được bật. |
| `tracking` | `tracker_config`, `vehicle_type_history`, `stable_track` | Cấu hình ByteTrack, làm mượt loại xe và nối track ổn định. |
| `lane_assignment` | `temporal`, `overlap_preference` | Làm mượt lane và xử lý xe nằm gần ranh giới lane. |
| `wrong_lane` | `min_duration_ms` | Thời gian tối thiểu trước khi phát lỗi sai làn. |
| `direction_detection` | `defaults.*` | Ngưỡng đánh giá đúng chiều/ngược chiều. |
| `turn_detection` | `heading`, `curvature`, `opposite_direction`, `trajectory` | Tham số hỗ trợ nhận diện đi thẳng/rẽ/quay đầu từ trajectory. |
| `evidence_fusion` | `line_crossing`, `turn_scoring` | Chấm điểm và hợp nhất bằng chứng hình học. |
| `event_lifecycle` | `violation_rearm_window_ms`, `state_prune_max_age_s` | Chống phát trùng sự kiện và dọn state cũ. |
| `geometry` | `evidence_crop`, `evidence_image` | Cách cắt/lưu ảnh bằng chứng. |
| `performance` | `preview`, `processing` | FPS preview, chất lượng ảnh preview và cách tính FPS xử lý. |
| `websocket` | `track_push_interval_ms`, `listener_queue_maxsize` | Chu kỳ push realtime và queue listener. |
| `ui.monitoring` | `trajectory`, `violation`, `processing_fps` | Tham số frontend dùng cho màn hình giám sát. |
| `license_plate` | detector, `detector_allowed_classes`, OCR, voting, crop | Cấu hình nhận diện biển số và lọc class bbox biển số. |
| `analytics.chart` | granularity, ticks, markers | Cấu hình biểu đồ thống kê. |
| `logging` | `level`, `verbose_violation_trace` | Key log dự phòng trong settings hiện tại. |

Giải thích đầy đủ từng key nằm trong [README tổng quan](../README.md#configsettingsjson).

## Luồng Xử Lý

### Tracking phương tiện

1. `RtspFrameReader` đọc frame và resize về kích thước camera cấu hình.
2. `YoloV8VehicleDetector` load YOLO `.pt`.
3. `YoloByteTrackVehicleTracker` gọi Ultralytics `model.track(...)` với ByteTrack.
4. Tracker chỉ trả về class nằm trong `detection.allowed_classes`.
5. `StableTrackIdAssigner` giảm nhảy ID khi object tạm mất.
6. `TemporalVehicleTypeAssigner` vote loại xe qua nhiều frame để tránh nhãn bị nhảy.

### Gán lane và phát hiện vi phạm

1. `LaneLogic` dùng polygon lane để gán lane raw.
2. `TemporalLaneAssigner` làm mượt lane raw thành lane ổn định.
3. `ViolationLogic` kiểm tra:
   - loại xe có được phép trong lane không;
   - xe có chuyển sang lane bị cấm không;
   - xe có đi ngược chiều không;
   - maneuver thực tế có bị cấm không.
4. Khi vi phạm đủ điều kiện, backend lưu DB, ảnh bằng chứng và đẩy WebSocket.

### Nhận diện biển số

1. Backend crop vùng xe theo bbox.
2. Detector biển số tìm vùng biển số trong crop xe.
3. OCR đọc text biển số.
4. `LicensePlateTemporalResolver` vote nhiều lần theo `vehicle_id`.
5. Trạng thái biển số có thể là `pending`, `confirmed`, `uncertain`, `unreadable`.

## API Và WebSocket

### REST API

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/api/health` | Health check. |
| `GET` | `/api/cameras` | Danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Chi tiết camera, lane config, validation, runtime status. |
| `POST` | `/api/cameras` | Tạo camera mới. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera/lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lane config dạng pixel. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Trajectory gần đây. |
| `GET` | `/api/cameras/{camera_id}/preview` | MJPEG preview. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền `.jpg`/`.png`. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh bằng chứng. |
| `GET` | `/api/violations/history` | Lịch sử vi phạm, có filter. |
| `GET` | `/api/violations/export` | Export CSV/XLSX. |
| `GET` | `/api/analytics/dashboard` | Dashboard và chart config. |
| `GET` | `/api/stats` | Thống kê tổng hợp. |

### WebSocket

| Endpoint | Chức năng |
|---|---|
| `WS /ws/tracks?camera_id=...` | Track realtime. |
| `WS /ws/violations?camera_id=...` | Sự kiện vi phạm realtime. |

## Kiểm Thử

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

## Tài Liệu Liên Quan

- [README tổng quan](../README.md)
- [Frontend README](../frontend/README.md)
