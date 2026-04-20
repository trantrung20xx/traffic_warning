# Frontend

Frontend là ứng dụng React + Vite dùng để giám sát, thống kê và quản lý cấu hình camera/làn đường. Frontend không chạy AI; toàn bộ dữ liệu được lấy từ backend qua REST API, WebSocket và luồng MJPEG.

## Công nghệ

- React 18
- Vite 5
- JavaScript thuần, không dùng TypeScript
- Canvas 2D để vẽ polygon làn, turn region và overlay xe

## Cài đặt

```powershell
cd frontend
npm install
```

## Chạy môi trường phát triển

```powershell
cd frontend
npm run dev
```

Mặc định Vite chạy ở `http://localhost:5173`.

## Build production

```powershell
cd frontend
npm run build
```

Xem bản build:

```powershell
npm run preview
```

## Cấu hình kết nối backend

Frontend dùng biến môi trường:

- `VITE_API_BASE`

Nếu không cấu hình, frontend sẽ mặc định gọi:

```text
http://localhost:8000
```

Ví dụ file `.env`:

```env
VITE_API_BASE=http://localhost:8000
```

## Các màn hình hiện có

### 1. Giám sát

- Chọn camera đang xem.
- Hiển thị metadata camera:
  - `camera_id`
  - loại camera
  - hướng quan sát
  - tuyến đường / nút giao
- Xem preview MJPEG từ backend qua `GET /api/cameras/{camera_id}/preview`.
- Overlay polygon làn lên ảnh camera.
- Nhận track realtime qua `WS /ws/tracks?camera_id=...`.
- Nhận vi phạm realtime qua `WS /ws/violations?camera_id=...`.
- Đánh dấu xe vừa vi phạm trong overlay.
- Mở modal chi tiết vi phạm và ảnh bằng chứng.

### 2. Thống kê

- Lọc theo camera hoặc toàn hệ thống.
- Lọc khoảng thời gian theo giờ Việt Nam.
- Tự bám thời gian hiện tại nếu mốc `to` gần với hiện tại.
- Lấy dashboard từ `GET /api/analytics/dashboard`.
- Lấy lịch sử từ `GET /api/violations/history`.
- Tự refresh khi có vi phạm mới qua `WS /ws/violations`.
- Hiển thị:
  - tổng số vi phạm
  - số camera có vi phạm
  - biểu đồ theo camera
  - biểu đồ theo loại xe
  - biểu đồ theo loại vi phạm
  - biểu đồ theo khu vực
  - chuỗi thời gian theo giờ
  - bảng lịch sử vi phạm
- Export lịch sử qua:
  - `GET /api/violations/export?format=csv`
  - `GET /api/violations/export?format=xlsx`

### 3. Quản lý camera

- Xem danh sách camera hiện có.
- Thêm camera mới.
- Chỉnh sửa camera hiện có.
- Xóa camera.
- Sửa metadata:
  - `camera_id`
  - `rtsp_url`
  - `camera_type`
  - `view_direction`
  - `road_name`
  - `intersection_name`
  - `gps_lat`
  - `gps_lng`
  - `frame_width`
  - `frame_height`
- Chỉnh polygon làn và `turn_regions` trên canvas.
- Thêm/xóa làn.
- Chọn:
  - `allowed_lane_changes`
  - `allowed_vehicle_types`
  - `allowed_maneuvers`
- Upload/xóa ảnh nền camera:
  - `POST /api/camera/{camera_id}/background-image`
  - `DELETE /api/camera/{camera_id}/background-image`
- Khóa/mở khóa chỉnh sửa polygon để tránh kéo nhầm.
- Kiểm tra cảnh báo polygon tự cắt hoặc chưa đủ số điểm trước khi lưu.

## Tổ chức source chính

- `src/App.jsx`: điều phối 3 màn hình chính.
- `src/api.js`: toàn bộ hàm gọi API/WS.
- `src/utils.js`: nhãn hiển thị, thời gian Việt Nam, chuẩn hóa polygon, validate polygon.
- `src/views/MonitoringView.jsx`: màn hình giám sát.
- `src/views/AnalyticsView.jsx`: màn hình thống kê.
- `src/views/ManagementView.jsx`: màn hình quản lý camera và polygon.
- `src/components/CameraCanvas.jsx`: canvas hiển thị overlay và chỉnh sửa polygon.
- `src/components/ViolationDetailModal.jsx`: modal xem chi tiết vi phạm.

## Luồng dữ liệu frontend

- Danh sách camera: `GET /api/cameras`
- Chi tiết camera + lane config: `GET /api/cameras/{camera_id}`
- Ảnh nền camera: `GET /api/camera/{camera_id}/background-image`
- Preview camera: `GET /api/cameras/{camera_id}/preview`
- Track realtime: `WS /ws/tracks`
- Vi phạm realtime: `WS /ws/violations`
- Dashboard: `GET /api/analytics/dashboard`
- Lịch sử vi phạm: `GET /api/violations/history`
- Export: `GET /api/violations/export`

## Ghi chú

- Frontend hiển thị thời gian theo múi giờ Việt Nam (`Asia/Ho_Chi_Minh`).
- Polygon trong state frontend được giữ ở dạng chuẩn hóa; khi vẽ mới đổi sang pixel.
- Ảnh bằng chứng có thể là `image_url` tuyệt đối hoặc đường dẫn backend tương đối; `api.js` đã xử lý cả hai trường hợp.
