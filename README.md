# Traffic Warning

Hệ thống giám sát giao thông thời gian thực, phát hiện phương tiện bằng YOLOv8, theo dõi bằng ByteTrack, gán xe vào làn bằng polygon cấu hình thủ công, phát hiện vi phạm và hiển thị kết quả trên giao diện web.

## Mục Lục

- [Tổng quan](#tổng-quan)
- [Luồng xử lý](#luồng-xử-lý)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Khởi chạy nhanh](#khởi-chạy-nhanh)
- [Model AI `.pt`](#model-ai-pt)
- [Cấu hình hệ thống](#cấu-hình-hệ-thống)
- [Các loại vi phạm](#các-loại-vi-phạm)
- [API và WebSocket](#api-và-websocket)
- [Kiểm thử](#kiểm-thử)
- [Tài liệu theo module](#tài-liệu-theo-module)

## Tổng Quan

Hệ thống gồm 3 phần chính:

| Thành phần | Vai trò |
|---|---|
| `backend` | FastAPI, YOLOv8, ByteTrack, logic gán làn, phát hiện vi phạm, OCR biển số, lưu SQLite, REST API và WebSocket. |
| `frontend` | React + Vite cho giám sát realtime, xem lịch sử/thống kê, cấu hình camera/làn/vùng hình học. |
| `config` | Cấu hình camera, cấu hình làn, settings runtime, ảnh nền, ảnh bằng chứng và database SQLite. |

Các khả năng chính:

- Nhận diện và tracking các class YOLO được bật trong `detection.allowed_classes`, mặc định: `motorcycle`, `car`, `truck`, `bus`.
- Gán xe vào làn bằng polygon thủ công theo từng camera.
- Phát hiện sai làn, sai loại phương tiện, đi ngược chiều, rẽ trái/rẽ phải/đi thẳng/quay đầu không đúng quy định.
- Nhận diện biển số bằng detector biển số + OCR, có trạng thái minh bạch: `pending`, `confirmed`, `uncertain`, `unreadable`.
- Hiển thị realtime qua MJPEG preview và WebSocket.
- Lưu lịch sử vi phạm, ảnh bằng chứng, ảnh biển số, thống kê dashboard, export CSV/XLSX.
- Quản lý camera/lane/maneuver trực tiếp trên UI, có ảnh nền hỗ trợ căn chỉnh polygon.

## Luồng Xử Lý

```text
Nguồn video
  -> YOLOv8 phát hiện phương tiện
  -> ByteTrack theo dõi object qua các frame
  -> ổn định ID và loại xe
  -> gán lane bằng polygon
  -> kiểm tra hướng đi, chuyển làn, loại xe, maneuver
  -> đọc biển số nếu bật OCR
  -> lưu DB, lưu ảnh bằng chứng, đẩy realtime lên frontend
```

Một số điểm cần hiểu:

- `vehicle_id` là ID tracking trong phiên chạy, dùng để theo dõi bbox/quỹ đạo realtime.
- `track_session_id` giúp phân biệt các phiên chạy khác nhau, tránh nhầm `vehicle_id` sau khi restart backend.
- Biển số là thông tin bổ sung. Nếu OCR chưa chắc chắn hoặc thất bại, hệ thống vẫn phát hiện và lưu vi phạm.
- Tọa độ cấu hình hình học được lưu theo dạng chuẩn hóa `[0, 1]`, nên không phụ thuộc kích thước canvas frontend.

## Cấu Trúc Thư Mục

| Đường dẫn | Nội dung |
|---|---|
| `backend/app/api` | REST API và WebSocket router. |
| `backend/app/core` | Load cấu hình, export dữ liệu, ảnh nền, ảnh bằng chứng, timezone. |
| `backend/app/logic` | Logic gán làn, phát hiện hướng, sai làn, maneuver, OCR temporal voting. |
| `backend/app/managers` | Quản lý camera runtime, vòng đời camera context, phát sự kiện realtime. |
| `backend/app/tracking` | Wrapper ByteTrack/Ultralytics tracking. |
| `backend/app/vision` | YOLO detector phương tiện, detector biển số, OCR biển số. |
| `backend/tests` | Test backend. |
| `frontend/src/views` | 3 màn hình chính: Monitoring, Analytics, Management. |
| `frontend/src/components` | Canvas, icon, biểu đồ và component UI. |
| `config/cameras.json` | Danh sách camera. |
| `config/lane_configs` | Cấu hình lane theo từng camera. |
| `config/settings.json` | Tham số runtime toàn hệ thống. |

## Khởi Chạy Nhanh

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend mặc định chạy tại `http://localhost:8000`.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Frontend mặc định chạy tại `http://localhost:5173`.

Nếu backend không chạy ở `http://localhost:8000`, tạo file `frontend/.env`:

```env
VITE_API_BASE=http://localhost:8000
```

## Model AI `.pt`

### Model phát hiện phương tiện YOLOv8

Nguồn chính thức: Ultralytics Assets trên GitHub Releases.

| Model | Link tải | Gợi ý sử dụng |
|---|---|---|
| `yolov8n.pt` | [Tải yolov8n.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) | Nhẹ nhất, FPS cao, phù hợp máy yếu hoặc nhiều camera. |
| `yolov8s.pt` | [Tải yolov8s.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt) | Cân bằng tốc độ và độ chính xác. |
| `yolov8m.pt` | [Tải yolov8m.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt) | Độ chính xác tốt hơn, cần máy khỏe hơn. |
| `yolov8l.pt` | [Tải yolov8l.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8l.pt) | Nặng hơn `m`, phù hợp khi ưu tiên chất lượng. |
| `yolov8x.pt` | [Tải yolov8x.pt](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt) | Nặng nhất, nên dùng GPU mạnh. |

Ví dụ tải `yolov8m.pt`:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

Sau đó trỏ `config/settings.json` tới model:

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

Ý nghĩa nhanh:

- `weights_path`: đường dẫn file YOLO `.pt`.
- `device`: `auto` để backend tự chọn GPU nếu có; cũng có thể đặt `cpu`, `cuda`, `cuda:0`.
- `confidence_threshold`: ngưỡng tin cậy tối thiểu. Tăng lên sẽ giảm nhận nhầm nhưng dễ bỏ sót vật thể nhỏ/mờ.
- `iou_threshold`: ngưỡng NMS để gộp bbox trùng nhau.
- `allowed_classes`: class YOLO được nhận diện/tracking. Xóa class khỏi danh sách để tắt nhận diện class đó.

Backend có cơ chế fallback: nếu model `.pt` được cấu hình không load được, detector sẽ thử các model cùng thư mục theo thứ tự `yolov8x.pt`, `yolov8l.pt`, `yolov8m.pt`, `yolov8s.pt`, `yolov8n.pt` nếu các file đó tồn tại.

### Model biển số

Pipeline biển số dùng:

- Detector biển số tại `license_plate.detector_weights_path`, mặc định: `backend/license_plate_yolov8.pt`.
- OCR backend theo `license_plate.ocr_backend`, mặc định hiện tại trong settings là `paddleocr`.
- PaddleOCR model names được cấu hình qua `paddle_text_detection_model_name` và `paddle_text_recognition_model_name`.

File `backend/license_plate_yolov8.pt` không được commit vào Git vì là file model lớn. Cần tải model detector biển số riêng trước khi bật `license_plate.enabled`.

| Model | Link tải | Khuyến nghị sử dụng |
|---|---|---|
| `Koushim/yolov8-license-plate-detection/best.pt` | [Tải best.pt](https://huggingface.co/Koushim/yolov8-license-plate-detection/resolve/main/best.pt?download=true) | **Khuyến nghị cho hệ thống hiện tại.** YOLOv8n, 1 class `license_plate`, nhẹ khoảng 6.25 MB, license MIT. Phù hợp vì code hiện crop vùng xe rồi detect biển số, không cần lọc nhiều class. Nhược điểm: là model generic, vẫn nên kiểm thử lại với biển số Việt Nam, góc camera và điều kiện ánh sáng thực tế. |
| `Murd0ck/LicensePlateDetector_YOLOv8n/best.pt` | [Tải best.pt](https://huggingface.co/Murd0ck/LicensePlateDetector_YOLOv8n/resolve/main/best.pt?download=true) | YOLOv8n một class, có công bố metric tốt trên tập validation và có cả bản ONNX. Nhược điểm: fine-tune chủ yếu cho biển số Ukraine/AUTO.RIA, license CC BY 4.0, cần kiểm thử domain Việt Nam trước khi dùng production. |
| `orionwambert/yolov8-license-plate-detection/best.pt` | [Tải best.pt](https://huggingface.co/orionwambert/yolov8-license-plate-detection/resolve/main/best.pt?download=true) | Có thể dùng để thử nghiệm vì nhận diện cả biển số và xe. Nhược điểm quan trọng: model có nhiều class (`License Plates`, `Vehicles`), trong khi detector biển số hiện tại lấy bbox confidence cao nhất mà chưa lọc class; vì vậy có thể chọn nhầm bbox xe làm biển số nếu dùng trực tiếp. Chỉ nên dùng sau khi chỉnh code lọc class hoặc retrain còn 1 class biển số. |

Tải nhanh model khuyến nghị về đúng tên file mặc định:

```powershell
Invoke-WebRequest -Uri "https://huggingface.co/Koushim/yolov8-license-plate-detection/resolve/main/best.pt?download=true" -OutFile ".\backend\license_plate_yolov8.pt"
```

Sau khi tải, giữ cấu hình mặc định hoặc trỏ lại đúng đường dẫn:

```json
{
  "license_plate": {
    "enabled": true,
    "detector_weights_path": "backend/license_plate_yolov8.pt",
    "detector_confidence_threshold": 0.35,
    "ocr_backend": "paddleocr"
  }
}
```

### Cài PyTorch

PyTorch nên cài theo phần cứng đang chạy.

CPU:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

GPU NVIDIA, ví dụ CUDA 13.0:

```powershell
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
```

## Cấu Hình Hệ Thống

Cấu hình chính nằm trong 3 nhóm file:

| File | Mục đích |
|---|---|
| `config/cameras.json` | Khai báo camera và thông tin nguồn video. |
| `config/lane_configs/<camera_id>.json` | Khai báo polygon lane, hướng đi, maneuver và luật theo từng lane. |
| `config/settings.json` | Tham số runtime: model AI, tracking, gán làn, vi phạm, OCR, UI, export/thống kê. |

### `config/cameras.json`

| Trường | Giải thích |
|---|---|
| `camera_id` | ID duy nhất của camera. ID này cũng là tên file lane config tương ứng. |
| `rtsp_url` | Nguồn video. Hỗ trợ `rtsp://`, `rtsps://`, `http://`, `https://` hoặc file local. |
| `camera_type` | Loại camera: `roadside`, `overhead`, `intersection`. |
| `view_direction` | Mô tả hướng nhìn, dùng để đọc hiểu cấu hình. |
| `location.road_name` | Tên tuyến đường. |
| `location.intersection_name` | Tên nút giao, nếu có. |
| `location.gps_lat`, `location.gps_lng` | Tọa độ GPS, phục vụ báo cáo/thống kê nếu cần. |
| `monitored_lanes` | Danh sách lane ID camera giám sát. |
| `frame_width`, `frame_height` | Kích thước frame chuẩn khi backend xử lý và giải chuẩn hóa polygon. |

### `config/lane_configs/<camera_id>.json`

Toàn bộ điểm hình học là `[x, y]` chuẩn hóa từ `0.0` đến `1.0`.

| Trường | Giải thích |
|---|---|
| `camera_id` | Phải trùng với camera đang cấu hình. |
| `frame_width`, `frame_height` | Kích thước tham chiếu khi chuyển giữa tọa độ chuẩn hóa và pixel. |
| `lanes` | Danh sách lane của camera. |
| `lanes[].lane_id` | ID lane trong camera. |
| `lanes[].polygon` | Biên lane, dùng để xác định xe đang thuộc lane nào. |
| `lanes[].approach_zone` | Vùng xe đi vào trước khi thực hiện maneuver, giúp khóa lane nguồn. |
| `lanes[].commit_gate` | Vùng xác nhận xe bắt đầu commit một hướng đi. |
| `lanes[].commit_line` | Vạch xác nhận xe bắt đầu commit một hướng đi. |
| `lanes[].allowed_lane_changes` | Lane đích được phép chuyển sang. Nếu xe sang lane ngoài danh sách sẽ bị xét `wrong_lane`. |
| `lanes[].allowed_vehicle_types` | Loại phương tiện được phép trong lane: `motorcycle`, `car`, `truck`, `bus`. |
| `lanes[].allowed_maneuvers` | Danh sách hướng đi được phép theo lane. |
| `lanes[].direction_rule.enabled` | Bật/tắt kiểm tra đi ngược chiều cho lane. |
| `lanes[].direction_rule.direction_path` | Đường hướng đúng chiều, dạng polyline. |
| `lanes[].direction_rule.check_zone` | Vùng áp dụng kiểm tra hướng. |
| `lanes[].maneuvers` | Cấu hình cho `straight`, `left`, `right`, `u_turn`. |

Mỗi `lanes[].maneuvers.<maneuver>` gồm:

| Trường | Giải thích |
|---|---|
| `enabled` | Bật/tắt nhận diện maneuver này. |
| `allowed` | Cho phép hoặc cấm maneuver này theo luật. |
| `movement_path` | Quỹ đạo kỳ vọng của xe, dạng polyline. |
| `corridor_preset` | Preset độ rộng corridor: `narrow`, `normal`, `wide`. |
| `corridor_width_px` | Độ rộng corridor theo pixel. Nếu để trống, backend có thể suy theo preset. |
| `turn_corridor` | Vùng corridor để nhận biết xe đang thực hiện maneuver. Có thể tự sinh từ `movement_path`. |
| `exit_line` | Vạch xác nhận xe đi ra đúng nhánh của maneuver. |
| `exit_zone` | Vùng xác nhận xe đi ra đúng nhánh của maneuver. |

### `config/settings.json`

#### Nhóm cơ bản

| Key | Giải thích |
|---|---|
| `database.path` | Đường dẫn SQLite. Nếu là đường dẫn tương đối, backend tính từ repo root. |
| `camera.stream.rtsp_reconnect_delay_s` | Số giây chờ trước khi thử kết nối lại nguồn video sau lỗi. |

#### `detection`

| Key | Giải thích |
|---|---|
| `detection.weights_path` | File YOLO `.pt` dùng để phát hiện phương tiện. |
| `detection.device` | Thiết bị suy luận: `auto`, `cpu`, `cuda`, `cuda:0`, ... |
| `detection.confidence_threshold` | Ngưỡng tin cậy tối thiểu của detector. |
| `detection.iou_threshold` | Ngưỡng IoU cho NMS, giúp xử lý bbox trùng nhau. |
| `detection.allowed_classes` | Danh sách class YOLO được nhận diện/tracking. Đây là bật/tắt nhận diện thật ở detector. |

#### `tracking`

| Key | Giải thích |
|---|---|
| `tracking.tracker_config` | File cấu hình ByteTrack truyền cho Ultralytics. |
| `tracking.vehicle_type_history.window_ms` | Khoảng thời gian giữ các dự đoán loại xe gần đây để làm mượt nhãn. |
| `tracking.vehicle_type_history.size` | Số mẫu loại xe tối đa giữ cho mỗi `vehicle_id`. |
| `tracking.vehicle_type_history.recency_weight_bias` | Mức ưu tiên nhẹ cho mẫu mới hơn khi vote loại xe. |
| `tracking.stable_track.max_idle_ms` | Thời gian cho phép track tạm biến mất trước khi không nối lại nữa. |
| `tracking.stable_track.min_iou_for_rebind` | IoU tối thiểu để nối track mới với track ổn định cũ. |
| `tracking.stable_track.max_normalized_distance` | Khoảng cách tâm tối đa khi xét nối lại track. |

#### `lane_assignment`

| Key | Giải thích |
|---|---|
| `lane_assignment.temporal.observation_window_ms` | Cửa sổ quan sát lane raw trước khi chọn lane ổn định. |
| `lane_assignment.temporal.min_majority_hits` | Số lần lane phải xuất hiện trong cửa sổ để được chốt. |
| `lane_assignment.temporal.switch_min_duration_ms` | Thời gian tối thiểu trước khi chấp nhận đổi lane ổn định. |
| `lane_assignment.overlap_preference.preferred_lane_overlap_ratio` | Tỷ lệ overlap giúp giữ lane ổn định khi xe nằm sát ranh giới. |
| `lane_assignment.overlap_preference.preferred_lane_overlap_margin_px` | Biên pixel cho phép khi so sánh overlap giữa các lane. |

#### `wrong_lane` và `direction_detection`

| Key | Giải thích |
|---|---|
| `wrong_lane.min_duration_ms` | Xe phải vi phạm sai làn tối thiểu bao lâu trước khi phát sự kiện. |
| `direction_detection.defaults.same_direction_cos_threshold` | Ngưỡng cosine để xem chuyển động là đúng chiều. |
| `direction_detection.defaults.opposite_direction_cos_threshold` | Ngưỡng cosine để xem chuyển động là ngược chiều. |
| `direction_detection.defaults.min_duration_ms` | Thời gian tối thiểu cần quan sát trước khi kết luận hướng. |
| `direction_detection.defaults.min_displacement_px` | Quãng dịch chuyển tối thiểu để tránh kết luận từ xe đứng yên/rung nhẹ. |
| `direction_detection.defaults.min_samples` | Số mẫu trajectory tối thiểu để đánh giá hướng. |

#### `turn_detection`

| Key | Giải thích |
|---|---|
| `turn_detection.turn_region_min_hits` | Số lần xe cần xuất hiện trong corridor/zone khi chưa có bằng chứng đầu ra mạnh. |
| `turn_detection.turn_state_timeout_ms` | Thời gian không còn tín hiệu thì reset trạng thái nhận diện rẽ. |
| `turn_detection.trajectory_history_window_ms` | Khoảng thời gian giữ trajectory để tính hướng, độ cong và evidence. |
| `turn_detection.heading.straight_max_deg` | Góc lệch tối đa để hỗ trợ nhận diện đi thẳng. |
| `turn_detection.heading.turn_min_deg` | Góc đổi hướng tối thiểu để hỗ trợ nhận diện rẽ. |
| `turn_detection.heading.turn_max_deg` | Góc đổi hướng tối đa cho rẽ thường. |
| `turn_detection.heading.u_turn_min_change_deg` | Góc đổi hướng tối thiểu để hỗ trợ nhận diện quay đầu. |
| `turn_detection.heading.side_sign_tolerance` | Sai số nhỏ khi xác định phía trái/phải. |
| `turn_detection.heading.value_sign_tolerance` | Sai số nhỏ khi xác định dấu của chuyển động. |
| `turn_detection.heading.straight_curvature_max_for_heading_support` | Độ cong tối đa để heading còn được xem là hỗ trợ đi thẳng. |
| `turn_detection.curvature.u_turn_min` | Độ cong tối thiểu hỗ trợ quay đầu. |
| `turn_detection.curvature.straight_max` | Độ cong tối đa hỗ trợ đi thẳng. |
| `turn_detection.curvature.turn_min` | Độ cong tối thiểu hỗ trợ rẽ. |
| `turn_detection.curvature.fallback_min` | Ngưỡng dự phòng khi evidence chưa đủ mạnh. |
| `turn_detection.opposite_direction.cos_threshold` | Ngưỡng cosine để nhận biết chuyển động ngược hướng khi xét quay đầu. |
| `turn_detection.trajectory.sample_inside_polygon_min_hits` | Số mẫu nằm trong polygon tối thiểu để tính là có hit. |
| `turn_detection.trajectory.entry_heading_lookback_points` | Số điểm nhìn lại để ước lượng hướng vào maneuver. |
| `turn_detection.trajectory.heading_local_window_points` | Số điểm dùng cho heading cục bộ. |

#### `evidence_fusion`

| Key | Giải thích |
|---|---|
| `evidence_fusion.line_crossing.side_tolerance_px` | Sai số khi xác định xe ở phía nào của vạch. |
| `evidence_fusion.line_crossing.min_pre_frames` | Số frame ổn định tối thiểu trước khi qua vạch. |
| `evidence_fusion.line_crossing.min_post_frames` | Số frame ổn định tối thiểu sau khi qua vạch. |
| `evidence_fusion.line_crossing.min_displacement_px` | Quãng dịch chuyển tối thiểu để xác nhận crossing. |
| `evidence_fusion.line_crossing.min_displacement_ratio` | Quãng dịch chuyển tối thiểu theo tỷ lệ chiều dài vạch. |
| `evidence_fusion.line_crossing.max_gap_ms` | Gap tối đa giữa các mẫu crossing trước khi reset. |
| `evidence_fusion.line_crossing.cooldown_ms` | Thời gian nghỉ sau một crossing để tránh double-count. |
| `evidence_fusion.evidence_expire_ms` | Thời gian evidence còn hiệu lực trước khi bị giảm/xóa. |
| `evidence_fusion.motion_window_samples` | Số mẫu trajectory dùng để tính chuyển động ngắn hạn. |
| `evidence_fusion.turn_scoring.decay_per_frame` | Mức giảm điểm evidence qua mỗi frame. |
| `evidence_fusion.turn_scoring.score_cap` | Điểm tối đa của một evidence. |
| `evidence_fusion.turn_scoring.*_weight` | Trọng số cho corridor, exit zone, exit line, heading, curvature, opposite direction. |
| `evidence_fusion.turn_scoring.temporal_continuity_bonus` | Điểm cộng khi evidence liên tục theo thời gian. |
| `evidence_fusion.turn_scoring.no_signal_penalty` | Điểm trừ khi thiếu tín hiệu mới. |
| `evidence_fusion.turn_scoring.temporal_hits_min` | Số hit liên tục tối thiểu để tăng độ tin cậy. |
| `evidence_fusion.turn_scoring.strong_exit_min_temporal_hits` | Số hit thời gian tối thiểu cho evidence đầu ra mạnh. |
| `evidence_fusion.turn_scoring.strong_exit_min_corridor_hits` | Số hit corridor tối thiểu cho evidence đầu ra mạnh. |
| `evidence_fusion.turn_scoring.threshold_*` | Ngưỡng điểm để xác nhận từng nhóm maneuver. |

#### `event_lifecycle`, `geometry`, `performance`, `websocket`

| Key | Giải thích |
|---|---|
| `event_lifecycle.violation_rearm_window_ms` | Thời gian chờ trước khi cho phép phát lại cùng loại vi phạm trong lifecycle mới. |
| `event_lifecycle.state_prune_max_age_s` | Tuổi tối đa của state xe trước khi dọn bộ nhớ. |
| `geometry.evidence_crop.expand_x_ratio` | Tỷ lệ nới bbox sang ngang khi cắt ảnh bằng chứng. |
| `geometry.evidence_crop.expand_y_top_ratio` | Tỷ lệ nới bbox lên phía trên. |
| `geometry.evidence_crop.expand_y_bottom_ratio` | Tỷ lệ nới bbox xuống phía dưới. |
| `geometry.evidence_crop.min_size_px` | Kích thước crop tối thiểu; quá nhỏ thì fallback phù hợp hơn. |
| `geometry.evidence_image.jpeg_quality` | Chất lượng JPEG ảnh bằng chứng. |
| `performance.preview.max_fps` | FPS tối đa của MJPEG preview. |
| `performance.preview.jpeg_quality` | Chất lượng JPEG preview. |
| `performance.processing.fps_window_s` | Cửa sổ tính FPS xử lý. |
| `websocket.track_push_interval_ms` | Chu kỳ tối thiểu gửi track realtime. |
| `websocket.listener_queue_maxsize` | Kích thước queue listener để tránh backlog realtime. |

#### `ui.monitoring`

| Key | Giải thích |
|---|---|
| `ui.monitoring.trajectory.default_limit` | Số trajectory mặc định hiển thị trên màn hình giám sát. |
| `ui.monitoring.trajectory.min_limit`, `max_limit` | Giới hạn cho bộ chọn số trajectory. |
| `ui.monitoring.trajectory.max_points_per_vehicle` | Số điểm tối đa giữ cho mỗi trajectory. |
| `ui.monitoring.trajectory.stale_ms` | Sau thời gian này không cập nhật thì trajectory bị coi là cũ. |
| `ui.monitoring.trajectory.min_point_distance_px` | Khoảng cách tối thiểu giữa 2 điểm để tránh vẽ nhiễu. |
| `ui.monitoring.violation.list_max_rows` | Số dòng vi phạm realtime tối đa giữ trên UI. |
| `ui.monitoring.violation.highlight_duration_ms` | Thời gian highlight sự kiện mới. |
| `ui.monitoring.processing_fps.stale_after_ms` | Sau thời gian này FPS xử lý bị coi là cũ. |
| `ui.monitoring.processing_fps.poll_interval_ms` | Chu kỳ UI poll/refresh trạng thái FPS. |

#### `license_plate`

| Key | Giải thích |
|---|---|
| `license_plate.enabled` | Bật/tắt pipeline biển số. |
| `license_plate.detector_weights_path` | File `.pt` dùng để detect vùng biển số trong crop xe. |
| `license_plate.detector_confidence_threshold` | Ngưỡng tin cậy tối thiểu của detector biển số. |
| `license_plate.ocr_backend` | OCR engine: `paddleocr` hoặc `easyocr`. |
| `license_plate.easyocr_lang` | Ngôn ngữ EasyOCR nếu chọn backend `easyocr`. |
| `license_plate.easyocr_use_gpu` | Bật GPU cho EasyOCR. |
| `license_plate.paddle_ocr_version` | Phiên bản PaddleOCR muốn dùng. |
| `license_plate.paddle_text_detection_model_name` | Tên model PaddleOCR phát hiện text. |
| `license_plate.paddle_text_recognition_model_name` | Tên model PaddleOCR nhận dạng text. |
| `license_plate.paddle_lang` | Ngôn ngữ PaddleOCR. |
| `license_plate.paddle_use_gpu` | Bật GPU cho PaddleOCR. |
| `license_plate.read_interval_ms` | Chu kỳ đọc biển số theo mỗi xe, không OCR mọi frame. |
| `license_plate.min_ocr_confidence` | Ngưỡng confidence tối thiểu để giữ kết quả OCR. |
| `license_plate.consensus_min_hits` | Số lần OCR trùng nhau tối thiểu để xác nhận biển số. |
| `license_plate.candidate_window_ms` | Cửa sổ thời gian dùng để vote biển số. |
| `license_plate.max_attempts_before_unreadable` | Số lần thử trước khi đánh dấu biển số là `unreadable`. |
| `license_plate.crop_expand_x_ratio` | Tỷ lệ nới ngang crop xe trước khi tìm biển số. |
| `license_plate.crop_expand_y_ratio` | Tỷ lệ nới dọc crop xe trước khi tìm biển số. |
| `license_plate.image_jpeg_quality` | Chất lượng JPEG ảnh crop biển số. |

#### `analytics` và `logging`

| Key | Giải thích |
|---|---|
| `analytics.chart.minute_granularity_max_range_hours` | Khoảng lọc tối đa để biểu đồ giữ mức chi tiết theo phút. |
| `analytics.chart.hour_granularity_max_range_days` | Khoảng lọc tối đa để biểu đồ giữ mức chi tiết theo giờ. |
| `analytics.chart.day_granularity_max_range_days` | Khoảng lọc tối đa để biểu đồ giữ mức chi tiết theo ngày. |
| `analytics.chart.week_granularity_max_range_days` | Khoảng lọc tối đa để biểu đồ giữ mức chi tiết theo tuần. |
| `analytics.chart.minute_axis_label_interval_minutes` | Khoảng cách nhãn trục X khi xem theo phút. |
| `analytics.chart.minute_axis_max_ticks` | Số tick tối đa của chart theo phút. |
| `analytics.chart.hour_axis_max_ticks` | Số tick tối đa của chart theo giờ. |
| `analytics.chart.overview_axis_max_ticks` | Số tick tối đa của chart tổng quan. |
| `analytics.chart.point_markers_max_points` | Nhiều hơn số điểm này thì UI giảm marker để chart dễ nhìn. |
| `logging.level` | Mức log mong muốn. Hiện là key dự phòng. |
| `logging.verbose_violation_trace` | Bật log trace vi phạm chi tiết. Hiện là key dự phòng. |

## Các Loại Vi Phạm

| Mã | Ý nghĩa |
|---|---|
| `wrong_lane` | Xe đi sang lane không được phép. |
| `wrong_direction` | Xe đi ngược chiều trong vùng kiểm tra hướng. |
| `vehicle_type_not_allowed` | Loại phương tiện không được phép trong lane. |
| `turn_left_not_allowed` | Rẽ trái không đúng quy định. |
| `turn_right_not_allowed` | Rẽ phải không đúng quy định. |
| `turn_straight_not_allowed` | Đi thẳng không đúng quy định. |
| `turn_u_turn_not_allowed` | Quay đầu không đúng quy định. |

## API Và WebSocket

### REST API

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/api/health` | Kiểm tra backend đang chạy. |
| `GET` | `/api/cameras` | Lấy danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Lấy chi tiết camera, lane config, validation, runtime status và UI config. |
| `POST` | `/api/cameras` | Tạo camera mới kèm lane config. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera và lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera và dữ liệu liên quan. |
| `GET` | `/api/cameras/{camera_id}/lanes` | Lấy lane config dạng pixel để overlay. |
| `GET` | `/api/cameras/{camera_id}/trajectories` | Lấy trajectory gần đây, có `limit`, `lane_id`, `vehicle_type`. |
| `GET` | `/api/cameras/{camera_id}/preview` | Luồng MJPEG preview. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền `.jpg`/`.png` cho màn hình cấu hình. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền hiện tại. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh bằng chứng vi phạm hoặc ảnh crop biển số. |
| `GET` | `/api/violations/history` | Lấy lịch sử vi phạm, lọc theo `camera_id`, `license_plate`, `from_ts`, `to_ts`, `limit`. |
| `GET` | `/api/violations/export` | Export CSV/XLSX, lọc theo thời gian/camera/biển số. |
| `GET` | `/api/analytics/dashboard` | Lấy dashboard, time series và chart config. |
| `GET` | `/api/stats` | Thống kê tổng hợp theo `from_ts`, `to_ts`. |

### WebSocket

| Endpoint | Chức năng |
|---|---|
| `WS /ws/tracks?camera_id=...` | Stream track realtime để vẽ bbox, lane, biển số, trạng thái hướng. |
| `WS /ws/violations?camera_id=...` | Stream sự kiện vi phạm realtime. |

## Kiểm Thử

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

## Tài Liệu Theo Module

- [Backend README](backend/README.md)
- [Frontend README](frontend/README.md)
