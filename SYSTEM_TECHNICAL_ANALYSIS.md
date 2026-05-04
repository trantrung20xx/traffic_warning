# Tài liệu phân tích kỹ thuật hệ thống Traffic Warning

Ngày rà soát: 2026-05-01

Phạm vi: tài liệu này dựa trên source code hiện tại trong `backend`, `frontend`, `config` và file SQLite `config/traffic_warning.sqlite`. Không suy luận ngoài code. Những điểm chưa thấy trong source được ghi rõ là "chưa thấy trong source".

## 1. Tổng Quan

### Mục tiêu hệ thống

Hệ thống giám sát camera/video giao thông thời gian thực, phát hiện phương tiện bằng YOLOv8, theo dõi bằng ByteTrack, gán xe vào làn thủ công bằng polygon, phân tích quỹ đạo/hình học để phát hiện vi phạm và gửi cảnh báo về giao diện web.

Mục tiêu này thể hiện trực tiếp trong:

- `backend/app/managers/camera_context.py`: class `CameraContext`, docstring mô tả pipeline `RTSP -> YOLOv8 -> ByteTrack -> gán làn -> luật vi phạm -> DB/WebSocket`.
- `README.md` và `backend/README.md`: mô tả hệ thống FastAPI + YOLOv8 + ByteTrack + React.

### Chức năng chính

- Quản lý camera và metadata vị trí: `config/cameras.json`, schema `backend/app/schemas/camera.py`.
- Quản lý cấu hình làn, vùng tiếp cận, vùng/vạch commit, hướng di chuyển, movement path, exit line/zone: `config/lane_configs/*.json`, model `backend/app/core/config.py`.
- Đọc nguồn video RTSP/HTTP/file local: `RtspFrameReader` trong `backend/app/rtsp/rtsp_stream.py`.
- Nhận diện và tracking phương tiện: `YoloV8VehicleDetector` và `YoloByteTrackVehicleTracker`.
- Ổn định track ID, loại xe và làn: `StableTrackIdAssigner`, `TemporalVehicleTypeAssigner`, `TemporalLaneAssigner`.
- Phát hiện vi phạm: `ViolationLogic` trong `backend/app/logic/violation_logic.py`.
- Lưu ảnh bằng chứng và bản ghi vi phạm: `backend/app/core/evidence_images.py`, `backend/app/db/repository.py`.
- Gửi realtime qua WebSocket và preview MJPEG: `backend/app/api/ws.py`, `backend/app/api/routes.py`.
- Frontend giám sát, thống kê, quản lý cấu hình: `frontend/src/views/MonitoringView.jsx`, `AnalyticsView.jsx`, `ManagementView.jsx`.

### Các module lớn

| Module | File/thư mục | Vai trò |
|---|---|---|
| Backend server | `backend/app/server.py` | Tạo FastAPI app, CORS, khởi tạo `CameraManager`, đăng ký REST/WS router, start/stop manager. |
| Camera orchestration | `backend/app/managers/camera_manager.py` | Load config, quản lý nhiều `CameraContext`, reload runtime sau khi lưu config, quản lý listener realtime. |
| Per-camera pipeline | `backend/app/managers/camera_context.py` | Vòng lặp đọc frame, tracking, lane assignment, violation logic, DB, evidence, WebSocket. |
| Video input | `backend/app/rtsp/rtsp_stream.py` | Đọc frame bằng OpenCV trong thread nền; hỗ trợ RTSP/HTTP/file local. |
| Detection/tracking | `backend/app/vision/detector.py`, `backend/app/tracking/tracker.py` | Load YOLOv8, lọc class phương tiện, gọi Ultralytics `model.track` với ByteTrack. |
| Logic hình học/làn | `backend/app/logic/polygon.py`, `lane_logic.py`, `geometry_validator.py` | Prepared polygon/line, gán làn, kiểm tra config geometry. |
| Violation engine | `backend/app/logic/violation_logic.py` | Sai làn, loại xe không hợp lệ, rẽ/đi thẳng/quay đầu sai quy định, evidence fusion, dedup lifecycle. |
| DB/repository | `backend/app/db/*` | SQLAlchemy + SQLite, model `Violation`, truy vấn lịch sử/dashboard. |
| API/WS | `backend/app/api/routes.py`, `ws.py` | REST endpoints, MJPEG, WebSocket tracks/violations. |
| Frontend API | `frontend/src/api.js` | Wrapper REST, download export, WebSocket, URL preview/evidence. |
| Frontend views | `frontend/src/views/*.jsx` | Màn hình giám sát, thống kê, quản lý camera/làn. |
| Canvas/editor | `frontend/src/components/CameraCanvas.jsx`, `components/canvas/*` | Vẽ lane/maneuver/vehicle/trajectory và chỉnh sửa hình học. |

## 2. Kiến Trúc

### Kiến trúc tổng thể

```text
config JSON + SQLite
        |
        v
FastAPI app (`server.py`)
        |
        v
CameraManager
        |
        +-- CameraContext per camera
              |
              +-- RtspFrameReader -> frame BGR
              +-- YOLOv8/ByteTrack -> raw tracks
              +-- StableTrackIdAssigner -> stable vehicle_id
              +-- TemporalVehicleTypeAssigner -> stable vehicle_type
              +-- LaneLogic + TemporalLaneAssigner -> raw/stable lane_id
              +-- ViolationLogic -> violation candidates
              +-- save evidence image + insert SQLite
              +-- push TrackMessage / ViolationEvent to queues
        |
        +-- REST API / WebSocket / MJPEG
        |
        v
React frontend
```

### Backend lifecycle

- `backend/app/server.py:create_app()`:
  - Tạo `FastAPI(title="Traffic Warning Backend", version="0.1.0")`.
  - Bật CORS rộng.
  - Tạo `CameraManager(repo_root)`.
  - Include REST router từ `create_api_router(manager)`.
  - Include WebSocket router từ `create_ws_router(manager)`.
  - Startup gọi `manager.start()`.
  - Shutdown gọi `manager.stop()`.

- `CameraManager.__init__()`:
  - Load `AppConfig` từ `config/settings.json` bằng `load_app_config`.
  - Validate camera/lane consistency bằng `validate_no_shared_lanes_across_cameras`.
  - Load cameras từ `config/cameras.json`.
  - Tạo SQLAlchemy engine/session bằng `create_engine_and_session`.

- `CameraManager.start()`:
  - Với mỗi camera, gọi `_start_context(camera_id)`.
  - `_build_context(camera_id)` load lane config, denormalize sang pixel, truyền toàn bộ settings vào `CameraContext`.

### Frontend lifecycle

- `frontend/src/main.jsx`: render React app.
- `frontend/src/App.jsx`:
  - Load danh sách camera bằng `fetchCameras()`.
  - Chọn camera mặc định.
  - Điều hướng 3 màn hình: `MonitoringView`, `AnalyticsView`, `ManagementView`.

## 3. Công Nghệ

### Backend

Các dependency được khai báo trong `backend/requirements.txt`:

| Công nghệ | Vai trò trong source |
|---|---|
| FastAPI | REST API, WebSocket endpoint, upload file. |
| Starlette | WebSocket state, ASGI layer; code import `WebSocketState` trong `backend/app/api/ws.py`. |
| Uvicorn | ASGI server để chạy backend. |
| OpenCV (`opencv-python`) | Đọc video bằng `cv2.VideoCapture`, resize frame, mã hóa JPEG preview/evidence. |
| NumPy | Kiểu dữ liệu frame, tracker input/output. |
| Shapely | Polygon/LineString, contains, intersection, validation geometry. |
| Pydantic | Schema camera/event/config. |
| SQLAlchemy | ORM SQLite. |
| Ultralytics | YOLOv8 và API `model.track` dùng ByteTrack. |
| `lap>=0.5.12` | Hỗ trợ matching cho ByteTrack/Ultralytics. |
| `python-multipart` | Upload ảnh nền. |
| `openpyxl` | Export lịch sử vi phạm XLSX. |
| Pytest | Test backend. |

### Frontend

Các dependency trong `frontend/package.json`:

| Công nghệ | Vai trò |
|---|---|
| React 18 | UI SPA. |
| React DOM | Render app. |
| Vite | Dev server/build. |
| lucide-react | Icon UI thông qua `AppIcon.jsx`. |

### Database/lưu trữ

- SQLite file: `config/traffic_warning.sqlite`.
- SQLAlchemy model duy nhất hiện thấy: `Violation` trong `backend/app/db/models.py`.
- Camera và lane config không lưu trong DB; lưu bằng JSON:
  - `config/cameras.json`
  - `config/lane_configs/<camera_id>.json`
- Ảnh nền: `config/background_images`.
- Ảnh bằng chứng: `config/evidence_images/<camera_id>/<dd-mm-yyyy>/...jpg`.

### YOLO / tracking

- YOLO detector class: `YoloV8VehicleDetector` trong `backend/app/vision/detector.py`.
- Tracking class: `YoloByteTrackVehicleTracker` trong `backend/app/tracking/tracker.py`.
- Source hiện không có method detect riêng; detection và tracking chạy chung qua `self.detector.model.track(...)`.
- ByteTrack config lấy từ `settings.json` field `tracking.tracker_config`, hiện là `bytetrack.yaml`.

### Geometry / trajectory / evidence logic

- Geometry low-level: `PreparedPolygon`, `PreparedLine`, `bbox_bottom_contact_points`, `signed_distance_to_line` trong `backend/app/logic/polygon.py`.
- Lane assignment: `LaneLogic` và `TemporalLaneAssigner` trong `backend/app/logic/lane_logic.py`.
- Violation/trajectory/evidence fusion: `ViolationLogic` trong `backend/app/logic/violation_logic.py`.
- Config geometry validator: `validate_lane_geometry` trong `backend/app/logic/geometry_validator.py`.

## 4. Luồng Dữ Liệu

Pipeline hiện tại:

```text
camera/video
  -> frame capture
  -> YOLOv8 + ByteTrack
  -> stable track id
  -> vehicle type smoothing
  -> raw lane assignment
  -> temporal stable lane
  -> trajectory sample + line crossing
  -> wrong_lane / vehicle_type / maneuver evidence fusion
  -> violation candidate
  -> crop + save evidence image
  -> ViolationEvent
  -> SQLite + WebSocket
  -> frontend monitoring/analytics
```

Chi tiết theo file/hàm:

| Bước | File/hàm/class | Input | Xử lý | Output |
|---|---|---|---|---|
| Load config | `CameraManager.__init__`, `load_app_config`, `load_cameras`, `load_lane_config_for_camera` | `settings.json`, `cameras.json`, lane JSON | Parse Pydantic, validate camera/lane | `AppConfig`, `CameraConfig`, `CameraLaneConfig` |
| Denormalize lane | `denormalize_lane_config` | Lane points `[0,1]` | Nhân `frame_width`, `frame_height` | Runtime pixel geometry |
| Capture frame | `RtspFrameReader.read` | RTSP/HTTP/local video | Thread nền dùng OpenCV, resize frame, trả frame mới | `Frame(bgr, timestamp_utc_ms)` |
| Preview | `CameraContext._maybe_update_preview` | frame BGR | JPEG encode theo `preview_max_fps` | `_latest_preview_jpeg` cho MJPEG |
| Detection/tracking | `YoloByteTrackVehicleTracker.track` | frame BGR | `YOLO.model.track(... persist=True, classes=vehicle_class_ids)` | list `Track` raw |
| Stable ID | `StableTrackIdAssigner.assign` | raw tracks | Giữ raw mapping, rebind bằng IoU/distance, cấp stable ID mới | list `Track` stable |
| Type smoothing | `TemporalVehicleTypeAssigner.resolve_type` | predicted type/confidence | Vote trọng số confidence + recency trong cửa sổ | stable vehicle type |
| Lane raw | `LaneLogic.assign_lane_id_from_bbox_xyxy` | bbox, preferred lane | Dùng đáy bbox, segment overlap, contains | raw lane id |
| Lane stable | `TemporalLaneAssigner.resolve_lane` | raw lane, ts | Majority window + hysteresis | stable lane id |
| Violation update | `ViolationLogic.update_and_maybe_generate_violation` | stable vehicle, lane, bbox, ts | trajectory, line crossing, wrong lane, turn state | candidates `[{lane_id, violation, evidence_summary?}]` |
| Evidence image | `CameraContext._create_violation_evidence`, `save_evidence_image` | frame, bbox, violation | Crop quanh bbox, encode JPEG, lưu file | relative image path |
| Event | `ViolationEvent.from_parts` | candidate + metadata | Tạo payload chuẩn schema | `ViolationEvent` |
| DB | `insert_violation` | `ViolationEvent` | Insert SQLAlchemy row | DB id gán vào event |
| Realtime track | `CameraManager._on_track`, `ws_tracks` | `TrackMessage` | Queue listener, filter camera_id | JSON WebSocket type `track` |
| Realtime violation | `CameraManager._on_violation`, `ws_violations` | `ViolationEvent` | Queue listener, filter camera_id | JSON `{type:"violation", event:{...}}` |
| Frontend render | `MonitoringView`, `CameraCanvas` | MJPEG, WS messages, lane config | Vẽ preview, lane, bbox, trajectory, danh sách vi phạm | UI realtime |

## 5. Backend

### Entry point

- `backend/app/server.py`
  - `create_app()`: tạo app, manager, REST/WS router.
  - `_install_event_loop_exception_guard()`: xử lý case Windows `WinError 10054` khi client ngắt WebSocket.

### CameraManager

File: `backend/app/managers/camera_manager.py`

Chức năng chính:

- `list_cameras()`: trả metadata camera cho frontend.
- `get_camera_detail(camera_id)`: trả camera, lane config, `runtime_applied`, `has_background_image`, `config_validation`, `ui`.
- `upsert_camera(camera_config, lane_config)`: validate, lưu JSON, reload context runtime nếu backend đang chạy.
- `delete_camera(camera_id)`: dừng context, xóa camera JSON, lane config, ảnh nền và ảnh evidence của camera.
- `query_history`, `query_dashboard`: wrapper DB repository.
- `create_track_listener`, `create_violation_listener`: tạo queue realtime.
- `_build_context(camera_id)`: truyền toàn bộ config runtime vào `CameraContext`.

Lưu ý: `delete_camera` xóa cả ảnh bằng chứng qua `delete_evidence_images_for_camera`; chưa thấy trong source cơ chế archive trước khi xóa.

### CameraContext

File: `backend/app/managers/camera_context.py`

Quan trọng nhất là `run_forever(stop_event)`:

1. `rtsp_reader.read(only_new=True)` lấy frame mới.
2. `_maybe_update_preview(frame.bgr)` cập nhật JPEG preview.
3. `tracker.track(frame.bgr)` chạy YOLO/ByteTrack.
4. `stable_track_id_assigner.assign(...)` ổn định ID.
5. Với từng track:
   - `TemporalVehicleTypeAssigner.resolve_type(...)`.
   - `LaneLogic.assign_lane_id_from_bbox_xyxy(...)`.
   - `TemporalLaneAssigner.resolve_lane(...)`.
   - Tạo `TrackVehicle`.
   - Gọi `ViolationLogic.update_and_maybe_generate_violation(...)`.
6. Push `TrackMessage` theo `track_push_interval_ms`.
7. Nếu có violation candidate:
   - `_handle_violations(...)`
   - `_create_violation_evidence(...)`
   - `_save_event_to_db(...)`
   - `on_violation(event)`
8. Prune state: violation logic, stable track, lane assigner, vehicle type assigner.
9. `_mark_processed_frame()` tính processing FPS.

### Video input

File: `backend/app/rtsp/rtsp_stream.py`

- `_describe_video_source(source)`: phân loại network source (`rtsp/rtsps/http/https`) hoặc local file.
- `RtspFrameReader.__init__`: mở thread nền `_reader_thread`.
- `_reader_loop`: dùng `cv2.VideoCapture`, đọc frame liên tục, resize về `frame_width/frame_height`, lưu latest frame.
- `read(only_new=True)`: tránh xử lý trùng frame nếu sequence chưa đổi.
- Với local file, `_configure_file_playback_pacing` đọc FPS và `_pace_file_playback_if_needed` giữ nhịp theo FPS gốc.

### Evidence image

File: `backend/app/core/evidence_images.py`

- `build_evidence_filename`: tên file chứa `camera_id`, timestamp UTC ms, vehicle id, lane id, violation.
- `build_evidence_date_folder`: thư mục ngày theo múi giờ Việt Nam.
- `save_evidence_image`: lưu JPEG vào `cfg.evidence_images_dir`.
- `build_evidence_image_url`: tạo URL `/api/violations/evidence/...`.
- `resolve_evidence_image_path`: chặn path traversal bằng kiểm tra path nằm trong base dir.

File: `CameraContext._crop_violation_evidence`

- Crop bbox có nới biên theo:
  - `evidence_crop_expand_x_ratio`
  - `evidence_crop_expand_y_top_ratio`
  - `evidence_crop_expand_y_bottom_ratio`
  - fallback full frame nếu crop quá nhỏ.

## 6. Frontend

### Cấu trúc màn hình

File: `frontend/src/App.jsx`

- Tab `Giám sát`: `MonitoringView`.
- Tab `Thống kê`: `AnalyticsView`.
- Tab `Quản lý camera`: `ManagementView`.
- `refreshCameras()` gọi `fetchCameras()` và cập nhật `selectedCameraId`.

### Màn hình giám sát

File: `frontend/src/views/MonitoringView.jsx`

Nguồn dữ liệu:

- `fetchCameraDetail(selectedCameraId)` để lấy lane config, camera metadata, `ui.monitoring`.
- `getCameraPreviewUrl(selectedCameraId)` để hiển thị MJPEG bằng thẻ `img`.
- `connectTracks(selectedCameraId, ...)` để nhận `TrackMessage`.
- `connectViolations(selectedCameraId, ...)` để nhận violation realtime.

Render:

- `CameraCanvas` vẽ overlay lane, bbox phương tiện, trajectory và FPS.
- Danh sách "Xe đang được theo dõi" lấy từ `message.vehicles`.
- Danh sách vi phạm realtime lấy từ WebSocket violations.
- Vehicle được highlight vi phạm bằng `violatingVehicleIdsRef` trong khoảng `highlight_duration_ms`.

Trajectory overlay:

- Có overlay quỹ đạo realtime.
- Quỹ đạo hiện được frontend tự dựng từ bbox trong track WS bằng `getVehicleTrajectoryPoint(vehicle)` lấy điểm giữa cạnh đáy bbox.
- `updateLiveTrajectories` giữ points theo `max_points_per_vehicle`, xóa stale theo `stale_ms`, lọc theo `min_point_distance_px`.
- Backend có endpoint `GET /api/cameras/{camera_id}/trajectories`, nhưng trong `frontend/src/api.js` hiện chưa thấy wrapper gọi endpoint này và `MonitoringView` không dùng endpoint đó.

### Màn hình thống kê

File: `frontend/src/views/AnalyticsView.jsx`

- Gọi song song `fetchDashboard` và `fetchViolationHistory`.
- Dùng `connectViolations(cameraFilter || null, ...)` để refresh dashboard/history khi có event mới phù hợp filter.
- Export CSV/XLSX qua `exportViolationHistory`.
- Hiển thị:
  - `StatPill` tổng quan.
  - `SimpleBarChart` theo camera, loại xe, loại vi phạm, khu vực.
  - `TimeSeriesChart` theo granularity backend trả hoặc frontend tự xác định fallback.
  - Bảng lịch sử và modal chi tiết vi phạm.

### Màn hình quản lý camera

File: `frontend/src/views/ManagementView.jsx`

Chức năng:

- Danh sách camera.
- Tạo camera mới bằng `createCameraDraft`.
- Sửa metadata camera: RTSP/video, camera type, hướng quan sát, frame size, vị trí/GPS.
- Sửa lane:
  - thêm/xóa lane.
  - `allowed_lane_changes`.
  - `allowed_vehicle_types`.
  - chọn edit target: lane polygon, approach zone, commit line, movement path, exit line, exit zone.
  - chọn maneuver: left, straight, right, u_turn.
  - chỉnh `enabled`, `allowed`, `corridor_width_px`.
- Upload/xóa background image qua `uploadBackgroundImage`, `deleteBackgroundImage`.
- Undo/redo thao tác geometry bằng `undoStackRef`, `redoStackRef`.
- Validate local bằng `validatePolygonDraft`.
- Hiển thị backend semantic validation từ `config_validation`.
- Lưu bằng `buildPayload(draft)` rồi gọi `createCamera` hoặc `updateCamera`.

Điểm quan trọng:

- State frontend giữ tọa độ normalized `[0,1]`.
- `CameraCanvas` denormalize sang pixel khi vẽ.
- Khi save, `buildPayload` gửi normalized points xuống backend.
- Frontend không gửi `turn_corridor`; nó gửi `movement_path`, `corridor_width_px`, `exit_line`, `exit_zone`. Backend tự dựng `turn_corridor` từ `movement_path`.

### Canvas/editor

File: `frontend/src/components/CameraCanvas.jsx`

- `denormalizeLane` đổi config normalized sang pixel để vẽ.
- Vẽ:
  - lane polygon.
  - approach zone.
  - commit gate/commit line.
  - movement path.
  - corridor preview.
  - exit zone/exit line.
  - trajectory overlay.
  - bbox và label xe.
  - FPS.
- Tương tác editor:
  - click thêm điểm.
  - kéo vertex.
  - click cạnh để chèn điểm.
  - kéo cả polygon/line.
  - clamp điểm trong frame.
  - trả điểm về normalized bằng `normalizePoint`.

File `frontend/src/components/canvas/PolygonLayer.js`:

- `drawPolygon`, `drawPolyline`, `drawCorridorPreview`, hướng mũi tên movement path.

File `frontend/src/components/canvas/BackgroundImageLayer.js`:

- Load image nền bằng `Image()`.
- Vẽ background image full canvas.

## 7. Config

### `config/settings.json`

Các nhóm đang được backend load trong `load_app_config`:

| Nhóm | Field chính | Ảnh hưởng |
|---|---|---|
| `database` | `path` | SQLite path cho SQLAlchemy. |
| `camera.stream` | `rtsp_reconnect_delay_s` | Delay reconnect video source. |
| `detection` | `weights_path`, `device`, `confidence_threshold`, `iou_threshold` | YOLO weight/device/conf/NMS IoU. |
| `tracking` | `tracker_config`, `vehicle_type_history`, `stable_track` | ByteTrack config, smoothing loại xe, stable ID rebind. |
| `lane_assignment` | `temporal`, `overlap_preference` | Smoothing lane và giữ lane khi overlap. |
| `wrong_lane` | `min_duration_ms` | Thời gian tối thiểu trước khi emit sai làn. |
| `turn_detection` | `turn_region_min_hits`, `turn_state_timeout_ms`, `trajectory_history_window_ms`, `heading`, `curvature`, `opposite_direction`, `trajectory` | State machine và motion inference cho maneuver. |
| `evidence_fusion.line_crossing` | side/pre/post/displacement/gap/cooldown | Xác nhận commit line/exit line ổn định. |
| `evidence_fusion.turn_scoring` | weights/thresholds | Chấm điểm maneuver evidence. |
| `event_lifecycle` | `violation_rearm_window_ms`, `state_prune_max_age_s` | Dedup/re-arm và prune state. |
| `geometry` | crop/image JPEG | Ảnh bằng chứng. |
| `performance` | preview FPS/JPEG, processing FPS window | Preview MJPEG và FPS xử lý. |
| `websocket` | push interval, queue maxsize | Tải realtime. |
| `ui.monitoring` | trajectory, violation, processing_fps | Frontend monitoring config. |
| `analytics.chart` | granularity/tick config | Dashboard/time-series. |
| `logging` | `level`, `verbose_violation_trace` | Có trong config; chưa thấy backend tiêu thụ trực tiếp ở runtime hiện tại. |

### `config/cameras.json`

Hiện có 3 camera:

- `cam_01`
  - `rtsp_url`: file local `C:\Users\trant\Videos\Captures\video.mp4`.
  - `camera_type`: `roadside`.
  - `view_direction`: `Đông - Bắc`.
  - road/intersection/GPS có đủ.
  - monitored lanes: `[1,2,3]`.
  - frame: `1280x720`.
- `cam_02`
  - `rtsp_url`: `abc`.
  - road: `Ngã tư Sở`.
  - lane: `[1]`.
- `cam_03`
  - `rtsp_url`: `bcd`.
  - road: `Ngã tư Nhổn`.
  - lane: `[1]`.

Ghi chú: `abc` và `bcd` không phải URL RTSP/HTTP hợp lệ theo format thông thường; code vẫn truyền vào `cv2.VideoCapture` vì source chỉ phân loại network/local file, không validate URL camera ở schema.

### Lane config hiện tại

File: `config/lane_configs/cam_01.json`

- Lane 1:
  - Có polygon.
  - `allowed_lane_changes: [1]`.
  - Cho phép cả `motorcycle`, `truck`, `bus`, `car`.
  - `straight.enabled=true`, `straight.allowed=true`, có `exit_line`.
  - right/left/u_turn disabled.
- Lane 2:
  - Có polygon và `commit_line`.
  - `allowed_lane_changes: [2]`.
  - Chỉ cho `motorcycle`, `truck`, `bus`; không cho `car`.
  - `straight.enabled=true`, `straight.allowed=false`, có `movement_path`.
  - `right.enabled=true`, `right.allowed=true`.
  - left/u_turn disabled.
- Lane 3:
  - Có polygon.
  - `allowed_lane_changes: [3]`.
  - Cho phép cả 4 loại xe.
  - straight/right/left/u_turn đều enabled và allowed.
  - Chưa thấy movement_path/exit geometry trong file cho các maneuver lane 3.

File: `config/lane_configs/cam_02.json` và `cam_03.json`

- Mỗi camera có 1 lane polygon hình chữ nhật.
- `straight` enabled/allowed.
- `right`, `left`, `u_turn` enabled nhưng `allowed=false`.
- Chưa thấy movement_path/exit line/exit zone cho các maneuver trong file.

### Backend load và dùng config

- `load_app_config(repo_root)` đọc `settings.json` và map vào `AppConfig`.
- `load_cameras(repo_root)` đọc `cameras.json`.
- `load_lane_config_for_camera(repo_root, camera_id)` đọc lane config, gọi `_normalize_lane_config_payload`.
- `_normalize_maneuver_config_payload`:
  - normalize geometry về `[0,1]`.
  - nếu có `movement_path`, tự dựng `turn_corridor` bằng `_build_turn_corridor_from_movement_path`.
  - `allowed = enabled and raw.allowed`.
- `denormalize_lane_config` đổi normalized sang pixel để runtime dùng trong `CameraContext`.
- `save_lane_config_for_camera` ghi JSON compact bằng `_compact_lane_config_for_storage`; không lưu `turn_corridor` vào file, vì có thể dựng lại từ `movement_path`.

### Frontend lưu config xuống backend

- `ManagementView.saveCurrentCamera()`:
  - Validate local bằng `validatePolygonDraft`.
  - Build payload bằng `buildPayload`.
  - Nếu camera mới: `createCamera(payload)` -> `POST /api/cameras`.
  - Nếu camera cũ: `updateCamera(camera_id, payload)` -> `PUT /api/cameras/{camera_id}`.
  - Sau save, fetch lại detail để lấy config backend đã normalize và `config_validation`.
- `CameraManager.upsert_camera` lưu `cameras.json` và lane JSON, sau đó `_reload_context(camera_id)` nếu runtime đang chạy.

## 8. Thuật Toán

### YOLO detection

File/class: `backend/app/vision/detector.py:YoloV8VehicleDetector`

Input:

- `weights_path`, `conf_threshold`, `iou_threshold`, `device`.

Xử lý:

- `_load_model_with_fallback(weights_path)` thử load weight được chỉ định; nếu `.pt` lỗi thì thử sibling weights tồn tại theo thứ tự `yolov8x`, `yolov8l`, `yolov8m`, `yolov8s`, `yolov8n`.
- `_resolve_inference_device`:
  - `auto`: dùng CUDA nếu `torch.cuda.is_available()`, ngược lại CPU.
  - `cuda`/`cuda:*`: yêu cầu PyTorch và CUDA khả dụng.
- Lấy `self.model.names`.
- Chỉ giữ COCO classes trong `ALLOWED_CLASSES = {"motorcycle","car","truck","bus"}`.

Output:

- Detector object chứa YOLO model, class names, vehicle class IDs, device.

### ByteTrack / tracking

File/class: `backend/app/tracking/tracker.py:YoloByteTrackVehicleTracker`

Input:

- Frame BGR (`np.ndarray`).
- Detector đã load.

Xử lý:

- Gọi:

```python
self.detector.model.track(
    frame_bgr,
    device=self.detector.device,
    persist=True,
    conf=self.detector.conf_threshold,
    iou=self.detector.iou_threshold,
    classes=self.detector.vehicle_class_ids,
    tracker=self.tracker_config,
    verbose=False,
)
```

- Lấy `boxes.xyxy`, `boxes.conf`, `boxes.cls`, `boxes.id`.
- Bỏ class không thuộc `ALLOWED_CLASSES`.

Output:

- `list[Track]` gồm `vehicle_id`, `vehicle_type`, `bbox_xyxy`, `confidence`.

### Stable track ID

File/class: `backend/app/logic/track_id_logic.py:StableTrackIdAssigner`

Input:

- Raw tracks từ ByteTrack.
- Timestamp.

Xử lý:

1. Prune state cũ.
2. Lượt 1: nếu raw ID đã map sang stable ID và bbox còn hợp lý theo IoU/distance, giữ stable ID.
3. Lượt 2: nếu raw ID đổi, tìm stable track gần nhất còn mới bằng:
   - IoU (`_bbox_iou`)
   - normalized center distance (`_normalized_center_distance`)
   - type bonus
   - confidence bonus
4. Lượt 3: không ghép được thì cấp stable ID mới.

Output:

- `list[Track]` với `vehicle_id` ổn định hơn.

### Vehicle type smoothing

File/class: `backend/app/logic/vehicle_type_logic.py:TemporalVehicleTypeAssigner`

Input:

- `vehicle_id`, predicted type, confidence, timestamp.

Xử lý:

- Lưu deque quan sát gần đây theo `history_window_ms` và `history_size`.
- Tính score theo `confidence * recency_weight`.
- Mẫu mới hơn được ưu tiên nhẹ bằng `recency_weight_bias`.

Output:

- Loại xe ổn định hơn: `motorcycle`, `car`, `truck`, `bus`.

### Lane assignment

File/class: `backend/app/logic/lane_logic.py:LaneLogic`

Input:

- Bbox phương tiện.
- `preferred_lane_id` là lane stable hiện tại nếu có.

Xử lý:

- Lấy 3 điểm tiếp xúc đáy bbox bằng `bbox_bottom_contact_points`.
- Tạo đoạn đáy bbox từ điểm trái/phải.
- Với mỗi lane polygon:
  - tính `segment_overlap_length`.
  - kiểm tra center bottom có nằm trong polygon.
- Nếu có overlap:
  - ưu tiên lane stable nếu overlap đủ theo `preferred_lane_overlap_ratio` hoặc center nằm trong lane và chênh lệch overlap nhỏ hơn `preferred_lane_overlap_margin_px`.
  - nếu tie thì dùng preferred lane hoặc center-inside.
- Nếu không có overlap, fallback kiểm tra center bottom trong polygon.

Output:

- `raw_lane_id` hoặc `None`.

### Temporal lane assignment

File/class: `backend/app/logic/lane_logic.py:TemporalLaneAssigner`

Input:

- `vehicle_id`, `raw_lane_id`, timestamp.

Xử lý:

- Lưu quan sát lane trong `observation_window_ms`.
- Nếu chưa có stable lane, cần majority hits >= `min_majority_hits`.
- Nếu majority lane khác stable lane:
  - tạo pending lane.
  - chỉ switch khi pending tồn tại >= `switch_min_duration_ms` và đủ majority hits.

Output:

- Stable lane id hoặc `None`.

### Polygon / geometry check

File: `backend/app/logic/polygon.py`

- `PreparedPolygon.from_points`: tạo Shapely `Polygon` và `prepare(polygon)` để tăng tốc contains/intersection.
- `PreparedPolygon.contains_xy`: point-in-polygon.
- `PreparedPolygon.segment_overlap_length`: độ dài đoạn đáy bbox nằm trong lane polygon.
- `PreparedLine.intersects_segment`: kiểm tra segment trajectory cắt line.
- `signed_distance_to_line`: xác định phía của điểm so với line, dùng cho line crossing.

### Trajectory handling

File/class: `backend/app/logic/violation_logic.py:ViolationLogic`

Input:

- Bbox mỗi frame.

Xử lý:

- `_build_sample` tạo `TrajectorySample` gồm 3 contact points đáy bbox: left/center/right.
- `_append_trajectory_sample` giữ deque trong `trajectory_history_window_ms`.
- `_compute_motion_features` lấy các sample gần nhất theo `motion_window_samples`:
  - `path_length`: tổng độ dài đường qua các center points.
  - `displacement`: khoảng cách đầu-cuối.
  - `curvature = max(path_length / displacement - 1, 0)`.
  - `heading_vector`: vector cục bộ cuối trajectory.
  - `entry_vector`: vector lúc commit hoặc vector hướng lane.
  - `heading_change_deg`: góc giữa entry và heading.
  - `signed_heading_change_deg`: góc có dấu để phân biệt trái/phải.
  - `opposite_direction`: dot product <= `opposite_direction_cos_threshold`.

Output:

- `MotionFeatures`.

### Heading / curvature / opposite-direction

File: `ViolationLogic._heading_support_for_maneuver`, `_curvature_support_for_maneuver`, `_compute_motion_features`

- `straight`:
  - heading change <= `straight_max_deg`.
  - curvature <= `straight_curvature_max_for_heading_support`.
- `left/right`:
  - heading change trong `[turn_min_deg, turn_max_deg]`.
  - dùng dấu của `signed_heading_change_deg`.
  - expected sign lấy từ `lane_direction_vector` và anchor của maneuver trong `_expected_turn_side_sign`.
- `u_turn`:
  - heading change >= `u_turn_min_change_deg`.
  - `opposite_direction` phải đúng.
  - curvature >= `u_turn_min`.

### Wrong lane logic

File: `ViolationLogic._update_lane_state`, `_update_wrong_lane_candidate`

Input:

- Stable lane hiện tại của xe.
- Lane trước đó trong state.
- `allowed_lane_changes` của source lane.

Xử lý:

- Khi xe đổi từ `previous_lane_id` sang `lane_id`:
  - Tính thời gian ở source lane.
  - Kiểm tra source/target có cho phép loại xe hiện tại.
  - Nếu chuyển từ lane không cho phép loại xe sang lane cho phép loại xe thì coi là corrective transition, không báo wrong lane.
  - Nếu target lane không nằm trong `allowed_lane_changes`, tạo `IllegalLaneCandidate`.
- Candidate chỉ emit khi:
  - xe vẫn đang ở target lane.
  - source lane đủ stable theo `wrong_lane_min_source_stable_ms`.
  - không phải corrective transition.
  - target lane duration >= `wrong_lane_min_duration_ms`.
- Dedup bằng lifecycle key `wrong_lane:{source}->{target}`.

Output:

- Candidate `{lane_id: target_lane_id, violation: "wrong_lane", evidence_summary: ...}`.

### Vehicle type not allowed

File: `ViolationLogic.update_and_maybe_generate_violation`

Input:

- Stable lane id, stable vehicle type.
- `lane.allowed_vehicle_types`.

Xử lý:

- Nếu lane có danh sách allowed type và type hiện tại không nằm trong danh sách:
  - emit qua `_emit_violation_if_needed`.
  - `min_active_ms` mặc định lấy từ `vehicle_type_min_duration_ms` (900ms nếu không override).

Output:

- Candidate `vehicle_type_not_allowed`.

### Illegal turn / maneuver logic

File: `ViolationLogic._update_turn_state`, `_update_turn_confirmation`

Source hiện không tạo violation code tên `illegal_turn`. Code phát sinh theo mẫu:

- `turn_left_not_allowed`
- `turn_right_not_allowed`
- `turn_straight_not_allowed`
- `turn_u_turn_not_allowed`

State machine:

```text
idle -> approach -> committed -> confirmed
```

Input:

- Stable lane hiện tại.
- Trajectory sample.
- Line crossing events.
- `approach_zone`, `commit_gate`, `commit_line`.
- Per-lane maneuver config: `turn_corridor`, `exit_zone`, `exit_line`, `allowed_maneuvers`.

Xử lý:

- `_match_approach_lane`: nếu sample nằm trong `approach_zone`, chuyển sang `approach`.
- `_match_commit_lane`: nếu sample nằm trong `commit_gate` hoặc có event cắt `commit_line`, chuyển sang `committed`.
- Fallback: nếu lane không có commit signal nhưng đã có turn evidence trong lane hiện tại thì vào `committed`.
- `_update_turn_confirmation` thu thập:
  - `exit_line_matches`
  - `exit_zone_matches`
  - `corridor_matches`
  - motion features.
- Chấm điểm từng maneuver bằng `_score_maneuver_evidence`.
- Xác nhận bằng `_evidence_confirms_maneuver`.
- Nếu confirmed maneuver không nằm trong `allowed_maneuvers`, emit violation `turn_{maneuver}_not_allowed`.

Output:

- Candidate violation rẽ/đi thẳng/quay đầu sai quy định.

### Evidence fusion

File: `ViolationLogic._score_maneuver_evidence`, `_evidence_confirms_maneuver`

Evidence được cộng điểm từ:

- `corridor_hits`
- `exit_zone_hits`
- `exit_line_hits`
- `heading_support_hits`
- `curvature_support_hits`
- `opposite_direction_hits` cho u-turn
- `temporal_hits`

Các trọng số lấy từ `settings.json` nhóm `evidence_fusion.turn_scoring`.

Điều kiện chung:

- Phải có path evidence: corridor hoặc exit zone hoặc exit line.
- `strong_exit = exit_line_hits > 0 or exit_zone_hits > 0`.
- `temporal_ok = temporal_hits >= temporal_hits_min or strong_exit`.
- Score phải vượt threshold tương ứng.

Điều kiện riêng:

- `u_turn`:
  - heading change đủ lớn.
  - opposite direction có thật hoặc có hit opposite evidence.
  - curvature đủ lớn hoặc có curvature support.
  - nếu không có strong exit thì corridor hits phải đủ.
- `straight`:
  - phải có heading support.
  - nếu không có strong exit thì corridor hits phải đủ.
- `left/right`:
  - cần heading support hoặc strong exit hoặc đủ corridor hits.
  - nếu giống u-turn thì reject.
  - strong exit yếu vẫn cần temporal/corridor support.

Output evidence:

- Candidate nội bộ có thể kèm `evidence_summary`.
- Nhưng `ViolationEvent` schema hiện không có field `evidence_summary`; DB và WebSocket event hiện không lưu/gửi phần này.

### Event lifecycle / dedup

File: `ViolationLogic._emit_violation_if_needed`, `_touch_violation_lifecycle`

Input:

- `lifecycle_key`, lane, violation, timestamp.

Xử lý:

- Mỗi vehicle giữ dict `violation_lifecycles`.
- Nếu active chưa đủ `min_active_ms`, phase về `candidate`, chưa emit.
- Nếu đã emit trong cùng lifecycle, không emit lại.
- Nếu `elapsed_ms > violation_rearm_window_ms`, lifecycle expired, reset `emitted_ts`, tăng `event_window_id`, cho phép emit event mới.

Output:

- Mỗi lifecycle chỉ append candidate một lần trong cửa sổ active.

### Geometry validator

File: `backend/app/logic/geometry_validator.py:validate_lane_geometry`

Validator trả list issue gồm `level`, `code`, `message`, `lane_id`, `maneuver`, `suggestion`.

Các nhóm check:

- Lane polygon rỗng/tự cắt.
- Commit line invalid/outside lane.
- Approach zone/commit gate invalid hoặc lệch lane.
- Lane overlap lớn (`LANE_OVERLAP_DANGEROUS`).
- `allowed_maneuvers` không khớp `maneuvers.enabled/allowed`.
- Maneuver enabled nhưng thiếu geometry.
- Maneuver disabled nhưng vẫn có geometry.
- Movement path quá ngắn hoặc bắt đầu xa lane.
- Turn corridor invalid/xa lane.
- Exit zone/exit line invalid hoặc xa path.
- Thiếu exit confirm.
- U-turn path chưa đối hướng hoặc thiếu exit confirm.
- Corridor overlap ambiguity, đặc biệt u-turn overlap cao.

Frontend hiển thị các issue này trong `ManagementView.ValidationIssuesPanel`.

## 9. API / WebSocket

### REST API

File: `backend/app/api/routes.py`

| Method | Endpoint | Request chính | Response chính | Frontend dùng |
|---|---|---|---|---|
| GET | `/api/health` | none | `{status:"ok"}` | Chưa thấy frontend gọi. |
| GET | `/api/cameras` | none | `{cameras:[...]}` | `fetchCameras` trong `api.js`. |
| GET | `/api/cameras/{camera_id}` | path camera_id | camera detail, lane_config, validation, ui | Monitoring/Management. |
| POST | `/api/cameras` | `{camera, lane_config}` | camera detail sau lưu | Management tạo camera. |
| PUT | `/api/cameras/{camera_id}` | `{camera, lane_config}` | camera detail sau lưu | Management cập nhật camera. |
| DELETE | `/api/cameras/{camera_id}` | path camera_id | `{ok:true}` | Management xóa camera. |
| POST | `/api/camera/{camera_id}/background-image` | multipart `file` jpg/png | `{ok,true,camera_id,has_background_image}` | Management upload ảnh nền. |
| GET | `/api/camera/{camera_id}/background-image` | path camera_id | image file | CameraCanvas background. |
| DELETE | `/api/camera/{camera_id}/background-image` | path camera_id | `{ok, camera_id, deleted}` | Management xóa ảnh nền. |
| GET | `/api/cameras/{camera_id}/lanes` | path camera_id | lane pixel geometry + validation | Endpoint có trong backend; chưa thấy wrapper frontend hiện tại. |
| GET | `/api/cameras/{camera_id}/trajectories` | `limit`, `lane_id`, `vehicle_type` | recent trajectories | Endpoint có trong backend; chưa thấy frontend gọi hiện tại. |
| GET | `/api/cameras/{camera_id}/preview` | path camera_id | MJPEG stream | `img.src` trong MonitoringView. |
| GET | `/api/violations/evidence/{evidence_path}` | path | image/jpeg | Modal chi tiết violation. |
| GET | `/api/violations/history` | `camera_id`, `from_ts`, `to_ts`, `limit` | `{rows:[...]}` | Analytics history. |
| GET | `/api/violations/export` | `format=csv|xlsx`, filters | file download | Analytics export. |
| GET | `/api/analytics/dashboard` | `camera_id`, `from_ts`, `to_ts` | overview, series, summaries, chart_config | Analytics dashboard. |
| GET | `/api/stats` | `from_ts`, `to_ts` | `{rows:[...]}` | Chưa thấy frontend gọi. |

### WebSocket

File: `backend/app/api/ws.py`

#### `WS /ws/tracks?camera_id=...`

Backend gửi `TrackMessage.model_dump(mode="json")`:

```json
{
  "type": "track",
  "camera_id": "cam_01",
  "timestamp": "...",
  "processing_fps": 12.3,
  "vehicles": [
    {
      "vehicle_id": 1,
      "vehicle_type": "car",
      "lane_id": 2,
      "raw_lane_id": 2,
      "bbox": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    }
  ]
}
```

Frontend:

- `api.js:connectTracks`.
- `MonitoringView` dùng vehicles để vẽ bbox, danh sách xe, trajectory live, FPS.

#### `WS /ws/violations?camera_id=...`

Backend gửi:

```json
{
  "type": "violation",
  "event": {
    "id": 1,
    "camera_id": "cam_01",
    "location": {...},
    "vehicle_id": 1,
    "vehicle_type": "car",
    "lane_id": 2,
    "violation": "wrong_lane",
    "image_path": "...",
    "image_url": "/api/violations/evidence/...",
    "timestamp": "..."
  }
}
```

Frontend:

- `api.js:connectViolations`.
- `MonitoringView`: thêm vào list realtime, highlight vehicle.
- `AnalyticsView`: dùng để schedule refresh dashboard/history.

## 10. Database / Lưu Trữ

### SQLite hiện tại

File: `config/traffic_warning.sqlite`

Kết quả rà soát DB hiện tại:

- File tồn tại, dung lượng khoảng 5,316,608 bytes.
- Table:
  - `violations`
- Index:
  - `ix_violations_camera_id`
  - `ix_violations_timestamp_utc`
  - `ix_violations_vehicle_id`
  - `ix_violations_violation`
- Số bản ghi hiện tại: 19,221.
- Phân bố violation hiện tại:
  - `vehicle_type_not_allowed`: 13,886
  - `turn_left_not_allowed`: 2,435
  - `wrong_lane`: 1,545
  - `turn_right_not_allowed`: 1,111
  - `turn_straight_not_allowed`: 230
  - `wrong_direction`: 14

Ghi chú quan trọng: search source hiện tại không thấy logic phát sinh `wrong_direction`. Các row `wrong_direction` trong DB có thể là dữ liệu cũ hoặc phát sinh từ phiên bản trước; trong source hiện tại, chưa thấy code emit violation này.

### Model DB

File: `backend/app/db/models.py`

Model `Violation`:

| Column | Kiểu | Ý nghĩa |
|---|---|---|
| `id` | Integer PK | ID tự tăng. |
| `camera_id` | String indexed | Camera phát hiện. |
| `road_name` | String | Tên đường. |
| `intersection` | String nullable | Nút giao. |
| `gps_lat`, `gps_lng` | Float nullable | GPS. |
| `vehicle_id` | Integer indexed | ID xe stable trong runtime. |
| `vehicle_type` | String | Loại xe. |
| `lane_id` | Integer | Làn liên quan vi phạm. |
| `violation` | String indexed | Mã vi phạm. |
| `evidence_image_path` | String nullable | Relative path ảnh evidence. |
| `timestamp_utc` | DateTime indexed | Thời điểm UTC. |

### Repository

File: `backend/app/db/repository.py`

- `insert_violation(session, event)`: insert event, lưu timestamp UTC, lưu `evidence_image_path`.
- `query_violation_history`: trả rows cho frontend; timestamp đổi sang giờ Việt Nam bằng `to_vietnam_isoformat`.
- `query_violation_counts`: thống kê nhóm theo camera/road/intersection/vehicle_type/violation.
- `query_dashboard_analytics`: overview, camera summary, road summary, hourly series, time series dynamic granularity.
- `_build_time_series`: bucket theo timezone Việt Nam và có thể fill missing buckets.

### Schema migration nhỏ

File: `backend/app/db/database.py`

- `Base.metadata.create_all(engine)`.
- `_ensure_violation_schema(engine)` thêm column `evidence_image_path` nếu table cũ chưa có.

### Lưu trữ ngoài DB

- Camera config: JSON file, không nằm trong DB.
- Lane config: JSON file, không nằm trong DB.
- Background image: file theo camera, ví dụ hiện có `config/background_images/cam_01.png`.
- Evidence images: hiện có 10,987 file ảnh trong `config/evidence_images` theo kết quả đếm hiện tại.

## 11. Luồng Phát Hiện Vi Phạm

### Sai làn (`wrong_lane`)

Luồng:

1. Stable lane được cập nhật bởi `TemporalLaneAssigner`.
2. `ViolationLogic._update_lane_state` phát hiện chuyển lane.
3. Nếu lane mới không nằm trong `allowed_lane_changes` của lane cũ, tạo `IllegalLaneCandidate`.
4. `_update_wrong_lane_candidate` đợi đủ `wrong_lane_min_duration_ms`.
5. `_emit_violation_if_needed` chống duplicate.
6. `CameraContext._handle_violations` lưu evidence/DB/WS.

Đúng bằng logic:

- Không dùng lane raw tức thời; dùng stable lane.
- Có hysteresis ở `TemporalLaneAssigner`.
- Có `overlap_preference` ở `LaneLogic` để giảm lane drift khi bbox đè biên.
- Có corrective transition: nếu xe đang ở lane không cho phép loại xe và chuyển sang lane cho phép loại xe thì không báo sai làn.

Rủi ro:

- Nếu polygon overlap lớn hoặc lane config thiếu chính xác, stable lane vẫn có thể sai.
- Lifecycle dedup chỉ ở memory; restart backend sẽ mất state dedup.

### Rẽ sai hướng (`turn_left_not_allowed`, `turn_right_not_allowed`)

Luồng:

1. Xe vào `approach_zone` hoặc fallback theo evidence.
2. Xe qua `commit_gate` hoặc `commit_line`.
3. Hệ thống chấm điểm maneuver từ corridor/exit/heading/curvature.
4. Nếu confirmed maneuver là `left` hoặc `right` nhưng source lane không cho phép trong `allowed_maneuvers`, emit `turn_left_not_allowed` hoặc `turn_right_not_allowed`.

Đúng bằng logic:

- Không chỉ dựa vào một điểm; dùng 3 contact points.
- Line crossing cần pre/post frame và displacement.
- Left/right heading có kiểm tra dấu hướng rẽ theo anchor geometry.
- Nếu motion giống U-turn thì reject left/right.

### Quay đầu (`turn_u_turn_not_allowed`)

Luồng:

1. U-turn cần geometry path evidence: corridor/exit line/exit zone.
2. Motion phải có heading change >= `u_turn_min_change_deg`.
3. Phải có opposite direction theo dot product threshold hoặc hit opposite evidence.
4. Curvature phải đủ.
5. Score vượt threshold U-turn.
6. Nếu source lane không cho phép `u_turn`, emit `turn_u_turn_not_allowed`.

Đúng bằng logic:

- Có điều kiện riêng để tránh nhầm left/right.
- Validator cảnh báo nếu u-turn path không đối hướng hoặc overlap cao với hướng khác.

### Đi thẳng (`turn_straight_not_allowed`)

Luồng:

1. Straight cũng là một maneuver trong config.
2. Nếu có movement path/corridor/exit cho straight và evidence xác nhận `straight`.
3. `_heading_support_for_maneuver("straight")` yêu cầu heading change nhỏ và curvature thấp.
4. Nếu source lane không cho phép straight, emit `turn_straight_not_allowed`.

Điểm cần chú ý:

- Nếu lane cấm straight nhưng không có geometry đủ cho straight, hệ thống có thể không xác nhận được straight để emit. Ví dụ lane 2 `cam_01` có `straight.allowed=false` và có `movement_path`, nên backend có thể dựng corridor từ path.

### Đổi làn rồi rẽ

Hệ thống xử lý bằng hai logic độc lập nhưng cùng state:

- Sai làn dựa trên stable lane transition và `allowed_lane_changes`.
- Rẽ sai dựa trên source lane trong turn state (`turn_state.source_lane_id`).

Điểm đúng:

- `turn_state.source_lane_id` được khóa khi vào approach/commit, nên rẽ được xét theo lane nguồn thay vì lane hiện tại sau khi xe đã đi sang nhánh khác.

Điểm cần chú ý:

- Nếu thiếu `approach_zone`/`commit_gate`/`commit_line`, source lane có thể dựa vào fallback evidence lane hiện tại; rủi ro khóa nhầm source lane cao hơn.

### Lane drift khi ôm cua

Các cơ chế giảm drift:

- `LaneLogic` dùng đáy bbox và overlap segment, không chỉ dùng center point.
- `preferred_lane_id` giữ lane ổn định nếu overlap còn đủ.
- `TemporalLaneAssigner` dùng majority window và switch delay.
- `wrong_lane_min_duration_ms` yêu cầu vi phạm tồn tại đủ lâu.

Rủi ro còn lại:

- Nếu polygon chồng lấn quá lớn, validator chỉ cảnh báo, không chặn lưu.
- Nếu xe ôm cua làm bbox đáy cắt sang lane khác lâu hơn switch delay, stable lane vẫn có thể đổi.

### Polygon overlap

Backend runtime:

- `LaneLogic` có tie-break/overlap preference.

Backend validator:

- `validate_lane_geometry` cảnh báo `LANE_OVERLAP_DANGEROUS` khi overlap ratio >= 0.12.
- Cảnh báo path/corridor overlap bằng `PATH_OVERLAP_AMBIGUOUS`, `UTURN_OVERLAP_HIGH`.

Frontend:

- `ValidationIssuesPanel` hiển thị các warning/error/info.

### Duplicate event

Logic:

- `_emit_violation_if_needed` không append nếu lifecycle đã có `emitted_ts`.
- `_touch_violation_lifecycle` chỉ re-arm sau `violation_rearm_window_ms`.

Phạm vi:

- Dedup theo từng `VehicleState`, tức là theo stable vehicle ID trong memory.
- Không thấy dedup ở DB theo unique constraint.
- Không thấy dedup cross-restart.

## 12. Đánh Giá Hệ Thống

### Điểm mạnh

- Pipeline end-to-end rõ: video -> AI/tracking -> geometry -> event -> DB/WS/UI.
- Tách module tốt: detector, tracker, lane logic, violation logic, repository, API, frontend views.
- Config lane-centric/maneuver-centric linh hoạt cho nhiều nút giao.
- Backend tự normalize/denormalize geometry, giảm lỗi phụ thuộc độ phân giải.
- Movement path tự sinh corridor, frontend không phải vẽ `turn_corridor` trực tiếp.
- Lane assignment dùng đáy bbox và overlap segment, tốt hơn single point.
- Có smoothing cho lane, vehicle type và stable track ID.
- Turn detection không dựa vào một signal; có evidence fusion, line crossing state, heading, curvature, opposite direction.
- Có semantic geometry validator giúp giảm cấu hình gây false positive/false negative.
- Có WebSocket realtime và MJPEG preview đơn giản cho frontend.
- Có lưu ảnh bằng chứng và export CSV/XLSX.
- Có test backend cho lane logic, u-turn, line crossing, timezone, config loading, RTSP shutdown, vehicle type smoothing.

### Hạn chế / rủi ro

- Chưa thấy authentication/authorization trong backend hoặc frontend.
- Chưa thấy quản lý user/role/audit log.
- Mỗi `CameraContext` load detector riêng; nhiều camera có thể tốn RAM/VRAM lớn.
- Inference YOLO/ByteTrack là bottleneck chính; chưa thấy cơ chế phân phối GPU, batching hoặc frame skipping thông minh ngoài `only_new`.
- ByteTrack và stable ID vẫn có thể sai khi occlusion mạnh, camera rung, vật thể quá gần nhau.
- Config geometry phụ thuộc người dùng vẽ đúng; validator cảnh báo nhưng không chặn mọi cấu hình rủi ro.
- Nhiều camera hiện tại (`cam_02`, `cam_03`) bật các maneuver bị cấm nhưng thiếu movement/exit geometry, dễ false negative cho rẽ sai.
- `evidence_summary` chỉ tồn tại trong candidate nội bộ, chưa lưu DB và chưa gửi frontend.
- Dedup lifecycle chỉ trong memory; restart backend làm mất state.
- SQLite phù hợp đồ án/demo, nhưng chưa thấy thiết kế scale write lớn/nhiều backend instance.
- Xóa camera sẽ xóa cả evidence images của camera; chưa thấy retention/archive policy.
- DB hiện có violation `wrong_direction`, nhưng current source không có logic emit `wrong_direction`; cần làm rõ khi báo cáo dữ liệu lịch sử.
- Frontend Monitoring tự dựng trajectory live từ WebSocket track, chưa dùng endpoint backend `/trajectories`; do đó reload màn hình sẽ mất trajectory live đang dựng.
- Chưa thấy endpoint chi tiết violation theo ID; modal dùng dữ liệu inline từ history/WS.
- Chưa thấy health status chi tiết từng camera/source; chỉ có `runtime_applied` và preview nếu có frame.

### False positive tiềm ẩn

- Lane polygon overlap hoặc vẽ sai biên lane.
- Bbox YOLO rung làm đáy bbox nhảy lane, dù đã có smoothing.
- Exit line đặt quá gần vùng nhiễu hoặc có crossing do jitter.
- Movement path/corridor overlap giữa left/right/u_turn.
- Vehicle type smoothing vẫn có thể sai nếu YOLO nhầm nhất quán trong vài giây.

### False negative tiềm ẩn

- Maneuver bị cấm nhưng thiếu movement_path/exit_line/exit_zone.
- Xe vi phạm nhưng không đi qua corridor/exit geometry đã vẽ.
- Track mất ID ngay trước/sau commit khiến lifecycle/trajectory reset.
- U-turn thiếu opposite-direction rõ do góc camera hoặc path quá ngắn.
- YOLO miss xe nhỏ/xa nếu confidence threshold cao hoặc model nhẹ.

### Bottleneck hiệu năng

- YOLO inference trong `YoloByteTrackVehicleTracker.track`.
- OpenCV decode nhiều camera.
- Shapely geometry lặp theo số xe/lane/maneuver, đã có `prepare` nhưng vẫn tăng theo cấu hình.
- JPEG encode preview/evidence.
- WebSocket queue nếu frontend chậm; đã có `listener_queue_maxsize` để bỏ listener queue đầy.

### Cải thiện sau này

- Thêm auth/role và audit log.
- Tách camera status/health endpoint: connected, last frame time, FPS, error message.
- Chia sẻ YOLO model hoặc quản lý worker/GPU pool cho nhiều camera.
- Lưu `evidence_summary` vào DB để phục vụ báo cáo giải thích quyết định.
- Thêm violation detail endpoint theo ID.
- Thêm unique/dedup DB-level nếu cần chống trùng sau restart.
- Thêm cấu hình retention cho evidence images.
- Thêm UI replay/trajectory từ backend endpoint `/trajectories`.
- Thêm validate URL/source camera ở frontend/backend.
- Thêm migration tool có version thay vì schema patch thủ công.
- Làm rõ hoặc xóa/di trú dữ liệu `wrong_direction` cũ nếu không còn dùng.

## 13. Nội Dung Gợi Ý Đưa Vào Báo Cáo

### Kiến trúc hệ thống

Hệ thống được thiết kế theo kiến trúc client-server. Backend FastAPI chịu trách nhiệm xử lý video, nhận diện phương tiện, theo dõi đối tượng, phân tích hình học làn đường và phát hiện vi phạm. Frontend React cung cấp giao diện giám sát thời gian thực, thống kê lịch sử và công cụ cấu hình camera/làn. Cấu hình camera/làn được lưu bằng JSON, còn sự kiện vi phạm được lưu trong SQLite.

### Công nghệ sử dụng

- Backend: FastAPI, Uvicorn, OpenCV, Ultralytics YOLOv8, ByteTrack, Shapely, Pydantic, SQLAlchemy, SQLite.
- Frontend: React, Vite, lucide-react, Canvas 2D.
- Realtime: WebSocket cho track/violation, MJPEG cho preview camera.
- Export: CSV và XLSX bằng `csv` và `openpyxl`.

### Thuật toán nhận diện

Hệ thống dùng YOLOv8 qua thư viện Ultralytics. Model được load từ đường dẫn `detection.weights_path`, tự chọn CPU/GPU theo `detection.device`. Hệ thống chỉ giữ các lớp phương tiện `motorcycle`, `car`, `truck`, `bus`. Detection được chạy trong API `model.track`, kết hợp trực tiếp với ByteTrack.

### Thuật toán theo dõi

ByteTrack được gọi thông qua `YOLO.model.track(..., persist=True, tracker="bytetrack.yaml")`. Sau ByteTrack, hệ thống dùng `StableTrackIdAssigner` để ổn định ID bằng IoU, khoảng cách tâm chuẩn hóa, loại xe và confidence. Nhãn loại xe được làm mượt bằng `TemporalVehicleTypeAssigner`, bỏ phiếu theo confidence và ưu tiên nhẹ quan sát mới.

### Thuật toán gán làn

Hệ thống không dùng AI nhận diện làn; làn được cấu hình bằng polygon. Với mỗi xe, hệ thống lấy cạnh đáy bounding box làm vùng tiếp xúc với mặt đường, tính độ dài overlap giữa cạnh đáy và từng polygon làn, sau đó kết hợp lane ổn định trước đó để hạn chế nhảy làn. Kết quả raw lane tiếp tục đi qua `TemporalLaneAssigner` để tạo stable lane bằng majority window và hysteresis.

### Thuật toán phát hiện vi phạm

Hệ thống phát hiện các lỗi:

- `wrong_lane`: xe chuyển sang lane không nằm trong `allowed_lane_changes`.
- `vehicle_type_not_allowed`: loại xe không nằm trong `allowed_vehicle_types`.
- `turn_left_not_allowed`, `turn_right_not_allowed`, `turn_straight_not_allowed`, `turn_u_turn_not_allowed`: maneuver được xác nhận nhưng không nằm trong hướng được phép của lane nguồn.

Maneuver được xác nhận bằng evidence fusion gồm corridor, exit line, exit zone, heading, curvature, opposite direction và temporal continuity. Event lifecycle đảm bảo mỗi vi phạm không bị phát lặp liên tục trong cùng một cửa sổ thời gian.

### Thiết kế giao diện

Frontend gồm 3 màn hình:

- Giám sát: preview camera, overlay lane/bbox/trajectory/FPS, danh sách xe và vi phạm realtime.
- Thống kê: dashboard tổng quan, biểu đồ theo thời gian, lịch sử vi phạm, export CSV/XLSX.
- Quản lý camera: thêm/sửa/xóa camera, cấu hình lane, allowed lane changes, allowed vehicle types, maneuver enabled/allowed, movement path, exit line/zone, background image và cảnh báo validator.

### Thiết kế cấu hình

Cấu hình theo hướng lane-centric và maneuver-centric. Mỗi camera có file lane config riêng. Mỗi lane chứa polygon, vùng approach/commit, luật đổi làn, loại xe cho phép và cấu hình từng maneuver. Tọa độ được lưu normalized `[0,1]` để không phụ thuộc kích thước canvas; backend denormalize sang pixel lúc chạy.

### Thiết kế cơ sở dữ liệu

SQLite chỉ lưu bản ghi vi phạm trong bảng `violations`. Camera và lane config lưu bằng JSON để dễ chỉnh sửa và triển khai demo. Mỗi bản ghi vi phạm lưu camera, vị trí, vehicle ID, vehicle type, lane, loại vi phạm, đường dẫn ảnh bằng chứng và timestamp UTC.

### Ưu điểm hệ thống

- Có pipeline hoàn chỉnh từ video đến cảnh báo realtime.
- Kết hợp AI detection/tracking với luật hình học có thể giải thích được.
- Có công cụ cấu hình trực quan cho lane và maneuver.
- Có validator cảnh báo cấu hình rủi ro.
- Có evidence image, lịch sử, dashboard và export.

### Hướng phát triển

- Thêm xác thực người dùng và phân quyền.
- Thêm dashboard health cho từng camera.
- Tối ưu inference đa camera bằng worker/GPU pool.
- Lưu evidence summary để giải thích quyết định trong báo cáo/điều tra.
- Thêm endpoint chi tiết violation theo ID.
- Thêm retention policy cho ảnh bằng chứng.
- Mở rộng DB/migration cho triển khai production.
- Chuẩn hóa lại dữ liệu lịch sử cũ như `wrong_direction` nếu không còn logic runtime tương ứng.
