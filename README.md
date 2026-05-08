# Traffic Warning

Traffic Warning là hệ thống giám sát giao thông thời gian thực. Backend đọc camera/video, phát hiện phương tiện bằng YOLOv8, theo dõi bằng ByteTrack, gán xe vào làn bằng polygon cấu hình thủ công, phân tích hướng di chuyển/quỹ đạo để phát hiện vi phạm, lưu bằng chứng và đẩy dữ liệu realtime lên frontend React.

Ngày cập nhật tài liệu: 2026-05-08.

## Mục Lục

- [Tổng quan](#tổng-quan)
- [Luồng xử lý](#luồng-xử-lý)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Khởi chạy nhanh](#khởi-chạy-nhanh)
- [Model AI và OCR](#model-ai-và-ocr)
- [Cấu hình hệ thống](#cấu-hình-hệ-thống)
- [Các loại vi phạm](#các-loại-vi-phạm)
- [API và WebSocket](#api-và-websocket)
- [Kiểm thử](#kiểm-thử)
- [Tài liệu liên quan](#tài-liệu-liên-quan)

## Tổng Quan

| Thành phần | Vai trò |
|---|---|
| `backend` | FastAPI, OpenCV, YOLOv8, ByteTrack, ổn định track ID, gán làn, phát hiện hướng/vi phạm, OCR biển số, lưu SQLite, REST API và WebSocket. |
| `frontend` | React + Vite cho màn hình giám sát realtime, thống kê, lịch sử vi phạm, quản lý camera/lane/maneuver và cấu hình hình học. |
| `config` | Cấu hình camera, lane config, settings runtime, ảnh nền, ảnh bằng chứng và SQLite database. |

Khả năng chính:

- Nhận diện phương tiện theo `detection.allowed_classes`, mặc định gồm `motorcycle`, `car`, `truck`, `bus`.
- Tracking bằng ByteTrack, sau đó ổn định `vehicle_id` bằng IoU và khoảng cách tâm bbox.
- Làm mượt loại xe và lane theo thời gian để giảm nhiễu theo từng frame.
- Gán xe vào lane bằng polygon/vùng đáy bbox, không dùng AI nhận diện lane.
- Phát hiện `wrong_lane`, `wrong_direction`, `vehicle_type_not_allowed` và các lỗi rẽ/đi thẳng/quay đầu không đúng quy định.
- OCR biển số bằng detector biển số + `paddleocr` hoặc `easyocr`, có voting theo thời gian với trạng thái `pending`, `confirmed`, `uncertain`, `unreadable`.
- Lưu ảnh bằng chứng tổng quan và ảnh crop biển số nếu có.
- Stream realtime qua WebSocket và MJPEG preview.
- Quản lý camera/lane/maneuver trên UI, có ảnh nền hỗ trợ căn chỉnh và cảnh báo cấu hình hình học.
- Xuất lịch sử vi phạm ra CSV/XLSX.

## Luồng Xử Lý

```text
Nguồn video RTSP/HTTP/file local
  -> RtspFrameReader đọc frame nền bằng OpenCV
  -> YOLOv8 + ByteTrack phát hiện và theo dõi phương tiện
  -> StableTrackIdAssigner ổn định vehicle_id
  -> TemporalVehicleTypeAssigner ổn định loại xe
  -> LaneLogic tính raw lane bằng polygon + đáy bbox
  -> TemporalLaneAssigner ổn định lane_id bằng majority/hysteresis
  -> ViolationLogic phân tích wrong lane, direction, maneuver, lifecycle
  -> License plate detector/OCR chạy nền nếu bật
  -> lưu SQLite + ảnh bằng chứng
  -> REST API, WebSocket, MJPEG preview cho frontend
```

Một số khái niệm quan trọng:

- `vehicle_id`: ID ổn định trong phiên runtime, có thể thay đổi sau khi backend restart.
- `track_session_id`: ID phiên chạy của camera context, dùng để phân biệt vòng đời track giữa các lần reload/restart.
- `raw_lane_id`: lane tính trực tiếp ở frame hiện tại.
- `lane_id`: lane ổn định sau temporal smoothing.
- `direction_dot`: cosine giữa vector chuyển động và vector hướng hợp lệ của lane.
- `evidence_summary`: thông tin giải thích quyết định vi phạm trong pipeline runtime.
- Tọa độ lane config được lưu normalized `[0, 1]`; backend denormalize sang pixel lúc chạy.

## Cấu Trúc Thư Mục

| Đường dẫn | Nội dung |
|---|---|
| `backend/app/api` | REST API, MJPEG preview và WebSocket router. |
| `backend/app/core` | Load/validate config, ảnh nền, ảnh bằng chứng, export CSV/XLSX, timezone. |
| `backend/app/db` | SQLAlchemy model, SQLite engine/session, repository query/insert. |
| `backend/app/logic` | Lane assignment, direction detection, violation logic, OCR temporal resolver, stable track ID, geometry validator. |
| `backend/app/managers` | Quản lý nhiều camera và pipeline runtime của từng camera. |
| `backend/app/rtsp` | Thread đọc frame từ RTSP/HTTP/file local bằng OpenCV. |
| `backend/app/tracking` | Wrapper Ultralytics ByteTrack. |
| `backend/app/vision` | YOLO detector phương tiện, detector biển số, OCR Paddle/EasyOCR, backend/device resolver. |
| `backend/tests` | Test backend. |
| `frontend/src/views` | Màn hình Monitoring, Analytics, Management. |
| `frontend/src/components` | Canvas, overlay, chart, icon và component UI. |
| `config/cameras.json` | Danh sách camera. |
| `config/lane_configs` | Lane config theo từng camera. |
| `config/settings.json` | Settings runtime toàn hệ thống. |
| `config/background_images` | Ảnh nền cấu hình lane. |
| `config/evidence_images` | Ảnh bằng chứng vi phạm. |

## Khởi Chạy Nhanh

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend mặc định chạy tại `http://localhost:8000`.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Frontend mặc định chạy tại `http://localhost:5173`.

Nếu backend không chạy ở port `8000`, tạo `frontend/.env`:

```env
VITE_API_BASE=http://localhost:8000
VITE_API_PORT=8000
```

## Model AI Và OCR

### YOLOv8 phát hiện phương tiện

`config/settings.json` đang trỏ `detection.weights_path` tới `backend/yolov8m.pt`. Có thể dùng các model Ultralytics `.pt` khác tùy phần cứng.

| Model | Link tải | Gợi ý |
|---|---|---|
| `yolov8n.pt` | [Ultralytics asset](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) | Nhẹ nhất, FPS cao. |
| `yolov8s.pt` | [Ultralytics asset](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt) | Cân bằng tốc độ/độ chính xác. |
| `yolov8m.pt` | [Ultralytics asset](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt) | Độ chính xác tốt hơn, cần máy khỏe hơn. |
| `yolov8l.pt` | [Ultralytics asset](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8l.pt) | Nặng, ưu tiên chất lượng. |
| `yolov8x.pt` | [Ultralytics asset](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt) | Nặng nhất, nên dùng GPU mạnh. |

Ví dụ tải `yolov8m.pt`:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

Cấu hình liên quan:

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

Backend có cơ chế fallback khi load YOLO `.pt`: nếu file cấu hình không load được, detector thử các model cùng thư mục theo thứ tự `yolov8x.pt`, `yolov8l.pt`, `yolov8m.pt`, `yolov8s.pt`, `yolov8n.pt` nếu file tồn tại.

### Backend inference

Source hỗ trợ các backend inference: `pytorch`, `tensorrt`, `openvino`, `onnxruntime`.

Backend thực sự được chọn theo cả cấu hình và định dạng weight:

- `pytorch`: `.pt`
- `tensorrt`: `.engine`
- `openvino`: `.xml` hoặc thư mục `_openvino_model`
- `onnxruntime`: `.onnx`

Nếu cấu hình backend không khớp định dạng weight, hệ thống fallback về `pytorch`.

### OCR biển số

Pipeline biển số gồm hai bước:

- Detector biển số YOLOv8: `license_plate.detector_weights_path`, mặc định `backend/license_plate_yolov8.pt`.
- OCR text: `license_plate.ocr_backend`, hỗ trợ `paddleocr` và `easyocr`.

Cấu hình hiện tại bật OCR biển số:

```json
{
  "license_plate": {
    "enabled": true,
    "detector_weights_path": "backend/license_plate_yolov8.pt",
    "detector_allowed_classes": ["license_plate", "License Plates"],
    "ocr_backend": "paddleocr",
    "easyocr_lang": "en",
    "easyocr_use_gpu": true,
    "paddle_ocr_version": "PP-OCRv5",
    "paddle_text_detection_model_name": "PP-OCRv5_mobile_det",
    "paddle_text_recognition_model_name": "PP-OCRv5_mobile_rec",
    "paddle_lang": "en",
    "paddle_use_gpu": false
  }
}
```

Đổi sang EasyOCR:

```json
{
  "license_plate": {
    "enabled": true,
    "ocr_backend": "easyocr",
    "easyocr_lang": "en",
    "easyocr_use_gpu": true
  }
}
```

Lưu ý:

- `easyocr_lang` có thể chứa nhiều ngôn ngữ, phân tách bằng dấu phẩy, dấu chấm phẩy hoặc khoảng trắng.
- `detector_allowed_classes` phải khớp tên class biển số của model detector. Backend normalize tên class để giảm lỗi hoa/thường/khoảng trắng/gạch dưới.
- Nếu detector/OCR khởi tạo lỗi, backend tự tắt nhánh biển số để pipeline vi phạm chính vẫn chạy.

## Cấu Hình Hệ Thống

### File cấu hình chính

| File | Mục đích |
|---|---|
| `config/cameras.json` | Camera, nguồn video, frame size, vị trí và lane đang giám sát. |
| `config/lane_configs/<camera_id>.json` | Polygon lane, approach/commit, direction rule, maneuver và luật theo lane. |
| `config/settings.json` | Model, tracking, lane smoothing, violation thresholds, evidence, OCR, WebSocket, UI, analytics. |

### `config/cameras.json`

| Trường | Ý nghĩa |
|---|---|
| `camera_id` | ID duy nhất của camera. |
| `rtsp_url` | Nguồn video `rtsp://`, `rtsps://`, `http://`, `https://` hoặc file local. |
| `camera_type` | `roadside`, `overhead`, `intersection`. |
| `view_direction` | Mô tả hướng nhìn. |
| `location` | Tên đường, nút giao, GPS. |
| `monitored_lanes` | Danh sách lane ID thuộc camera. |
| `frame_width`, `frame_height` | Kích thước frame chuẩn để xử lý và denormalize polygon. |

### `config/lane_configs/<camera_id>.json`

| Trường | Ý nghĩa |
|---|---|
| `lanes[].polygon` | Biên lane chính dùng để gán xe vào làn. |
| `lanes[].approach_zone` | Vùng tiếp cận để khóa lane nguồn khi xét maneuver. |
| `lanes[].commit_gate` | Polygon xác nhận xe bắt đầu maneuver. |
| `lanes[].commit_line` | Vạch xác nhận xe bắt đầu maneuver. |
| `lanes[].allowed_lane_changes` | Lane đích hợp lệ khi chuyển làn. |
| `lanes[].allowed_vehicle_types` | Loại xe được phép trong lane. |
| `lanes[].allowed_maneuvers` | Maneuver được phép theo lane. Nếu không khai báo, backend suy ra từ `maneuvers.*.allowed`. |
| `lanes[].direction_rule.enabled` | Bật/tắt phát hiện ngược chiều. |
| `lanes[].direction_rule.direction_path` | Polyline hướng đúng chiều. |
| `lanes[].direction_rule.check_zone` | Vùng áp dụng đánh giá hướng. |
| `lanes[].maneuvers.<maneuver>.turn_zone` | Vùng hỗ trợ nhận diện hướng rẽ/đi thẳng/quay đầu. |
| `lanes[].maneuvers.<maneuver>.exit_line` | Vạch đầu ra xác nhận maneuver. |
| `lanes[].maneuvers.<maneuver>.exit_zone` | Vùng đầu ra xác nhận maneuver. |

Maneuver hợp lệ: `straight`, `right`, `left`, `u_turn`.

### `config/settings.json`

Các nhóm chính:

| Nhóm | Nội dung |
|---|---|
| `database` | Đường dẫn SQLite. |
| `camera.stream` | Delay reconnect video source. |
| `detection` | YOLO weights, backend, device, confidence, IoU, allowed classes. |
| `tracking` | ByteTrack config, stable track, smoothing loại xe. |
| `lane_assignment` | Cửa sổ temporal lane, majority hit, switch delay, overlap preference. |
| `wrong_lane` | Thời gian tối thiểu trước khi xác nhận sai làn. |
| `direction_detection` | Ngưỡng cosine, displacement, warmup, consensus, vector blending cho ngược chiều. |
| `turn_detection` | Heading, curvature, trajectory và fallback reference cho maneuver. |
| `evidence_fusion` | Line crossing, decay/score/threshold cho maneuver evidence. |
| `event_lifecycle` | Chống phát trùng và prune state runtime. |
| `geometry` | Crop ảnh bằng chứng và JPEG quality. |
| `performance` | Preview FPS/JPEG quality, cửa sổ tính processing FPS. |
| `websocket` | Chu kỳ push track và queue listener. |
| `ui.monitoring` | Cấu hình trajectory, highlight violation, trạng thái FPS cho UI. |
| `license_plate` | Detector biển số, OCR backend, interval đọc, consensus, crop và JPEG quality. |
| `analytics` | Granularity/tick/marker cho dashboard time series. |
| `logging` | Key dự phòng cho logging. |

## Các Loại Vi Phạm

| Mã | Ý nghĩa | Logic chính |
|---|---|---|
| `wrong_lane` | Xe chuyển sang lane không được phép. | Stable lane transition + `allowed_lane_changes` + duration/evidence. |
| `wrong_direction` | Xe đi ngược chiều. | `direction_rule` + trajectory vector + cosine/consensus/candidate duration. |
| `vehicle_type_not_allowed` | Loại phương tiện không được phép trong lane. | `allowed_vehicle_types` theo lane. |
| `turn_left_not_allowed` | Rẽ trái không đúng quy định. | Evidence fusion xác nhận `left`, so với `allowed_maneuvers`. |
| `turn_right_not_allowed` | Rẽ phải không đúng quy định. | Evidence fusion xác nhận `right`, so với `allowed_maneuvers`. |
| `turn_straight_not_allowed` | Đi thẳng không đúng quy định. | Evidence fusion xác nhận `straight`, so với `allowed_maneuvers`. |
| `turn_u_turn_not_allowed` | Quay đầu không đúng quy định. | Heading change, curvature, opposite direction và exit/zone evidence. |

## API Và WebSocket

### REST API

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/api/health` | Healthcheck backend. |
| `GET` | `/api/cameras` | Danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Chi tiết camera, lane config, validation, runtime status, UI config. |
| `POST` | `/api/cameras` | Tạo camera kèm lane config. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera và lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera, lane config, ảnh nền và evidence liên quan. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lane polygons dạng pixel cho overlay/editor. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Trajectory runtime gần đây, có `limit`, `lane_id`, `vehicle_type`. |
| `GET` | `/api/cameras/{camera_id}/preview` | MJPEG preview. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền `.jpg`/`.png`. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh evidence hoặc ảnh crop biển số. |
| `GET` | `/api/violations/history` | Lịch sử vi phạm, filter theo camera, biển số, thời gian, limit. |
| `GET` | `/api/violations/export` | Export CSV/XLSX. |
| `GET` | `/api/analytics/dashboard` | Dashboard overview, summary và time series. |
| `GET` | `/api/stats` | Thống kê count theo filter thời gian. |

### WebSocket

| Endpoint | Payload | Chức năng |
|---|---|---|
| `WS /ws/tracks?camera_id=...` | `TrackMessage` | Xe realtime, bbox, lane, raw lane, biển số, direction status, FPS. |
| `WS /ws/violations?camera_id=...` | `{ "type": "violation", "event": ... }` | Sự kiện vi phạm realtime. |

## Kiểm Thử

Backend:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

Kiểm tra cú pháp backend:

```powershell
python -m compileall backend/app
```

Frontend:

```powershell
cd frontend
npm run build
```

## Tài Liệu Liên Quan

- [Phân tích kỹ thuật hệ thống](SYSTEM_TECHNICAL_ANALYSIS.md)
- [Backend README](backend/README.md)
- [Frontend README](frontend/README.md)
