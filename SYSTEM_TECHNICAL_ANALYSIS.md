# Tài liệu phân tích kỹ thuật hệ thống Traffic Warning

Ngày rà soát: 2026-05-08

Phạm vi: tài liệu này đồng bộ theo source hiện tại trong `backend`, `frontend`, `config`, `backend/requirements.txt` và `frontend/package.json`. Nội dung tập trung vào kiến trúc, luồng dữ liệu, thuật toán, config, API, DB, rủi ro kỹ thuật và hướng phát triển.

## 1. Tổng Quan

Traffic Warning là hệ thống giám sát giao thông thời gian thực theo kiến trúc client-server:

- Backend FastAPI xử lý video, AI detection/tracking, logic hình học, phát hiện vi phạm, OCR biển số, lưu dữ liệu và phát realtime.
- Frontend React hiển thị camera preview, overlay lane/bbox/trajectory/FPS, lịch sử/thống kê và giao diện cấu hình camera/lane/maneuver.
- Config dùng JSON cho camera/lane/settings; SQLite dùng để lưu lịch sử vi phạm.

Pipeline chính:

```text
Video source
  -> OpenCV frame reader
  -> YOLOv8 + ByteTrack
  -> stable track ID
  -> vehicle type smoothing
  -> lane assignment bằng polygon
  -> direction / lane / maneuver / vehicle-type violation logic
  -> license plate detector + OCR nếu bật
  -> evidence image + SQLite
  -> REST API / WebSocket / MJPEG
  -> React frontend
```

Các module lớn:

| Module | File/thư mục | Vai trò |
|---|---|---|
| Server | `backend/app/server.py` | Tạo FastAPI app, CORS, lifespan startup/shutdown, đăng ký REST/WS router. |
| Camera manager | `backend/app/managers/camera_manager.py` | Load config, quản lý nhiều `CameraContext`, reload runtime, query DB, quản lý listener realtime. |
| Camera context | `backend/app/managers/camera_context.py` | Pipeline xử lý cho từng camera, OCR worker, preview worker, evidence, DB, WebSocket callbacks. |
| Video input | `backend/app/rtsp/rtsp_stream.py` | Thread đọc RTSP/HTTP/file local bằng OpenCV, resize frame, pacing file local. |
| Vision | `backend/app/vision` | YOLO detector phương tiện, detector biển số, OCR Paddle/EasyOCR, resolver device/backend. |
| Tracking | `backend/app/tracking/tracker.py` | Gọi `YOLO.model.track(..., persist=True)` với ByteTrack. |
| Logic | `backend/app/logic` | Lane assignment, direction detection, violation engine, OCR temporal voting, stable track ID, geometry validator. |
| DB | `backend/app/db` | SQLAlchemy model, SQLite engine/session, repository insert/query/dashboard. |
| API | `backend/app/api` | REST API, MJPEG preview, WebSocket tracks/violations. |
| Frontend | `frontend/src` | SPA React cho giám sát, thống kê, quản lý camera/lane/maneuver. |

## 2. Công Nghệ

Backend dependency chính:

| Công nghệ | Vai trò |
|---|---|
| FastAPI, Starlette, Uvicorn | REST API, WebSocket, ASGI runtime. |
| OpenCV | Đọc video, resize frame, encode JPEG preview/evidence. |
| NumPy | Frame/image array. |
| Ultralytics YOLOv8 | Detection/tracking phương tiện và detector biển số. |
| ByteTrack | Object tracking qua Ultralytics `model.track`. |
| PyTorch/Torchvision | Inference `.pt` mặc định. |
| Shapely | Polygon/LineString, point-in-polygon, line intersection, geometry validation. |
| Pydantic | Schema config/API/event. |
| SQLAlchemy | ORM SQLite. |
| PaddleOCR, EasyOCR | OCR biển số. |
| openpyxl | Export XLSX. |
| pytest | Test backend. |

Frontend dependency chính:

| Công nghệ | Vai trò |
|---|---|
| React 18 | UI SPA. |
| Vite | Dev server/build. |
| lucide-react | Icon UI. |
| Canvas 2D | Vẽ preview overlay, lane, bbox, trajectory và editor geometry. |

## 3. Backend Lifecycle

### `server.py`

`create_app()` tạo FastAPI app, khởi tạo `CameraManager(repo_root)`, include REST/WS router và gắn lifespan:

- Startup: `manager.start()`.
- Shutdown: `manager.stop()`.
- Có guard cho lỗi WebSocket client reset trên Windows (`WinError 10054`).

### `CameraManager`

Vai trò:

- Load `config/settings.json`, `config/cameras.json`, lane config.
- Validate không có shared lane giữa camera bằng `validate_no_shared_lanes_across_cameras`.
- Tạo SQLAlchemy engine/session từ `database.path`.
- Tạo/dừng/reload `CameraContext` theo camera.
- Quản lý queue listener cho WebSocket tracks/violations.
- Cung cấp API thao tác camera, background image, evidence path, history/dashboard.

Các hành vi đáng chú ý:

- `upsert_camera()` ghi camera/lane config xuống JSON rồi reload context runtime nếu manager đang chạy.
- `delete_camera()` dừng context, xóa camera config, lane config, background image và evidence images của camera.
- Queue WebSocket đầy sẽ bị loại khỏi listener để tránh backpressure kéo chậm toàn hệ thống.

### `CameraContext`

`CameraContext.run_forever()` là vòng lặp realtime của từng camera:

1. Đọc frame mới bằng `RtspFrameReader.read(only_new=True)`.
2. Gửi frame sang preview worker để encode JPEG theo `preview.max_fps`.
3. Chạy tracker trong thread bằng `asyncio.to_thread`.
4. Ổn định raw track ID bằng `StableTrackIdAssigner`.
5. Chuẩn bị snapshot OCR biển số hiện có và enqueue job OCR mới nếu đủ interval.
6. Với từng track:
   - Làm mượt loại xe bằng `TemporalVehicleTypeAssigner`.
   - Tính raw lane bằng `LaneLogic`.
   - Làm mượt lane bằng `TemporalLaneAssigner`.
   - Tạo `TrackVehicle` cho WebSocket.
   - Gọi `ViolationLogic.update_and_maybe_generate_violation`.
7. Push `TrackMessage` theo `websocket.track_push_interval_ms`.
8. Nếu có violation candidate, tạo evidence, lưu DB và push `ViolationEvent`.
9. Prune state định kỳ theo `performance.processing.prune_interval_ms` và `event_lifecycle.state_prune_max_age_s`.
10. Tính processing FPS bằng cửa sổ trượt.

Worker nền:

- Preview worker chỉ giữ frame mới nhất, không encode mọi frame.
- OCR worker dùng queue theo `vehicle_id`; mỗi xe chỉ giữ job mới nhất để tránh backlog cũ.
- OCR worker có batch size và max pending jobs để bảo vệ pipeline realtime.

## 4. AI, Tracking Và OCR

### Detector phương tiện

File: `backend/app/vision/detector.py`

- Load YOLO từ `detection.weights_path`.
- Resolve inference backend bằng `resolve_inference_backend`.
- Resolve device bằng `resolve_inference_device`.
- Lọc class theo `detection.allowed_classes`.
- Nếu load weight `.pt` thất bại, thử fallback model cùng thư mục: `yolov8x.pt`, `yolov8l.pt`, `yolov8m.pt`, `yolov8s.pt`, `yolov8n.pt`.

Backend inference được source hỗ trợ:

| Backend | Điều kiện weight |
|---|---|
| `pytorch` | Mặc định hoặc `.pt`. |
| `tensorrt` | `.engine`. |
| `openvino` | `.xml` hoặc `_openvino_model`. |
| `onnxruntime` | `.onnx`. |

### Tracking

File: `backend/app/tracking/tracker.py`

- Gọi `self.detector.model.track(...)`.
- Dùng `persist=True` để Ultralytics/ByteTrack giữ state tracking qua frame.
- Chỉ emit track khi `r.boxes.id` tồn tại.
- Convert tensor về numpy CPU, lọc lại class theo allowlist, trả `Track`.

### Stable track ID

File: `backend/app/logic/track_id_logic.py`

Mục tiêu là giảm nhảy `vehicle_id` khi ByteTrack đổi raw ID:

- Lượt 1: giữ mapping raw ID -> stable ID nếu IoU/khoảng cách vẫn hợp lý.
- Lượt 2: nếu raw ID mới, thử rebind với stable state còn mới bằng IoU và normalized center distance.
- Lượt 3: cấp stable ID mới nếu không match được.
- Prune stable state quá hạn để tránh tái dùng nhầm ID.

### OCR biển số

File: `backend/app/vision/license_plate_ocr.py`, `backend/app/logic/license_plate_logic.py`

Pipeline:

1. Crop vùng xe theo bbox đã nới biên.
2. Detector biển số tìm bbox biển số trong crop xe.
3. OCR đọc text bằng `paddleocr` hoặc `easyocr`.
4. `LicensePlateTemporalResolver` chuẩn hóa text, lọc confidence, vote trong cửa sổ thời gian.
5. Snapshot trả về `license_plate`, `status`, `confidence`.

Trạng thái OCR:

| Trạng thái | Ý nghĩa |
|---|---|
| `pending` | Đang chờ đủ bằng chứng OCR. |
| `confirmed` | Text đủ số hit và confidence. |
| `uncertain` | Có nhiều candidate cạnh tranh. |
| `unreadable` | Quá số lần thử nhưng không có candidate hợp lệ. |

## 5. Lane Assignment Và Geometry

### Geometry low-level

File: `backend/app/logic/polygon.py`

- `PreparedPolygon`: Shapely polygon đã `prepare()` để tăng tốc contains/intersection.
- `PreparedLine`: line dùng cho crossing detection.
- `bbox_bottom_contact_points`: lấy 3 điểm 1/4, 1/2, 3/4 cạnh đáy bbox để đại diện vị trí bánh xe/mặt đường.
- `signed_distance_to_line`: khoảng cách có dấu để biết điểm nằm phía nào của vạch.

### Raw lane assignment

File: `backend/app/logic/lane_logic.py`

`LaneLogic.observe_lane_from_bbox_xyxy()`:

- Lấy tâm đáy và 2 điểm tiếp xúc cạnh đáy bbox.
- Tính độ dài overlap giữa đoạn đáy bbox và từng lane polygon.
- Chuẩn hóa overlap thành `overlap_ratio`.
- Cộng bonus khi tâm/trái/phải đáy bbox nằm trong lane.
- Tạo `LaneScore` cho từng lane.
- Chọn raw lane theo overlap, preferred lane hysteresis, center fallback và confidence tie-break.

### Temporal lane assignment

`TemporalLaneAssigner.resolve_lane()`:

- Giữ deque observation theo `vehicle_id`.
- Cắt cửa sổ theo `lane_assignment.temporal.observation_window_ms`.
- Vote bằng tổng confidence theo lane.
- Chỉ khởi tạo stable lane khi đủ `min_majority_hits`.
- Khi đổi lane, yêu cầu majority ratio, confidence, source lane yếu, target thắng rõ, target có consecutive frames và pending đủ `switch_min_duration_ms`.

### Geometry validator

File: `backend/app/logic/geometry_validator.py`

Validator kiểm tra các rủi ro cấu hình:

- Polygon lane/zone không hợp lệ.
- Lane overlap cao.
- Approach/commit/turn/exit geometry thiếu hoặc mâu thuẫn.
- Direction path thiếu hoặc không đủ điểm.
- Maneuver path overlap dễ gây nhầm left/right/u_turn.
- Cảnh báo semantic được trả trong API camera detail/lane payload để UI hiển thị.

## 6. Violation Engine

File: `backend/app/logic/violation_logic.py`

`VehicleState` giữ state theo xe:

- Stable lane hiện tại và thời điểm bắt đầu lane.
- Lane history.
- Illegal lane candidate.
- Turn state machine.
- Trajectory deque.
- Line crossing states.
- Violation lifecycle.
- Direction status/dot.

### Sai làn

Điều kiện chính:

- Stable lane chuyển từ source sang target.
- Target không nằm trong `allowed_lane_changes` của source.
- Không phải corrective transition từ lane cấm loại xe sang lane cho phép loại xe.
- Candidate tồn tại đủ `wrong_lane.min_duration_ms`.
- Nếu có lane observation chi tiết, cần đủ observed frames, target majority ratio, target confidence, source confidence thấp và lateral displacement đủ lớn.

### Sai loại phương tiện

Điều kiện chính:

- Xe có stable lane.
- Lane có `allowed_vehicle_types`.
- `vehicle_type` hiện tại không nằm trong danh sách cho phép.
- `_emit_violation_if_needed` áp dụng active-time tối thiểu để giảm false positive tức thời.

### Đi ngược chiều

File: `backend/app/logic/direction_logic.py`

Điều kiện chính:

- Lane có `direction_rule.enabled`.
- Lấy trajectory kể từ khi xe vào lane hiện tại.
- Cắt đoạn liên tục gần nhất, bỏ segment gap quá lớn.
- Chỉ đánh giá khi điểm hiện tại nằm trong `check_zone` và không nằm trong vùng loại trừ.
- Tạo segment observations từ trajectory đủ displacement.
- So vector segment với vector direction path gần midpoint.
- Tính dot/cosine, same/opposite ratio, tail consensus và opposite displacement.
- Opposite phải đi qua candidate window `direction_detection.defaults.min_duration_ms` trước khi emit `wrong_direction`.

### Rẽ/đi thẳng/quay đầu sai quy định

Turn state machine:

```text
idle -> approach -> committed -> confirmed
```

Nguồn evidence:

- `approach_zone`
- `commit_gate`
- `commit_line`
- `turn_zone`
- `exit_line`
- `exit_zone`
- heading change
- curvature
- opposite direction
- temporal continuity

`_score_maneuver_evidence()` cộng/trừ điểm theo từng frame. `_evidence_confirms_maneuver()` xác nhận maneuver khi đủ path evidence, score threshold và điều kiện riêng cho `straight`, `left`, `right`, `u_turn`.

Sau khi xác nhận maneuver, nếu maneuver không nằm trong `allowed_maneuvers` của lane nguồn thì emit:

- `turn_left_not_allowed`
- `turn_right_not_allowed`
- `turn_straight_not_allowed`
- `turn_u_turn_not_allowed`

### Lifecycle chống trùng

`_emit_violation_if_needed()` và `_touch_violation_lifecycle()`:

- Mỗi vi phạm có `lifecycle_key` riêng theo vehicle/lane/rule.
- Event chỉ append một lần trong cùng event window.
- Sau `event_lifecycle.violation_rearm_window_ms`, lifecycle được re-arm để cho phép phát lại nếu hành vi tái diễn.
- Dedup hiện nằm trong memory, không phải unique constraint ở DB.

## 7. Evidence, DB Và Export

### Evidence image

File: `backend/app/core/evidence_images.py`

- File evidence lưu dưới `config/evidence_images/<camera_id>/<dd-mm-yyyy>/...jpg`.
- Tên file chứa camera, timestamp UTC ms, vehicle, lane và violation.
- `resolve_evidence_image_path()` chặn path traversal bằng kiểm tra candidate nằm trong base dir.
- Ảnh bằng chứng tổng quan crop quanh bbox xe; nếu crop quá nhỏ thì fallback phù hợp theo logic trong `CameraContext`.
- Ảnh crop biển số được lưu riêng với suffix violation `_license_plate`.

### SQLite schema

Model `Violation` hiện tại:

| Cột | Ý nghĩa |
|---|---|
| `id` | Primary key tự tăng. |
| `camera_id` | Camera phát hiện. |
| `road_name`, `intersection`, `gps_lat`, `gps_lng` | Vị trí camera. |
| `vehicle_id` | Stable vehicle ID trong phiên runtime. |
| `vehicle_type` | Loại xe đã làm mượt. |
| `lane_id` | Lane liên quan. |
| `violation` | Mã vi phạm. |
| `evidence_image_path` | Relative path ảnh bằng chứng tổng quan. |
| `license_plate` | Biển số OCR nếu có. |
| `license_plate_status` | Trạng thái OCR. |
| `license_plate_confidence` | Confidence OCR. |
| `license_plate_image_path` | Relative path ảnh crop biển số nếu có. |
| `track_session_id` | Phiên runtime của camera context. |
| `timestamp_utc` | Thời điểm UTC. |

`backend/app/db/database.py` có schema patch để thêm các cột mới nếu DB cũ thiếu.

### Repository

File: `backend/app/db/repository.py`

- `insert_violation()`: insert event vào SQLite và gán `event.id`.
- `query_violation_history()`: filter theo thời gian/camera/biển số/limit, trả timestamp theo giờ Việt Nam.
- `query_violation_counts()`: thống kê count theo filter.
- `query_dashboard_analytics()`: overview, camera/road/vehicle/violation summary và time series theo granularity.
- Time series fill missing buckets theo timezone Việt Nam.

### Export

File: `backend/app/core/violation_exports.py`

- `build_violation_export_rows()`: map raw history row sang nhãn tiếng Việt, link evidence tuyệt đối.
- `build_violation_history_csv()`: CSV có BOM UTF-8 để Excel đọc tiếng Việt.
- `build_violation_history_xlsx()`: Excel có header bold, freeze pane và auto width có giới hạn.

## 8. API Và WebSocket

### REST API

| Endpoint | Vai trò |
|---|---|
| `GET /api/health` | Healthcheck. |
| `GET /api/cameras` | Danh sách camera. |
| `GET /api/cameras/{camera_id}` | Camera detail, lane config, validation, runtime status, UI config. |
| `POST /api/cameras` | Tạo camera + lane config. |
| `PUT /api/cameras/{camera_id}` | Cập nhật camera + lane config. |
| `DELETE /api/cameras/{camera_id}` | Xóa camera và dữ liệu file liên quan. |
| `POST /api/camera/{camera_id}/background-image` | Upload ảnh nền JPG/PNG. |
| `GET /api/camera/{camera_id}/background-image` | Lấy ảnh nền. |
| `DELETE /api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET /api/cameras/{camera_id}/lanes` | Lane polygons dạng pixel, kèm validation. |
| `GET /api/cameras/{camera_id}/trajectories` | Trajectory runtime gần đây. |
| `GET /api/cameras/{camera_id}/preview` | MJPEG stream. |
| `GET /api/violations/evidence/{evidence_path}` | Evidence image. |
| `GET /api/violations/history` | Lịch sử vi phạm. |
| `GET /api/violations/export` | Export CSV/XLSX. |
| `GET /api/analytics/dashboard` | Dashboard analytics. |
| `GET /api/stats` | Count thống kê theo filter. |

### WebSocket

`WS /ws/tracks?camera_id=...` gửi `TrackMessage`:

- `camera_id`
- `timestamp`
- `processing_fps`
- `vehicles[]`
- mỗi xe có `vehicle_id`, `vehicle_type`, `lane_id`, `raw_lane_id`, `license_plate`, `license_plate_status`, `license_plate_confidence`, `direction_status`, `direction_dot`, `bbox`.

`WS /ws/violations?camera_id=...` gửi:

```json
{
  "type": "violation",
  "event": {
    "id": 1,
    "camera_id": "cam_01",
    "vehicle_id": 1,
    "vehicle_type": "car",
    "lane_id": 2,
    "violation": "wrong_lane",
    "image_url": "/api/violations/evidence/...",
    "license_plate": "30A12345",
    "timestamp": "..."
  }
}
```

WebSocket handler có cơ chế chờ song song giữa queue nội bộ và disconnect message để nhận biết client đóng kết nối.

## 9. Frontend

### App lifecycle

File: `frontend/src/App.jsx`

- Load cameras từ backend.
- Chọn camera hiện tại.
- Điều hướng 3 màn hình: `MonitoringView`, `AnalyticsView`, `ManagementView`.

### Monitoring

File: `frontend/src/views/MonitoringView.jsx`

Nguồn dữ liệu:

- Camera detail/lane config qua REST.
- MJPEG preview qua `/api/cameras/{camera_id}/preview`.
- Tracks realtime qua `/ws/tracks`.
- Violations realtime qua `/ws/violations`.

UI hiển thị:

- Preview camera.
- Overlay lane, bbox, trajectory, FPS.
- Danh sách xe đang theo dõi.
- Danh sách vi phạm realtime.
- Highlight xe vừa vi phạm theo `ui.monitoring.violation.highlight_duration_ms`.

### Analytics

File: `frontend/src/views/AnalyticsView.jsx`

- Gọi dashboard và history.
- Refresh khi nhận violation WebSocket phù hợp filter.
- Export CSV/XLSX.
- Hiển thị overview, bar chart, time series, bảng lịch sử và modal chi tiết.

### Management

File: `frontend/src/views/ManagementView.jsx`

Chức năng:

- Tạo/sửa/xóa camera.
- Cấu hình metadata camera, source video, frame size, location.
- Thêm/xóa/sửa lane.
- Chỉnh `allowed_lane_changes`, `allowed_vehicle_types`, maneuver enabled/allowed.
- Vẽ/chỉnh lane polygon, approach zone, commit gate/line, direction path/check zone, turn zone, exit line/zone.
- Upload/xóa background image.
- Undo/redo geometry.
- Validate local và hiển thị validation từ backend.

### Canvas

File: `frontend/src/components/CameraCanvas.jsx`

- Denormalize geometry để vẽ theo pixel.
- Vẽ background image, lane/maneuver geometry, bbox, labels, trajectory, FPS.
- Hỗ trợ thêm điểm, kéo vertex, chèn điểm vào cạnh, kéo cả polygon/line và clamp trong frame.
- Khi save, payload gửi về backend ở tọa độ normalized `[0, 1]`.

## 10. Config Hiện Tại

`config/settings.json` hiện đang có các điểm đáng chú ý:

- `detection.weights_path`: `backend/yolov8m.pt`.
- `detection.device`: `auto`.
- `detection.confidence_threshold`: `0.25`.
- `tracking.vehicle_type_history.window_ms`: `5000`.
- `wrong_lane.min_duration_ms`: `900`.
- `direction_detection.defaults.opposite_direction_cos_threshold`: `-0.45`.
- `websocket.track_push_interval_ms`: `250`.
- `license_plate.enabled`: `true`.
- `license_plate.ocr_backend`: `paddleocr`.
- `license_plate.easyocr_use_gpu`: `true`.
- `license_plate.paddle_use_gpu`: `false`.

Config geometry:

- Lane/maneuver/direction geometry được validate normalized `[0, 1]`.
- `allowed_maneuvers` có thể được tự suy ra từ `maneuvers.*.allowed` khi không khai báo.
- Nếu maneuver `enabled=false`, backend ép `allowed=false`.

## 11. Kiểm Thử Hiện Có

`backend/tests` bao phủ các vùng quan trọng:

- Config loading/normalization.
- Lane logic và temporal lane assignment.
- Vehicle type smoothing.
- RTSP shutdown.
- Direction/line crossing/turn related behavior.
- Timezone.
- API behavior ở một số phần.

Các lệnh kiểm thử:

```powershell
python -m compileall backend/app
cd backend
python -m pytest tests -q
cd ../frontend
npm run build
```

## 12. Điểm Mạnh

- Pipeline end-to-end rõ ràng từ video đến cảnh báo realtime.
- Tách module tốt: vision, tracking, lane, direction, violation, API, DB, frontend.
- Lane assignment giải thích được bằng hình học, không phụ thuộc AI lane detection.
- Có smoothing nhiều lớp: stable track ID, vehicle type, lane ID.
- Direction detection có consensus, tail segment, displacement và candidate window.
- Turn detection dùng evidence fusion thay vì single signal.
- Lifecycle chống phát trùng trong runtime.
- OCR biển số tách worker nền, có temporal voting và degrade an toàn khi OCR lỗi.
- UI có công cụ cấu hình lane/maneuver trực quan và validator.
- Có evidence image, dashboard, history và export CSV/XLSX.

## 13. Rủi Ro Và Hạn Chế

- Chưa có authentication/authorization/user role.
- Chưa có audit log cho thao tác cấu hình.
- Mỗi `CameraContext` tự load detector/tracker, nhiều camera có thể tốn RAM/VRAM.
- YOLO/ByteTrack vẫn là bottleneck chính khi nhiều camera hoặc model lớn.
- SQLite phù hợp demo/đồ án, chưa tối ưu cho nhiều writer hoặc triển khai nhiều instance.
- Dedup lifecycle nằm trong memory, restart backend sẽ mất trạng thái chống trùng.
- `evidence_summary` chưa được lưu trong DB schema hiện tại.
- Xóa camera sẽ xóa evidence image của camera, chưa có retention/archive policy.
- Geometry phụ thuộc người dùng vẽ đúng; validator cảnh báo nhưng không thể loại toàn bộ cấu hình rủi ro.
- Frontend trajectory live chủ yếu dựng từ WebSocket track; reload màn hình sẽ mất trajectory live đang có.
- Chưa có endpoint health chi tiết cho từng camera: connected, last frame time, FPS, error message.

## 14. False Positive Và False Negative Tiềm Ẩn

False positive:

- Lane polygon overlap hoặc vẽ sai biên lane.
- BBox rung mạnh làm đáy bbox nhảy lane.
- Exit/commit line đặt quá gần vùng nhiễu.
- Turn zone/exit zone overlap giữa nhiều maneuver.
- OCR đọc sai nhưng đủ confidence trong vài lần liên tiếp.

False negative:

- Maneuver bị cấm nhưng thiếu `turn_zone`, `exit_line` hoặc `exit_zone`.
- Xe vi phạm không đi qua vùng/vạch đã vẽ.
- Track mất ID ngay trước/sau commit.
- Xe đi quá chậm hoặc đứng yên làm direction vector không đủ displacement.
- Model YOLO bỏ sót xe nhỏ/xa khi confidence threshold cao.
- Biển số quá nhỏ/mờ/góc xiên làm detector/OCR không đủ confidence.

## 15. Hướng Phát Triển

- Thêm auth, role và audit log.
- Thêm camera health endpoint và UI trạng thái kết nối.
- Chia sẻ model/worker pool hoặc GPU scheduler cho nhiều camera.
- Lưu `evidence_summary` vào DB để giải thích quyết định sau này.
- Thêm violation detail endpoint theo ID.
- Thêm DB-level dedup nếu cần chống trùng sau restart.
- Thêm retention/archive policy cho evidence images.
- Thêm migration tool có version thay vì schema patch thủ công.
- Thêm replay/trajectory history từ backend vào Monitoring.
- Tối ưu OCR theo batch lớn hơn hoặc queue ưu tiên khi mật độ xe cao.
- Chuẩn hóa profile OCR riêng cho biển số Việt Nam nếu dùng production.

## 16. Nội Dung Tóm Tắt Đưa Vào Báo Cáo

Hệ thống Traffic Warning kết hợp YOLOv8, ByteTrack và luật hình học lane/maneuver để giám sát giao thông thời gian thực. Backend FastAPI xử lý video, tracking, gán làn, phát hiện vi phạm, OCR biển số, lưu SQLite và phát dữ liệu realtime qua WebSocket. Frontend React cung cấp màn hình giám sát, thống kê và công cụ cấu hình lane/maneuver bằng canvas.

Điểm kỹ thuật chính là hệ thống không dùng AI để nhận diện làn; lane được cấu hình thủ công bằng polygon và được xử lý bằng Shapely. Vi phạm được phát hiện bằng state machine và evidence fusion: sai làn dựa trên stable lane transition, ngược chiều dựa trên vector trajectory và direction path, rẽ sai dựa trên turn/exit zone, line crossing, heading, curvature và temporal continuity. OCR biển số là nhánh bổ sung, có detector riêng và temporal voting để tránh chốt kết quả từ một frame đơn lẻ.

Thiết kế hiện phù hợp đồ án/demo kỹ thuật, có khả năng giải thích quyết định và chỉnh cấu hình trực quan. Khi triển khai production, cần bổ sung auth, audit log, health monitoring, retention policy, tối ưu đa camera/GPU và lưu evidence summary vào DB.
