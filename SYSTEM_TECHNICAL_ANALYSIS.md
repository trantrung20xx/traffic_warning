# SYSTEM_TECHNICAL_ANALYSIS

Ngày cập nhật: 2026-05-21
Phạm vi: toàn bộ hệ thống `traffic_warning` hiện tại (software + hardware), đồng bộ theo source code đang có trong repository.

Tài liệu này là **nguồn ngữ cảnh kỹ thuật duy nhất** cho hệ thống, thay thế các báo cáo rời trước đây.

---

## 1. Mục tiêu và nguyên tắc hệ thống

### 1.1 Mục tiêu nghiệp vụ
- Phát hiện vi phạm giao thông theo camera realtime.
- Ghi nhận bằng chứng vi phạm (ảnh tổng quan xe vi phạm).
- Đọc và enrich biển số (khi có đủ bằng chứng) mà không làm chậm phát hiện vi phạm.
- Đồng bộ dữ liệu realtime lên UI qua WebSocket.

### 1.2 Nguyên tắc cốt lõi đang triển khai
- **Violation first**: vi phạm được emit/lưu ngay khi logic vi phạm xác nhận.
- **Plate later**: OCR biển số chạy nền, enrich sau.
- **Safety first cho plate**: không gán nhầm biển số giữa xe/track/session.
- **Realtime non-blocking**: không OCR đồng bộ trong hot path `_handle_violations()`.
- **Evidence upgrade độc lập**: nâng cấp ảnh evidence có thể chạy độc lập với plate confirm.

---

## 2. Ảnh chụp hiện trạng cấu hình (runtime snapshot)

### 2.1 Camera cấu hình hiện tại (`config/cameras.json`)
- `cam_01`: `rtsp_url=""` (chưa có nguồn runtime).
- `cam_02`: RTSP qua edge node (`rtsp://172.20.10.2:8593/cam_2ccf6788f9e5`).
- `cam_03`: đang trỏ video local file `.mp4` (dùng cho test/demo luồng backend).

### 2.2 Backend settings chính (`config/settings.json`)
- Detector: `yolov8s.pt`, `device=auto`, `conf=0.25`.
- License plate:
  - `enabled=true`
  - `ocr_backend=paddleocr`
  - `read_interval_ms=250`
  - `min_ocr_confidence=0.55`
  - `consensus_min_hits=2`
  - `candidate_window_ms=5000`
  - `violation_update_enabled=true`
  - `violation_update_min_confidence=0.55`
  - `violation_update_consensus_min_hits=2`
  - `violation_update_window_ms=10000`
  - `require_clean_track_for_violation_update=true`
  - `prioritize_pending_violation_ocr=true`
- WebSocket: `track_push_interval_ms=150`.
- Evidence crop vi phạm:
  - `expand_x_ratio=0.28`
  - `expand_y_top_ratio=0.32`
  - `expand_y_bottom_ratio=0.27`
  - `evidence jpeg quality=92`

---

## 3. Kiến trúc tổng thể

```text
Edge Camera Node (Raspberry Pi 5)
  -> RTSP stream (MediaMTX)
  -> Backend FastAPI (OpenCV + YOLO + ByteTrack + rule engine + OCR worker)
  -> SQLite + Evidence images
  -> REST API + WebSocket
  -> Frontend React (Monitoring / Analytics / Management)
```

### 3.1 Phân lớp trách nhiệm
- `edge_camera_node`: phần cứng, stream pipeline, watchdog cục bộ, health API, TFT/GPIO.
- `backend`: AI + tracking + lane/violation logic + OCR + DB + realtime API.
- `frontend`: hiển thị, điều khiển cấu hình, realtime UX, lịch sử/thống kê.
- `config`: nguồn sự thật cho settings, cameras, lane geometry, evidence, SQLite.

---

## 4. Cấu trúc source theo module

### 4.1 Backend
- `backend/app/server.py`: tạo FastAPI app, startup/shutdown lifecycle.
- `backend/app/api/routes.py`: REST endpoints.
- `backend/app/api/ws.py`: WebSocket `/ws/tracks`, `/ws/violations`.
- `backend/app/managers/camera_manager.py`: quản lý nhiều camera context, listeners, DB query adapter.
- `backend/app/managers/camera_context.py`: pipeline xử lý theo từng camera.
- `backend/app/vision/*`: detector xe, detector biển số, OCR backend.
- `backend/app/tracking/tracker.py`: YOLO track + ByteTrack.
- `backend/app/logic/*`: lane logic, direction logic, violation logic, plate resolver.
- `backend/app/db/*`: model, schema patch, repository query/update.
- `backend/app/rtsp/rtsp_stream.py`: reader frame từ RTSP/file.

### 4.2 Frontend
- `frontend/src/views/MonitoringView.jsx`: giám sát realtime.
- `frontend/src/views/AnalyticsView.jsx`: thống kê, lịch sử, export.
- `frontend/src/views/ManagementView.jsx`: cấu hình camera/lane/maneuver.
- `frontend/src/components/ViolationDetailModal.jsx`: modal chi tiết vi phạm.
- `frontend/src/api.js`: toàn bộ REST/WS client.
- `frontend/src/violationDetails.js`: sanitize và section builder cho detail.

### 4.3 Edge camera node
- `edge_camera_node/src/traffic_camera_node/main.py`: app entrypoint.
- `.../stream/rtsp_pipeline.py`: pipeline camera -> MediaMTX.
- `.../stream/process_supervisor.py`: watchdog cấp ứng dụng.
- `.../stream/fps_probe.py`: đo FPS RTSP loopback.
- `.../health_api.py`: health/control API.
- `.../hardware/buttons.py`: GPIO buttons.
- `.../hardware/leds.py`: GPIO LEDs.
- `.../hardware/tft_display.py`: ILI9341 renderer + console fallback.
- `.../state.py`: trạng thái node và health snapshot.

---

## 5. Luồng dữ liệu backend end-to-end

### 5.1 Startup
1. `app.server.create_app()` khởi tạo `CameraManager`.
2. Startup event:
   - bật `EdgeDiscoveryService`
   - chạy `edge_discovery.rescan()`
   - `manager.start()` dựng context theo từng camera.

### 5.2 Pipeline mỗi frame trong `CameraContext.run_forever()`
1. `RtspFrameReader.read(only_new=True)` lấy frame mới nhất.
2. Gửi frame sang worker preview JPEG snapshot (dùng cho fallback MJPEG); frontend ưu tiên transport realtime qua `/api/cameras/{camera_id}/stream-endpoints` (`WebRTC -> HLS -> fallback MJPEG`).
3. Detect + track:
   - YOLO + ByteTrack (`tracker.track`)
   - stable ID assignment.
4. Lane assignment:
   - `LaneLogic.observe_lane_from_bbox_xyxy`
   - `TemporalLaneAssigner.resolve_lane`.
5. Violation engine:
   - `violation_logic.update_and_maybe_generate_violation`.
6. License plate flow song song:
   - snapshot hiện tại từ resolver
   - enqueue OCR jobs (throttle theo `read_interval_ms`).
7. Push WebSocket tracks theo nhịp `track_push_interval_ms`.
8. Nếu có candidate vi phạm -> `_handle_violations(...)`.
9. Prune runtime state định kỳ.

### 5.3 `_handle_violations()` (không delay)
Cho mỗi candidate:
1. Tạo evidence ảnh vi phạm tổng quan (`_create_violation_evidence`).
2. Tạo plate crop evidence độc lập (`_create_license_plate_evidence`).
3. Lấy `plate_snapshot` hiện tại.
4. Nếu snapshot `confirmed` nhưng chưa có ảnh crop biển số:
   - giữ `license_plate=null`, `status=pending`.
5. Tạo `ViolationEvent` và `insert_violation` vào DB.
6. Đăng ký pending state cho late enrichment.
7. Emit realtime violation ngay (`self.on_violation(event)`).

Kết luận: OCR không chặn phát hiện vi phạm.

---

## 6. OCR biển số: kiến trúc và quy tắc

### 6.1 Detector biển số
`backend/app/vision/license_plate_detector.py`
- Dùng YOLOv8 trên **vehicle crop**, không OCR toàn frame.
- Hỗ trợ batch detect để giảm overhead.
- Lọc class qua `detector_allowed_classes`.

### 6.2 OCR engine
`backend/app/vision/license_plate_ocr.py`
- Hỗ trợ `paddleocr` và `easyocr`.
- `read_best(aggressive=False|True)`.
- Khi `aggressive=True`, thử thêm nhiều biến thể ảnh:
  - upscale
  - padding
  - CLAHE
  - sharpen
  - adaptive threshold
  - Otsu
- Chọn candidate confidence cao nhất.

### 6.3 Temporal resolver
`backend/app/logic/license_plate_logic.py`
- State theo `vehicle_id`:
  - `pending`, `confirmed`, `uncertain`, `unreadable`.
- Voting theo cửa sổ `candidate_window_ms`.
- Confirm khi:
  - hits >= `consensus_min_hits`
  - avg confidence >= `min_ocr_confidence`
  - không có đối thủ consensus quá sát.
- Nếu 2 phương án cạnh tranh sát nhau -> `uncertain`.

### 6.4 Nguyên tắc hiển thị plate evidence
- Cả backend và frontend đều enforce:
  - **Không có `license_plate_image_path` -> không coi plate là bằng chứng hiển thị hợp lệ**.
- Cụ thể:
  - `repository._normalize_plate_fields_for_payload(...)`
  - `frontend/src/violationDetails.js::sanitizeViolationPlateForDisplay(...)`.

---

## 7. Late plate enrichment (violation đã có, plate cập nhật sau)

### 7.1 Khóa liên kết an toàn
Update theo bộ khóa:
- `camera_id`
- `track_session_id`
- `vehicle_id`

Không update theo `vehicle_id` đơn lẻ.

### 7.2 Hàm thực hiện
- `CameraContext._attempt_late_plate_enrichment(...)`
- Repository: `update_pending_violation_plate(...)`

### 7.3 Điều kiện update plate hiện tại
- `violation_update_enabled=true`
- track continuity sạch (`_is_late_plate_update_track_clean`)
- có candidate plate + confidence >= `violation_update_min_confidence`
- phải có `license_plate_image_path` (ảnh crop hợp lệ)
- row DB thuộc status cho phép (`pending`, `unreadable`, `uncertain`) hoặc backfill hợp lệ
- trong `violation_update_window_ms`
- không overwrite `confirmed` khác text

### 7.4 Trạng thái update
- Có thể lưu `license_plate_status="confirmed"` hoặc `"uncertain"` tùy nguồn bằng chứng.
- Cho phép chi tiết vi phạm có `text + plate crop` nhưng vẫn giữ trạng thái chưa chắc chắn khi cần.

---

## 8. Evidence image upgrade độc lập plate

### 8.1 Mục tiêu
- Nâng cấp `evidence_image_path` (ảnh tổng quan vi phạm) nếu tìm được ảnh tốt hơn.
- Không phụ thuộc plate confirm.

### 8.2 Hàm thực hiện
- `CameraContext._attempt_evidence_upgrade(...)`
- Repository: `update_violation_evidence_image_if_better(...)`

### 8.3 Gate chính
- vehicle đang thuộc pending violation scope
- track continuity sạch
- candidate quality > baseline + margin (`evidence_quality_delta_min=0.45`)
- update DB thành công mới emit realtime update

### 8.4 Scoring
- Base quality xe (`_vehicle_evidence_quality_score`)
- Bonus plate-aware khi có plate crop/text/confidence
- Tránh thay ảnh nếu cải thiện không đủ rõ.

---

## 9. Database và repository

### 9.1 Schema
`backend/app/db/models.py` bảng `violations`:
- khóa chính: `id`
- scope chính:
  - `camera_id`
  - `vehicle_id`
  - `track_session_id`
  - `timestamp_utc`
- evidence:
  - `evidence_image_path`
  - `license_plate_image_path`
- plate fields:
  - `license_plate`
  - `license_plate_status`
  - `license_plate_confidence`

### 9.2 Schema patch runtime
`backend/app/db/database.py::_ensure_violation_schema`
- tự add cột/index nếu thiếu.
- tạo index phục vụ lookup scope:
  - `(camera_id, track_session_id, vehicle_id, timestamp_utc)`
  - `(camera_id, track_session_id, vehicle_id)`.

### 9.3 Repository quan trọng
- `insert_violation(...)`
- `update_pending_violation_plate(...)`
- `update_violation_evidence_image_if_better(...)`
- `query_violation_payloads_by_ids(...)`
- `query_violation_history(...)`, `query_violation_detail_by_id(...)`, `query_dashboard_analytics(...)`.

---

## 10. Realtime contract backend -> frontend

### 10.1 Tracks WebSocket
- Endpoint: `WS /ws/tracks?camera_id=...`
- Payload: `TrackMessage`
  - `type="track"`
  - `camera_id`
  - `timestamp`
  - `processing_fps`
  - `stream_fps`
  - `vehicles[]` gồm bbox, lane, plate snapshot...

### 10.2 Violations WebSocket
- Endpoint: `WS /ws/violations?camera_id=...`
- Payload envelope:
```json
{
  "type": "violation",
  "event": { "...ViolationEvent..." }
}
```

### 10.3 Upsert realtime trên frontend
- `MonitoringView` dùng `upsertViolationRows(...)` theo `violationRowKey` (ưu tiên `id`).
- `AnalyticsView` dùng `upsertHistoryRows(...)` tương tự.
- Nếu modal đang mở đúng record, `selectedViolation` được merge theo event mới.

Kết quả: không cần refresh trang để thấy plate/evidence update mới.

---

## 11. Frontend chi tiết theo màn hình

### 11.1 Monitoring
`frontend/src/views/MonitoringView.jsx`
- Dùng 2 WS song song: tracks + violations.
- Hiển thị video realtime (ưu tiên WebRTC, fallback HLS, fallback MJPEG) + overlay bbox/lane/quỹ đạo.
- Vi phạm list realtime có upsert, không append trùng.
- `ViolationDetailModal` mở theo record đang chọn.

### 11.2 Analytics
`frontend/src/views/AnalyticsView.jsx`
- Lấy dashboard + history theo filter.
- Nhận WS violations và upsert lịch sử ngay.
- Có schedule refresh ngắn để đồng bộ chart/summary.
- Export CSV/XLSX.

### 11.3 Violation detail modal
`frontend/src/components/ViolationDetailModal.jsx`
- Load detail theo `violation_id`.
- Polling detail mỗi 1s làm fallback chống stale.
- Render:
  - evidence image tổng quan
  - plate crop image (nếu có)
  - metadata đầy đủ.

### 11.4 Plate sanitize hiển thị
`frontend/src/violationDetails.js`
- Nếu không có plate image URL/path:
  - reset plate text/confidence cho hiển thị
  - `confirmed` bị hạ hiển thị thành `pending`.

---

## 12. Edge camera node: kiến trúc phần cứng/phần mềm

### 12.1 Vai trò
- Cấp stream RTSP ổn định cho backend.
- Watchdog tự hồi phục pipeline stream.
- Cung cấp health/control API cho frontend.
- Giao tiếp trực quan với người vận hành qua TFT + LED + nút.

### 12.2 Runtime identity
`edge_camera_node/src/traffic_camera_node/identity.py`
- Sinh từ MAC + machine-id.
- Persist vào `config/runtime_identity.json`.
- Duy trì:
  - `camera_id`
  - `mdns_hostname`
  - `rtsp_port`
  - `stream_path`
  - `fallback_ip`.

### 12.3 RTSP pipeline
`stream/rtsp_pipeline.py`
- Source mode:
  - `rpi_csi` (rpicam/libcamera)
  - `usb_v4l2` (ffmpeg v4l2)
  - `auto` (ưu tiên CSI, fallback USB).
- Pipeline mode:
  - `libav_mpegts`
  - `h264`
  - `auto` (thử fallback khi mode đầu fail).
- Thành phần:
  - MediaMTX
  - source process
  - ffmpeg publisher (với CSI mode).

### 12.4 Watchdog ứng dụng
`stream/process_supervisor.py`
- Theo dõi:
  - tiến trình pipeline còn chạy không (`pipeline.health()`)
  - FPS RTSP loopback local (`FpsProbe`).
- `FpsProbe`:
  - probe `rtsp://127.0.0.1:<port>/<path>`
  - dùng `ffprobe -count_packets` trong ~1.5s
  - probe mỗi 5s.
- Chính sách restart:
  - pipeline chết -> restart có giới hạn.
  - fps <= 0.1 kéo dài ~6s -> restart.
  - nếu restart quá số lần trong cửa sổ:
    - latch watchdog
    - dừng pipeline
    - yêu cầu `RESET_WATCHDOG`.

### 12.5 Health API edge
`health_api.py`
- GET:
  - `/health` và `/api/health`
  - `/api/identity`
- POST:
  - `/api/stream/start`
  - `/api/stream/stop`
  - `/api/stream/restart`
  - `/api/image-tuning/cycle`
- Port cố định: `8088`.

### 12.6 GPIO buttons (4 nút)
Theo `hardware/buttons.py` và `main.py`:
- `MODE` (`GPIO5`): đổi trang TFT (`next_screen`).
- `RESTART_STREAM` (`GPIO6`): yêu cầu restart stream supervisor.
- `SAFE_SHUTDOWN` (`GPIO13`, hold 3s): shutdown an toàn hệ điều hành.
- `RESET_WATCHDOG` (`GPIO19`): clear watchdog latch và restart lại pipeline.

### 12.7 GPIO LEDs (4 đèn)
Theo `hardware/leds.py`:
- `ONLINE` (`GPIO17`):
  - booting: blink chậm (0.2 on / 0.8 off)
  - runtime: bật sáng khi node hoạt động.
- `STREAMING` (`GPIO23`):
  - sáng khi stream đang chạy.
- `WARNING` (`GPIO27`):
  - blink 0.5/0.5 khi `NodeStatus.WARNING`.
- `ERROR` (`GPIO22`):
  - blink nhanh 0.2/0.2 khi `NodeStatus.ERROR`.
- `SHUTTING_DOWN`:
  - tắt toàn bộ LED.

### 12.8 TFT display
`hardware/tft_display.py`
- Hỗ trợ ILI9341 SPI, fallback console renderer nếu không có hardware libs.
- 4 màn hình:
  1. `NET + RTSP`
  2. `HARDWARE`
  3. `CAMERA`
  4. `DIAGNOSTICS`
- Nội dung gồm: camera id, mDNS, RTSP URL, IP fallback, fps, temp, watchdog, restart count...

---

## 13. API backend chính

### 13.1 REST
- `GET /api/health`
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}`
- `GET /api/cameras/{camera_id}/stream-endpoints`
- `POST /api/cameras`
- `PUT /api/cameras/{camera_id}`
- `DELETE /api/cameras/{camera_id}`
- `GET /api/cameras/{camera_id}/preview`
- `GET /api/cameras/{camera_id}/lanes` (qua camera detail/lane config)
- `GET /api/violations/history`
- `GET /api/violations/detail/{violation_id}`
- `GET /api/violations/evidence/{path}`
- `GET /api/violations/export?format=csv|xlsx`
- `GET /api/analytics/dashboard`
- `GET /api/stats`
- Nhóm edge discovery/proxy:
  - `GET /api/edge-cameras`
  - `POST /api/edge-cameras/rescan`
  - `GET /api/edge-cameras/{camera_id}`
  - `POST /api/edge-cameras/{camera_id}/stream/start|stop|restart`
  - `POST /api/edge-cameras/{camera_id}/image-tuning/cycle`

### 13.2 WS
- `WS /ws/tracks`
- `WS /ws/violations`

---

## 14. Cơ chế chống nghẽn và ổn định realtime

### 14.1 Backend
- `RtspFrameReader.read(only_new=True)` tránh backlog frame.
- Preview encode tách worker, có throttle theo fps.
- WebSocket listeners dùng queue có `maxsize`; queue full sẽ drop listener chậm.
- OCR queue coalesce theo `vehicle_id`, ưu tiên pending violation.
- DB update enrichment chỉ chạy khi có thay đổi thực sự.

### 14.2 Frontend
- Upsert theo `id` giảm duplicate/stale.
- Polling modal chỉ là fallback.
- API helper có timeout/abort cho request edge/REST.

### 14.3 Edge
- Supervisor thread tách riêng với main loop.
- Restart window tránh vòng lặp restart vô hạn.
- Mỗi action start/stop/restart đều đi qua cờ điều khiển thread-safe.

---

## 15. Bất biến nghiệp vụ quan trọng (để AI/code maintainer không phá)

1. Không delay emit vi phạm để chờ OCR.
2. Không OCR toàn frame trong hot path.
3. Không xác nhận plate khi không có plate crop evidence.
4. Không overwrite biển số `confirmed` khác text.
5. Enrichment phải bám khóa `camera_id + track_session_id + vehicle_id`.
6. Evidence upgrade không được degrade ảnh cũ bằng ảnh kém hơn.
7. Realtime violation event phải giữ tương thích payload cũ (`image_*`) và alias evidence (`evidence_image_*`).
8. Frontend phải upsert theo `id` thay vì append vô điều kiện.

---

## 16. Điểm cần lưu ý khi dùng tài liệu này cho Gemini

### 16.1 Mô hình đúng của hệ thống
- Đây là hệ thống **rule-based violation + AI detection/tracking + OCR enrichment**.
- OCR là nhánh bổ sung bằng chứng, không phải điều kiện tiên quyết để tạo violation.
- Plate text chỉ có giá trị hiển thị/chứng cứ khi đi kèm ảnh crop biển số.

### 16.2 Nếu yêu cầu thay đổi code
Gemini nên giữ các guard:
- không sửa YOLO/ByteTrack core nếu không bắt buộc.
- không block `_handle_violations()`.
- không làm tăng backlog OCR vô hạn.
- không phá hợp đồng payload WS/REST hiện có.

---

## 17. Gợi ý kiểm tra sau mỗi thay đổi lớn

### 17.1 Backend
```powershell
python -m compileall backend/app
cd backend
python -m pytest tests -q
```

### 17.2 Frontend
```powershell
cd frontend
npm run build
```

### 17.3 Edge node (khi có thay đổi edge)
```bash
cd edge_camera_node
pytest -q
```

---

## 18. Kết luận

Hệ thống hiện tại đã có đầy đủ:
- pipeline phát hiện vi phạm realtime độc lập OCR,
- cơ chế late plate enrichment an toàn,
- cơ chế evidence upgrade độc lập,
- đồng bộ update DB -> WS -> UI theo `id`,
- watchdog phần cứng/phần mềm cho edge node.

Đây là baseline kỹ thuật phù hợp để viết báo cáo đồ án và làm ngữ cảnh chuẩn cho AI hỗ trợ phát triển tiếp.
