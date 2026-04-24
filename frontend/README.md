# Frontend

Frontend là ứng dụng React + Vite cho 3 màn hình:

- Giám sát realtime.
- Thống kê lịch sử vi phạm.
- Quản lý camera và cấu hình lane/maneuver.

Frontend không chạy AI; toàn bộ dữ liệu lấy từ backend qua REST/WebSocket/MJPEG.

## Chạy frontend

```powershell
cd frontend
npm install
npm run dev
```

Mặc định chạy ở `http://localhost:5173`.

## Cấu hình kết nối backend

| Biến | Giải thích |
|---|---|
| `VITE_API_BASE` | Base URL backend. Nếu không đặt, frontend dùng `http://localhost:8000`. |

Ví dụ `.env`:

```env
VITE_API_BASE=http://localhost:8000
```

## Mô hình `.pt` và cách chạy (liên quan frontend)

Frontend không tải/chạy model YOLO trực tiếp. Model `.pt` chạy hoàn toàn ở backend.

Để đổi model:

1. Tải model `.pt` về máy (ví dụ `backend/yolov8m.pt`).
2. Sửa `config/settings.json` tại `detection.weights_path`.
3. Restart backend.

Sau khi backend đổi model, frontend tự nhận kết quả mới qua API/WS, không cần build lại frontend.

## Cấu hình người dùng thao tác trên UI (lane-centric + maneuver-centric)

Phần này đồng bộ với schema backend `config/lane_configs/<camera_id>.json`.

### Nhóm thông tin từng làn

| Trường | Giải thích |
|---|---|
| `polygon` | Biên làn để gán lane cho xe. |
| `approach_zone` | Vùng xe đi vào trước khi rẽ, dùng để khóa làn nguồn. |
| `commit_gate` | Vùng xác nhận xe bắt đầu thực hiện hướng đi (tùy chọn). |
| `commit_line` | Vạch xác nhận xe bắt đầu thực hiện hướng đi (tùy chọn). |
| `allowed_lane_changes` | Làn được phép chuyển tới (quy tắc cho lỗi `wrong_lane`). |
| `allowed_vehicle_types` | Loại xe hợp lệ trong lane. |
| `allowed_maneuvers` | Danh sách maneuver hợp lệ theo luật cho lane. |

### Maneuver configuration (`straight`, `left`, `right`, `u_turn`)

| Trường | Giải thích |
|---|---|
| `enabled` | Bật/tắt nhận diện maneuver. |
| `allowed` | Cho phép/cấm maneuver theo luật. |
| `movement_path` | Quỹ đạo kỳ vọng (polyline) để backend suy corridor/evidence. |
| `corridor_preset` | Preset corridor (`narrow`, `normal`, `wide`). |
| `corridor_width_px` | Độ rộng corridor (px), có thể để backend tự gán theo preset. |
| `turn_corridor` | Polygon corridor; nếu không có và có `movement_path`, backend có thể tự sinh. |
| `exit_line` | Vạch xác nhận xe đã đi ra đúng nhánh của maneuver. |
| `exit_zone` | Vùng xác nhận xe đã đi ra đúng nhánh của maneuver. |

## Ý nghĩa các trường analytics chart mà frontend sử dụng

Các trường này backend trả về qua `chart_config` và frontend dùng để chọn granularity/tick:

| Key | Giải thích |
|---|---|
| `minute_granularity_max_range_hours` | Tối đa bao nhiêu giờ thì biểu đồ giữ granularity theo phút. |
| `hour_granularity_max_range_days` | Tối đa bao nhiêu ngày thì giữ granularity theo giờ. |
| `day_granularity_max_range_days` | Tối đa bao nhiêu ngày thì giữ granularity theo ngày. |
| `week_granularity_max_range_days` | Tối đa bao nhiêu ngày thì giữ granularity theo tuần. |
| `minute_axis_label_interval_minutes` | Khoảng cách nhãn phút trên trục X. |
| `minute_axis_max_ticks` | Số tick tối đa khi vẽ minute chart. |
| `hour_axis_max_ticks` | Số tick tối đa khi vẽ hour chart. |
| `overview_axis_max_ticks` | Số tick tối đa khi vẽ day/week/month chart. |
| `point_markers_max_points` | Ngưỡng số điểm để frontend quyết định hiển thị marker. |

## Cấu hình UI monitoring nhận từ backend

`GET /api/cameras/{camera_id}` trả thêm `ui.monitoring`; frontend dùng để điều chỉnh hiển thị realtime mà không cần hard-code trong React.

| Key | Giải thích |
|---|---|
| `trajectory.default_limit` | Số quỹ đạo mặc định hiển thị trên màn hình giám sát. |
| `trajectory.min_limit` / `trajectory.max_limit` | Giới hạn cho bộ chọn số quỹ đạo. |
| `trajectory.max_points_per_vehicle` | Số điểm trajectory tối đa giữ trên mỗi xe. |
| `trajectory.stale_ms` | Thời gian không cập nhật thì trajectory bị coi là cũ. |
| `trajectory.min_point_distance_px` | Khoảng cách tối thiểu giữa 2 điểm để tránh vẽ nhiễu. |
| `violation.list_max_rows` | Số vi phạm realtime tối đa giữ trên danh sách. |
| `violation.highlight_duration_ms` | Thời gian highlight vi phạm mới. |
| `processing_fps.stale_after_ms` / `processing_fps.poll_interval_ms` | Ngưỡng stale và chu kỳ poll FPS xử lý. |

## API/WS mà frontend gọi (kèm giải thích)

| Method | Endpoint | Giải thích |
|---|---|---|
| `GET` | `/api/health` | Kiểm tra backend sống trước khi vận hành. |
| `GET` | `/api/cameras` | Lấy danh sách camera để render dropdown. |
| `GET` | `/api/cameras/{camera_id}` | Lấy chi tiết camera + lane config + validation + cấu hình UI monitoring. |
| `POST` | `/api/cameras` | Tạo camera mới từ màn hình quản lý. |
| `PUT` | `/api/cameras/{camera_id}` | Lưu cập nhật camera/lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lấy lane theo pixel để vẽ canvas nhanh. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Lấy trajectory theo track để vẽ nét xanh trên màn hình giám sát. |
| `GET` | `/api/cameras/{camera_id}/preview` | Stream MJPEG cho ảnh camera realtime. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền để căn hình học dễ hơn. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền camera. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền camera. |
| `GET` | `/api/violations/history` | Lấy lịch sử vi phạm có lọc thời gian/camera. |
| `GET` | `/api/violations/export` | Export lịch sử sang CSV/XLSX. |
| `GET` | `/api/analytics/dashboard` | Lấy số liệu dashboard và time series. |
| `GET` | `/api/stats` | Lấy thống kê tổng hợp theo thời gian. |

| WebSocket | Giải thích |
|---|---|
| `WS /ws/tracks?camera_id=...` | Nhận track realtime để vẽ bbox/lane overlay. |
| `WS /ws/violations?camera_id=...` | Nhận vi phạm realtime để cập nhật danh sách sự kiện. |

## Tổ chức source chính

| File | Vai trò |
|---|---|
| `src/api.js` | Wrapper REST + WebSocket endpoint. |
| `src/utils.js` | Label, timezone VN, normalize geometry, validate draft, analytics helper. |
| `src/views/MonitoringView.jsx` | Màn hình giám sát realtime. |
| `src/views/AnalyticsView.jsx` | Màn hình thống kê + lịch sử + export. |
| `src/views/ManagementView.jsx` | Màn hình cấu hình camera/lane/maneuver. |
| `src/components/CameraCanvas.jsx` | Canvas overlay + editor geometry. |

## Build production

```powershell
cd frontend
npm run build
npm run preview
```

## Tài liệu liên quan

- Backend chi tiết: [backend/README.md](../backend/README.md)
- Tài liệu tổng quan: [README.md](../README.md)
