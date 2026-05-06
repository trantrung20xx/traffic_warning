# Frontend

Frontend là ứng dụng React + Vite dùng để vận hành hệ thống Traffic Warning qua trình duyệt. Frontend không chạy AI trực tiếp; toàn bộ detector, tracker, OCR và logic vi phạm nằm ở backend.

## Mục Lục

- [Chức năng chính](#chức-năng-chính)
- [Chạy frontend](#chạy-frontend)
- [Kết nối backend](#kết-nối-backend)
- [Model AI](#model-ai)
- [Các màn hình](#các-màn-hình)
- [Cấu hình trên UI](#cấu-hình-trên-ui)
- [API frontend sử dụng](#api-frontend-sử-dụng)
- [Tổ chức source](#tổ-chức-source)
- [Build production](#build-production)

## Chức Năng Chính

Frontend có 3 màn hình:

| Màn hình | Vai trò |
|---|---|
| Giám sát | Xem camera realtime, bbox xe, lane, trajectory, biển số, trạng thái hướng và vi phạm mới. |
| Thống kê | Xem dashboard, biểu đồ, lịch sử vi phạm, lọc theo camera/thời gian/biển số và export CSV/XLSX. |
| Quản lý | Tạo/sửa/xóa camera, upload ảnh nền, cấu hình lane polygon, hướng đúng chiều, maneuver và luật theo làn. |

Dữ liệu frontend nhận từ backend qua:

- REST API cho cấu hình, lịch sử, dashboard, export.
- WebSocket cho track và vi phạm realtime.
- MJPEG endpoint cho preview camera.

## Chạy Frontend

```powershell
cd frontend
npm install
npm run dev
```

Mặc định frontend chạy tại:

```text
http://localhost:5173
```

## Kết Nối Backend

Frontend đọc backend base URL từ biến môi trường `VITE_API_BASE`.

Nếu không khai báo, mặc định là:

```text
http://localhost:8000
```

Ví dụ `frontend/.env`:

```env
VITE_API_BASE=http://localhost:8000
```

Sau khi đổi `.env`, restart `npm run dev`.

## Model AI

Frontend không tải hoặc chạy model `.pt`. Khi cần đổi model, thực hiện ở backend:

1. Tải model `.pt`.
2. Đặt file vào thư mục phù hợp, thường là `backend/`.
3. Sửa `config/settings.json`.
4. Restart backend.

Các link model YOLOv8 chính thức đang dùng trong tài liệu dự án:

| Model | Link tải |
|---|---|
| `yolov8n.pt` | [Tải yolov8n.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) |
| `yolov8s.pt` | [Tải yolov8s.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt) |
| `yolov8m.pt` | [Tải yolov8m.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt) |
| `yolov8l.pt` | [Tải yolov8l.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8l.pt) |
| `yolov8x.pt` | [Tải yolov8x.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt) |

Ví dụ cấu hình backend:

```json
{
  "detection": {
    "weights_path": "backend/yolov8m.pt",
    "allowed_classes": ["motorcycle", "car", "truck", "bus"]
  }
}
```

`allowed_classes` là danh sách class detector thật sự nhận diện/tracking. Khác với `allowed_vehicle_types` trên UI quản lý lane, vốn chỉ là luật xe nào được phép đi trong lane.

## Các Màn Hình

### Giám sát realtime

Màn hình giám sát hiển thị:

- Video preview từ `/api/cameras/{camera_id}/preview`.
- Bbox phương tiện và label loại xe/biển số.
- Lane ổn định, lane raw và trạng thái hướng.
- Trajectory gần đây của xe.
- Danh sách vi phạm realtime.
- FPS xử lý do backend gửi.

Các tham số hiển thị trajectory, highlight vi phạm và FPS được backend trả qua `ui.monitoring` để tránh hard-code trong frontend.

### Thống kê và lịch sử

Màn hình thống kê hỗ trợ:

- Lọc theo camera.
- Lọc theo khoảng thời gian.
- Lọc lịch sử theo biển số.
- Xem biểu đồ time series với granularity do frontend chọn theo `analytics.chart`.
- Export CSV/XLSX từ `/api/violations/export`.
- Mở modal chi tiết vi phạm, gồm ảnh bằng chứng và ảnh crop biển số nếu có.

### Quản lý camera và lane

Màn hình quản lý hỗ trợ:

- Tạo, sửa, xóa camera.
- Upload/xóa ảnh nền để căn chỉnh hình học.
- Vẽ/sửa `lane_polygon`, `approach_zone`, `commit_gate`, `commit_line`.
- Vẽ/sửa `direction_path` và `direction_check_zone` cho phát hiện đi ngược chiều.
- Vẽ/sửa `turn_zone`, `exit_line`, `exit_zone` cho từng maneuver.
- Cấu hình loại phương tiện được phép trong lane.
- Cấu hình lane được phép chuyển sang.
- Cấu hình hướng đi được phép/cấm.
- Undo/redo thao tác polygon tối đa 80 bước trong bộ nhớ frontend.

## Cấu Hình Trên UI

### Camera

| Trường | Ý nghĩa |
|---|---|
| `camera_id` | ID duy nhất của camera. |
| `rtsp_url` | Nguồn video backend đọc. |
| `camera_type` | Loại camera: `roadside`, `overhead`, `intersection`. |
| `view_direction` | Mô tả hướng nhìn. |
| `location` | Tên đường, nút giao và GPS nếu có. |
| `monitored_lanes` | Lane ID camera giám sát. |
| `frame_width`, `frame_height` | Kích thước frame dùng để chuẩn hóa/giải chuẩn hóa polygon. |

### Lane

| Trường | Ý nghĩa |
|---|---|
| `polygon` | Biên lane để backend gán xe vào lane. |
| `allowed_lane_changes` | Lane đích xe được phép chuyển sang. |
| `allowed_vehicle_types` | Loại xe được phép trong lane. Nếu xe vẫn được detector nhận diện nhưng không nằm trong danh sách này, backend có thể phát `vehicle_type_not_allowed`. |
| `direction_rule` | Quy tắc đi đúng chiều của lane. |
| `maneuvers` | Cấu hình các hướng `straight`, `left`, `right`, `u_turn`. |

### Maneuver

| Trường | Ý nghĩa |
|---|---|
| `enabled` | Bật/tắt nhận diện hướng này. |
| `allowed` | Hướng này có được phép theo luật hay không. |
| `turn_zone` | Vùng rẽ dùng để khớp trajectory của xe theo từng hướng. |
| `exit_line` | Vạch xác nhận xe ra đúng nhánh. |
| `exit_zone` | Vùng xác nhận xe ra đúng nhánh. |

### Biển số

Frontend hiển thị các trạng thái OCR do backend trả:

| Trạng thái | Ý nghĩa |
|---|---|
| `pending` | Đang chờ đủ dữ liệu OCR. |
| `confirmed` | Biển số đã đủ số lần xác nhận. |
| `uncertain` | Có kết quả nhưng chưa đủ tin cậy. |
| `unreadable` | Đã thử nhiều lần nhưng không đọc được. |

## API Frontend Sử Dụng

| Method | Endpoint | Dùng để |
|---|---|---|
| `GET` | `/api/health` | Kiểm tra backend. |
| `GET` | `/api/cameras` | Lấy danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Lấy chi tiết camera, lane config, validation và UI config. |
| `POST` | `/api/cameras` | Tạo camera. |
| `PUT` | `/api/cameras/{camera_id}` | Lưu cấu hình camera/lane. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lấy lane dạng pixel để overlay. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Lấy trajectory gần đây. |
| `GET` | `/api/cameras/{camera_id}/preview` | Video preview MJPEG. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh bằng chứng hoặc ảnh biển số. |
| `GET` | `/api/violations/history` | Lấy lịch sử vi phạm. |
| `GET` | `/api/violations/export` | Export CSV/XLSX. |
| `GET` | `/api/analytics/dashboard` | Lấy dashboard và time series. |

| WebSocket | Dùng để |
|---|---|
| `WS /ws/tracks?camera_id=...` | Nhận track realtime. |
| `WS /ws/violations?camera_id=...` | Nhận vi phạm realtime. |

## Tổ Chức Source

| File | Vai trò |
|---|---|
| `src/api.js` | Wrapper REST API, WebSocket và URL ảnh. |
| `src/utils.js` | Label tiếng Việt, timezone, normalize geometry, validate draft, helper chart. |
| `src/App.jsx` | Shell chính và chuyển tab màn hình. |
| `src/views/MonitoringView.jsx` | Màn hình giám sát realtime. |
| `src/views/AnalyticsView.jsx` | Dashboard, lịch sử, export. |
| `src/views/ManagementView.jsx` | Quản lý camera/lane/maneuver. |
| `src/components/CameraCanvas.jsx` | Canvas video overlay và editor hình học. |
| `src/components/canvas/PolygonLayer.js` | Hàm vẽ polygon, polyline và marker chỉnh sửa. |

## Build Production

```powershell
cd frontend
npm run build
npm run preview
```

## Tài Liệu Liên Quan

- [README tổng quan](../README.md)
- [Backend README](../backend/README.md)
