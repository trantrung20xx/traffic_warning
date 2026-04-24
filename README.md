# Traffic Warning

Hệ thống giám sát và cảnh báo vi phạm giao thông thời gian thực gồm:

- `backend`: FastAPI + YOLOv8 + ByteTrack + bộ luật nhận diện vi phạm.
- `frontend`: React cho giám sát realtime, thống kê, quản lý cấu hình camera.
- `config`: dữ liệu cấu hình camera/làn, ảnh nền, ảnh bằng chứng, SQLite.

## Khởi chạy nhanh

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Mặc định:

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

## Mô hình YOLO `.pt` (tải và sử dụng)

### Link tải model chính thức

Nguồn: Ultralytics Assets (GitHub Releases).

- `yolov8n.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt`
- `yolov8s.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt`
- `yolov8m.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt`
- `yolov8x.pt`: `https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt`

### Tải nhanh bằng PowerShell

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

### Chọn model nào?

- `yolov8n.pt`: nhanh nhất, nhẹ nhất, phù hợp máy yếu hoặc nhiều camera.
- `yolov8s.pt`: cân bằng tốc độ/chất lượng cho đa số trường hợp.
- `yolov8m.pt`: chính xác cao hơn nhưng nặng hơn.
- `yolov8x.pt`: nặng nhất, cần GPU mạnh.

### Cấu hình model trong hệ thống

Sửa `config/settings.json`:

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

Ý nghĩa nhanh:

- `weights_path`: đường dẫn model `.pt`.
- `device`: `auto` (ưu tiên GPU nếu có), hoặc `cpu`, `cuda`, `cuda:0`.
- `confidence_threshold`: tăng thì ít false positive hơn nhưng dễ miss vật thể nhỏ.
- `iou_threshold`: điều khiển NMS.

### Cài PyTorch theo phần cứng

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA (ví dụ CUDA 13.0):

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

### Kiểm tra model đã chạy đúng chưa

Khi backend start, log camera sẽ in thông tin detector và device thực tế (requested/resolved).
Nếu thiếu model hoặc sai đường dẫn `weights_path`, backend sẽ báo lỗi ngay ở bước khởi tạo detector.

## Mô hình cấu hình hiện tại

Hệ thống dùng mô hình `lane-centric + maneuver-centric`:

- Mỗi camera có nhiều làn đường.
- Mỗi lane có quy tắc đổi làn, loại xe được phép và các maneuver.
- Mỗi maneuver có evidence hình học riêng (`movement_path`, `exit_line`, `exit_zone`, `turn_corridor`).
- Backend tự suy luận heading/curvature/opposite-direction từ trajectory, frontend không nhập tay các ngưỡng này.

## 1) `config/cameras.json` (metadata camera)

Mỗi phần tử trong `cameras`:

| Trường | Giải thích |
|---|---|
| `camera_id` | ID duy nhất của camera, dùng làm khóa cho API, lane config, ảnh nền, ảnh bằng chứng. |
| `rtsp_url` | Nguồn video: `rtsp://`, `rtsps://`, `http://`, `https://` hoặc đường dẫn file local. |
| `camera_type` | Loại camera (`roadside`, `overhead`, `intersection`) để hiển thị/nghiệp vụ. |
| `view_direction` | Mô tả hướng nhìn camera (chuỗi tự do). |
| `location.road_name` | Tên tuyến đường. |
| `location.intersection_name` | Tên nút giao/ngã tư (nếu có). |
| `location.gps_lat` | Vĩ độ vị trí camera (nếu có). |
| `location.gps_lng` | Kinh độ vị trí camera (nếu có). |
| `monitored_lanes` | Danh sách `lane_id` camera này quản lý; phải khớp lane config tương ứng. |
| `frame_width` | Chiều rộng frame runtime (pixel). |
| `frame_height` | Chiều cao frame runtime (pixel). |

## 2) `config/lane_configs/<camera_id>.json` (cấu hình làn + hành vi)

Toàn bộ tọa độ lưu theo chuẩn hóa `[0, 1]`.

### Nhóm thông tin camera

| Trường | Giải thích |
|---|---|
| `camera_id` | Phải trùng với `camera_id` trong `cameras.json`. |
| `frame_width` | Kích thước tham chiếu khi chuẩn hóa/giải chuẩn hóa polygon. |
| `frame_height` | Kích thước tham chiếu khi chuẩn hóa/giải chuẩn hóa polygon. |
| `lanes` | Danh sách lane của camera. |

### Nhóm thông tin từng làn

| Trường | Giải thích |
|---|---|
| `lane_id` | ID làn duy nhất trong camera. |
| `polygon` | Biên làn dùng để gán lane. |
| `approach_zone` | Vùng xe đi vào trước khi rẽ, dùng để khóa làn nguồn (tùy chọn). |
| `commit_gate` | Vùng xác nhận xe bắt đầu thực hiện hướng đi (tùy chọn). |
| `commit_line` | Vạch xác nhận xe bắt đầu thực hiện hướng đi (tùy chọn). |
| `allowed_lane_changes` | Danh sách lane xe được phép chuyển sang; dùng cho `wrong_lane`. |
| `allowed_vehicle_types` | Loại xe được phép chạy trong lane (`motorcycle`, `car`, `truck`, `bus`). |
| `allowed_maneuvers` | Danh sách maneuver hợp lệ theo luật ở lane này; thường được suy ra từ `maneuvers.*.allowed`. |
| `maneuvers` | Maneuver configuration cho `straight`, `left`, `right`, `u_turn`. |

### Maneuver configuration (`lanes[].maneuvers.<maneuver>`)

| Trường | Giải thích |
|---|---|
| `enabled` | Bật/tắt nhận diện maneuver này. |
| `allowed` | Cho phép hoặc cấm maneuver này theo luật. |
| `movement_path` | Quỹ đạo kỳ vọng của xe khi thực hiện maneuver này (polyline). |
| `corridor_preset` | Preset độ rộng corridor (`narrow`, `normal`, `wide`). |
| `corridor_width_px` | Độ rộng corridor theo pixel; có thể tự suy ra từ preset. |
| `turn_corridor` | Polygon corridor. Nếu không cung cấp và có `movement_path`, hệ thống tự sinh. |
| `exit_line` | Vạch xác nhận xe đã đi ra đúng nhánh của maneuver này. |
| `exit_zone` | Vùng xác nhận xe đã đi ra đúng nhánh của maneuver này. |

## 3) `config/settings.json` (tham số runtime)

### `database`

| Key | Giải thích |
|---|---|
| `database.path` | Đường dẫn file SQLite. |

### `camera.stream`

| Key | Giải thích |
|---|---|
| `camera.stream.rtsp_reconnect_delay_s` | Thời gian chờ trước khi thử kết nối lại nguồn video khi lỗi. |

### `detection`

| Key | Giải thích |
|---|---|
| `detection.weights_path` | Đường dẫn model YOLO `.pt`. |
| `detection.device` | Thiết bị suy luận (`auto`, `cpu`, `cuda`, `cuda:0`, ...). |
| `detection.confidence_threshold` | Ngưỡng confidence tối thiểu của detector. |
| `detection.iou_threshold` | Ngưỡng IoU NMS cho detector. |

### `tracking`

| Key | Giải thích |
|---|---|
| `tracking.tracker_config` | File cấu hình ByteTrack. |
| `tracking.vehicle_type_history.window_ms` | Cửa sổ thời gian để làm mượt nhãn loại xe. |
| `tracking.vehicle_type_history.size` | Số mẫu tối đa lưu cho làm mượt loại xe. |
| `tracking.vehicle_type_history.recency_weight_bias` | Độ ưu tiên mẫu loại xe mới hơn khi vote nhãn ổn định. |
| `tracking.stable_track.max_idle_ms` | Thời gian track có thể mất trước khi coi là hết hiệu lực rebind. |
| `tracking.stable_track.min_iou_for_rebind` | IoU tối thiểu để nối lại track ổn định. |
| `tracking.stable_track.max_normalized_distance` | Khoảng cách chuẩn hóa tối đa để nối lại track ổn định. |

### `lane_assignment`

| Key | Giải thích |
|---|---|
| `lane_assignment.temporal.observation_window_ms` | Cửa sổ quan sát lane raw để tạo lane ổn định. |
| `lane_assignment.temporal.min_majority_hits` | Số hit tối thiểu để chốt lane theo majority. |
| `lane_assignment.temporal.switch_min_duration_ms` | Thời gian tối thiểu trước khi chấp nhận chuyển lane ổn định. |
| `lane_assignment.overlap_preference.preferred_lane_overlap_ratio` | Tỷ lệ overlap để ưu tiên lane đã ổn định khi bbox nằm trên nhiều lane. |
| `lane_assignment.overlap_preference.preferred_lane_overlap_margin_px` | Biên pixel hỗ trợ giữ lane ổn định khi overlap gần bằng nhau. |

### `wrong_lane`

| Key | Giải thích |
|---|---|
| `wrong_lane.min_duration_ms` | Thời gian vi phạm tối thiểu trước khi hệ thống phát lỗi `wrong_lane`. |

### `turn_detection`

| Key | Giải thích |
|---|---|
| `turn_detection.turn_region_min_hits` | Số lần xe phải xuất hiện trong corridor/zone khi chưa có bằng chứng đầu ra đủ mạnh. |
| `turn_detection.turn_state_timeout_ms` | Timeout reset turn state machine nếu không còn hoạt động. |
| `turn_detection.trajectory_history_window_ms` | Cửa sổ trajectory dùng cho heading/curvature và evidence. |
| `turn_detection.heading.*` | Ngưỡng heading/delta heading để hỗ trợ phân loại đi thẳng, rẽ và quay đầu. |
| `turn_detection.curvature.*` | Ngưỡng curvature hỗ trợ phân biệt đi thẳng, rẽ và quay đầu. |
| `turn_detection.opposite_direction.cos_threshold` | Ngưỡng cosine để nhận biết chuyển động ngược hướng khi xét quay đầu. |
| `turn_detection.trajectory.*` | Số mẫu trajectory dùng cho heading cục bộ, entry heading và hit trong polygon. |

### `evidence_fusion.line_crossing`

| Key | Giải thích |
|---|---|
| `evidence_fusion.line_crossing.side_tolerance_px` | Sai số cho phân loại phía của điểm so với line. |
| `evidence_fusion.line_crossing.min_pre_frames` | Số frame ổn định tối thiểu trước khi qua line. |
| `evidence_fusion.line_crossing.min_post_frames` | Số frame ổn định tối thiểu sau khi qua line. |
| `evidence_fusion.line_crossing.min_displacement_px` | Dịch chuyển tối thiểu (px) để xác nhận qua line. |
| `evidence_fusion.line_crossing.min_displacement_ratio` | Dịch chuyển tối thiểu theo tỷ lệ chiều dài line. |
| `evidence_fusion.line_crossing.max_gap_ms` | Gap tối đa giữa các mẫu crossing trước khi reset state. |
| `evidence_fusion.line_crossing.cooldown_ms` | Cooldown sau một lần crossing confirmed. |

### `evidence_fusion` (khác line crossing)

| Key | Giải thích |
|---|---|
| `evidence_fusion.evidence_expire_ms` | Thời gian evidence turn bị coi là stale và decay/xóa. |
| `evidence_fusion.motion_window_samples` | Số mẫu trajectory dùng để tính motion feature ngắn hạn. |
| `evidence_fusion.turn_scoring.*` | Weight, penalty, bonus và threshold score dùng để xác nhận maneuver. |

### `event_lifecycle`

| Key | Giải thích |
|---|---|
| `event_lifecycle.violation_rearm_window_ms` | Thời gian `re-arm` trước khi cho phép `emit` vi phạm mới trong lifecycle kế tiếp. |
| `event_lifecycle.state_prune_max_age_s` | Tuổi tối đa của vehicle state trước khi prune. |

### `websocket`

| Key | Giải thích |
|---|---|
| `websocket.track_push_interval_ms` | Chu kỳ tối thiểu đẩy track message realtime. |
| `websocket.listener_queue_maxsize` | Kích thước queue listener track/violation để tránh backlog realtime. |

### `performance`

| Key | Giải thích |
|---|---|
| `performance.preview.max_fps` | FPS tối đa của luồng preview MJPEG. |
| `performance.preview.jpeg_quality` | Chất lượng JPEG preview. |
| `performance.processing.fps_window_s` | Cửa sổ thời gian để tính `processing_fps`. |

### `geometry`

| Key | Giải thích |
|---|---|
| `geometry.evidence_crop.expand_x_ratio` | Tỷ lệ nới ngang bbox khi cắt ảnh bằng chứng. |
| `geometry.evidence_crop.expand_y_top_ratio` | Tỷ lệ nới lên trên bbox khi cắt ảnh bằng chứng. |
| `geometry.evidence_crop.expand_y_bottom_ratio` | Tỷ lệ nới xuống dưới bbox khi cắt ảnh bằng chứng. |
| `geometry.evidence_crop.min_size_px` | Kích thước crop tối thiểu; nhỏ hơn sẽ fallback dùng full frame. |
| `geometry.evidence_image.jpeg_quality` | Chất lượng JPEG của ảnh bằng chứng lưu đĩa. |

### `ui.monitoring`

| Key | Giải thích |
|---|---|
| `ui.monitoring.trajectory.*` | Giới hạn số quỹ đạo, số điểm, thời gian stale và khoảng cách điểm tối thiểu khi vẽ trajectory trên màn hình giám sát. |
| `ui.monitoring.violation.*` | Số dòng vi phạm realtime và thời gian highlight sự kiện mới. |
| `ui.monitoring.processing_fps.*` | Thời gian stale và chu kỳ poll FPS xử lý hiển thị trên UI. |

### `analytics.chart`

| Key | Giải thích |
|---|---|
| `analytics.chart.minute_granularity_max_range_hours` | Khoảng lọc tối đa (giờ) để hệ thống giữ granularity theo phút. |
| `analytics.chart.hour_granularity_max_range_days` | Khoảng lọc tối đa (ngày) để hệ thống giữ granularity theo giờ. |
| `analytics.chart.day_granularity_max_range_days` | Khoảng lọc tối đa (ngày) để hệ thống giữ granularity theo ngày. |
| `analytics.chart.week_granularity_max_range_days` | Khoảng lọc tối đa (ngày) để hệ thống giữ granularity theo tuần. |
| `analytics.chart.minute_axis_label_interval_minutes` | Khoảng phút giữa các mốc nhãn trục X ở chế độ minute. |
| `analytics.chart.minute_axis_max_ticks` | Số tick tối đa của biểu đồ minute. |
| `analytics.chart.hour_axis_max_ticks` | Số tick tối đa của biểu đồ hour. |
| `analytics.chart.overview_axis_max_ticks` | Số tick tối đa của biểu đồ tổng quan day/week/month. |
| `analytics.chart.point_markers_max_points` | Số điểm tối đa trước khi ẩn marker để giảm rối và tải render. |

### `logging`

| Key | Giải thích |
|---|---|
| `logging.level` | Mức log mong muốn cho môi trường triển khai. |
| `logging.verbose_violation_trace` | Cờ dự phòng cho log trace vi phạm chi tiết. |
Ghi chú: 2 key trên hiện là key cấu hình dự phòng, chưa được backend tiêu thụ trực tiếp ở runtime hiện tại.

## API REST (có giải thích theo endpoint)

| Method | Endpoint | Giải thích |
|---|---|---|
| `GET` | `/api/health` | Kiểm tra backend đang sống, trả `{"status":"ok"}`. |
| `GET` | `/api/cameras` | Lấy danh sách camera để hiển thị ở UI. |
| `GET` | `/api/cameras/{camera_id}` | Lấy chi tiết camera + lane config + trạng thái áp dụng runtime + validation. |
| `POST` | `/api/cameras` | Tạo camera mới và lane config tương ứng. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera/lane config; backend reload context camera. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera, lane config, ảnh nền, ảnh bằng chứng liên quan. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lấy lane config dạng pixel để frontend overlay trực tiếp lên canvas. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Lấy trajectory gần đây cho overlay cấu hình (`limit`, `lane_id`, `vehicle_type`). |
| `GET` | `/api/cameras/{camera_id}/preview` | Luồng MJPEG preview camera để xem trực tiếp trên frontend. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền (jpg/png) cho màn hình cấu hình. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền hiện tại của camera. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền hiện tại. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Trả file ảnh bằng chứng vi phạm. |
| `GET` | `/api/violations/history` | Truy vấn lịch sử vi phạm (`camera_id`, `from_ts`, `to_ts`, `limit`). |
| `GET` | `/api/violations/export` | Export lịch sử vi phạm `csv/xlsx` (`format`, `camera_id`, `from_ts`, `to_ts`). |
| `GET` | `/api/analytics/dashboard` | Dữ liệu dashboard tổng hợp + `chart_config` cho frontend. |
| `GET` | `/api/stats` | Thống kê tổng hợp theo khoảng thời gian (`from_ts`, `to_ts`). |

## WebSocket

| Endpoint | Giải thích |
|---|---|
| `WS /ws/tracks?camera_id=...` | Stream track realtime theo camera (hoặc toàn bộ nếu không lọc). |
| `WS /ws/violations?camera_id=...` | Stream vi phạm realtime theo camera (hoặc toàn bộ nếu không lọc). |

## Kiểm thử

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

## Tài liệu theo module

- [backend/README.md](backend/README.md)
- [frontend/README.md](frontend/README.md)
