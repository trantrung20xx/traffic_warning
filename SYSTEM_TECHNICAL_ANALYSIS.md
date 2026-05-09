# Tài liệu phân tích kỹ thuật hệ thống Traffic Warning

Ngày rà soát: 2026-05-09

Phạm vi: tài liệu này đồng bộ theo source hiện tại trong `backend`, `frontend`, `config` và `edge_camera_node`. Nội dung được viết để phục vụ báo cáo đồ án tốt nghiệp và để các công cụ hỗ trợ như Gemini/ChatGPT nắm đầy đủ ngữ cảnh, kiến trúc, phần cứng, luồng dữ liệu, trạng thái triển khai và các giới hạn kỹ thuật của hệ thống.

## 1. Tổng Quan Hệ Thống

Traffic Warning là hệ thống giám sát giao thông theo kiến trúc tách lớp:

- `edge_camera_node` chạy trên Raspberry Pi 5, nhận hình từ camera 2K, mã hóa H.264 và phát RTSP ổn định.
- `backend` FastAPI chạy xử lý chính: đọc video bằng OpenCV, YOLOv8, ByteTrack, gán làn, phát hiện vi phạm, OCR biển số, lưu SQLite và phát realtime.
- `frontend` React hiển thị giám sát, thống kê, cấu hình camera/làn và popup trạng thái edge node.
- `config` lưu cấu hình camera, lane, settings, ảnh nền, ảnh bằng chứng và database SQLite.

Luồng tổng thể:

```text
Camera 2K trên Raspberry Pi 5
  -> edge_camera_node phát RTSP ổn định
  -> Backend đọc RTSP bằng OpenCV
  -> YOLOv8 + ByteTrack
  -> stable track ID
  -> lane assignment bằng polygon
  -> rule-based violation logic
  -> OCR biển số nếu bật
  -> SQLite + evidence image
  -> REST API / WebSocket / MJPEG preview
  -> React frontend
```

Điểm phân tách trách nhiệm quan trọng:

- Edge node không chạy AI, không OCR, không tracking, không quyết định vi phạm và không ghi database chính.
- Backend là nơi duy nhất xử lý AI, logic vi phạm, evidence, REST API, WebSocket và SQLite.
- Frontend chỉ gọi backend cho nghiệp vụ chính; riêng popup edge node gọi trực tiếp Health API của Raspberry Pi để xem trạng thái, bật/tắt stream và restart service.

## 2. Cấu Trúc Dự Án

| Module           | File/thư mục                             | Vai trò                                                                                                              |
| ---------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Edge camera node | `edge_camera_node`                       | Chương trình phần cứng trên Raspberry Pi 5, phát RTSP, hiển thị TFT, xử lý nút/LED, watchdog, health API và systemd. |
| Server           | `backend/app/server.py`                  | Tạo FastAPI app, CORS, lifespan startup/shutdown, đăng ký REST/WS router.                                            |
| Camera manager   | `backend/app/managers/camera_manager.py` | Load config, quản lý nhiều `CameraContext`, reload runtime, query DB, listener realtime.                             |
| Camera context   | `backend/app/managers/camera_context.py` | Pipeline xử lý từng camera: đọc frame, tracking, OCR worker, violation, evidence, DB, realtime callbacks.            |
| Video input      | `backend/app/rtsp/rtsp_stream.py`        | Thread đọc RTSP/HTTP/file local bằng OpenCV, resize frame, pacing file local.                                        |
| Vision           | `backend/app/vision`                     | YOLO detector phương tiện, detector biển số, OCR Paddle/EasyOCR, resolver device/backend.                            |
| Tracking         | `backend/app/tracking/tracker.py`        | Gọi `YOLO.model.track(..., persist=True)` với ByteTrack.                                                             |
| Logic            | `backend/app/logic`                      | Lane assignment, direction detection, violation engine, OCR temporal voting, stable track ID, geometry validator.    |
| DB               | `backend/app/db`                         | SQLAlchemy model, SQLite engine/session, repository insert/query/dashboard.                                          |
| API              | `backend/app/api`                        | REST API, MJPEG preview, WebSocket tracks/violations.                                                                |
| Frontend         | `frontend/src`                           | SPA React cho giám sát, thống kê, quản lý camera/lane/maneuver và popup edge node.                                   |
| Config           | `config`                                 | Cấu hình server, camera, lane, ảnh nền, evidence và SQLite.                                                          |

## 3. Danh Sách Linh Kiện Phần Cứng

| Tên linh kiện                     | Số lượng | Chức năng với hệ thống                                                                                       |
| --------------------------------- | -------: | ------------------------------------------------------------------------------------------------------------ |
| Raspberry Pi 5                    |        1 | Máy tính biên chạy `edge_camera_node`, quản lý camera, RTSP pipeline, TFT, nút nhấn, LED và systemd service. |
| Camera 2K                         |        1 | Cung cấp hình ảnh đầu vào cho edge node, mặc định phát ở 2560x1440 và 30 FPS theo `settings.json`.           |
| Adapter USB-C 5V 5A               |        1 | Cấp nguồn chính cho Raspberry Pi 5 và các module nhỏ lấy nguồn từ Pi.                                        |
| Tản nhiệt Raspberry Pi 5          |        1 | Giữ nhiệt độ ổn định để stream chạy lâu dài, giảm rủi ro throttling khi encode video.                        |
| Thẻ nhớ microSD                   |        1 | Lưu Raspberry Pi OS, source `edge_camera_node`, virtualenv, config, log và runtime identity.                 |
| Hộp tủ điện                       |        1 | Bảo vệ Pi, camera node và dây nối trong môi trường triển khai thực địa.                                      |
| TFT IPS SPI 2.4 inch ILI9341      |        1 | Hiển thị camera ID, mDNS URL, IP fallback, trạng thái stream, FPS, nhiệt độ, lỗi và watchdog.                |
| Nút nhấn momentary MODE           |        1 | Đổi màn hình trạng thái trên TFT.                                                                            |
| Nút nhấn momentary RESTART_STREAM |        1 | Gửi lệnh restart pipeline RTSP cục bộ trên edge node.                                                        |
| Nút nhấn momentary SAFE_SHUTDOWN  |        1 | Nhấn giữ để shutdown Raspberry Pi an toàn.                                                                   |
| Nút nhấn momentary RESET_WATCHDOG |        1 | Xóa trạng thái lỗi/watchdog latched và thử khởi động lại pipeline.                                           |
| LED ONLINE                        |        1 | Báo service edge node đang hoạt động bình thường.                                                            |
| LED WARNING                       |        1 | Báo trạng thái cảnh báo như FPS thấp, mDNS lỗi hoặc lỗi chưa nghiêm trọng.                                   |
| LED ERROR                         |        1 | Báo lỗi nghiêm trọng hoặc watchdog đã chốt lỗi.                                                              |
| LED STREAMING                     |        1 | Báo RTSP pipeline đang chạy.                                                                                 |
| Điện trở hạn dòng LED             |        4 | Hạn dòng cho từng LED để bảo vệ GPIO và LED.                                                                 |

Ghi chú: RTC DS3231 không còn nằm trong thiết kế source hiện tại. Thời gian vi phạm do backend server ghi nhận, còn edge node chỉ cần thời gian đủ ổn định cho log cục bộ.

## 4. Công Nghệ Sử Dụng

Backend dependency chính:

| Công nghệ                   | Vai trò                                                                       |
| --------------------------- | ----------------------------------------------------------------------------- |
| FastAPI, Starlette, Uvicorn | REST API, WebSocket, ASGI runtime.                                            |
| OpenCV                      | Đọc video, resize frame, encode JPEG preview/evidence.                        |
| NumPy                       | Frame/image array.                                                            |
| Ultralytics YOLOv8          | Detection/tracking phương tiện và detector biển số.                           |
| ByteTrack                   | Object tracking qua Ultralytics `model.track`.                                |
| PyTorch/Torchvision         | Inference `.pt` mặc định.                                                     |
| Shapely                     | Polygon/LineString, point-in-polygon, line intersection, geometry validation. |
| Pydantic                    | Schema config/API/event.                                                      |
| SQLAlchemy                  | ORM SQLite.                                                                   |
| PaddleOCR, EasyOCR          | OCR biển số.                                                                  |
| openpyxl                    | Export XLSX.                                                                  |
| pytest                      | Test backend.                                                                 |

Frontend dependency chính:

| Công nghệ    | Vai trò                                                        |
| ------------ | -------------------------------------------------------------- |
| React 18     | UI SPA.                                                        |
| Vite         | Dev server/build.                                              |
| lucide-react | Icon UI.                                                       |
| Canvas 2D    | Vẽ preview overlay, lane, bbox, trajectory và editor geometry. |

Edge dependency chính:

| Công nghệ                | Vai trò                                                                                              |
| ------------------------ | ---------------------------------------------------------------------------------------------------- |
| Raspberry Pi OS          | Môi trường chạy trên Raspberry Pi 5.                                                                 |
| rpicam-vid/libcamera-vid | Đọc camera Pi và encode H.264 bằng camera stack chính thống của Raspberry Pi.                        |
| MediaMTX                 | RTSP server cục bộ, cung cấp URL RTSP cố định cho backend.                                           |
| ffmpeg                   | Đọc MPEG-TS qua UDP nội bộ và publish vào MediaMTX bằng RTSP, dùng `-c:v copy` nên không encode lại. |
| Avahi/mDNS               | Publish hostname ổn định dạng `cam-<mac>.local`.                                                     |
| psutil                   | Đọc network interface, MAC, IP, CPU/RAM/disk và process/network status.                              |
| gpiozero/lgpio           | Đọc nút nhấn và điều khiển LED trên Raspberry Pi 5.                                                  |
| Pillow/spidev            | Render status lên TFT ILI9341 qua SPI.                                                               |
| systemd                  | Tự chạy edge node khi boot và restart process khi service thoát.                                     |

## 5. Edge Camera Node Trên Raspberry Pi 5

### Vai trò

`edge_camera_node` nằm trong thư mục riêng và không phụ thuộc Windows. Mục tiêu là copy xuống Raspberry Pi 5, cài dependency, bật systemd và để node tự phát RTSP sau mỗi lần boot.

Edge node đảm nhiệm:

- Đọc camera 2K.
- Tuning ảnh nhẹ bằng tham số `rpicam-vid`.
- Encode H.264.
- Phát RTSP bằng MediaMTX.
- Tự sinh và giữ cố định `camera_id`, hostname mDNS, port và stream path.
- Hiển thị trạng thái trên TFT.
- Đọc nút nhấn và điều khiển LED.
- Watchdog restart pipeline khi tiến trình chết hoặc FPS thấp.
- Health API cục bộ để frontend popup đọc trạng thái và điều khiển stream.

Edge node không đảm nhiệm:

- Không chạy YOLO/ByteTrack/OCR.
- Không quyết định vi phạm.
- Không ghi SQLite chính.
- Không gửi WebSocket track/violation.
- Không yêu cầu backend đổi logic xử lý.

### Identity và URL ổn định

File identity được tạo ở:

```text
edge_camera_node/config/runtime_identity.json
```

Quy tắc:

- Ưu tiên MAC của `eth0`, nếu không có thì dùng `wlan0`, nếu vẫn thiếu thì dò interface không phải loopback.
- `camera_id = cam_<mac>`, ví dụ `cam_dca632112233`.
- `mdns_hostname = cam-<mac>.local`, ví dụ `cam-dca632112233.local`.
- `node_id` là hash ổn định từ `/etc/machine-id` và MAC.
- `rtsp_port` sinh ổn định trong dải 8554-8654 nếu không cấu hình port cố định.
- `stream_path = /<camera_id>`.
- Nếu identity đã tồn tại thì dùng lại, không sinh lại.

URL chính:

```text
rtsp://cam-dca632112233.local:8554/cam_dca632112233
```

URL fallback:

```text
rtsp://<fallback_ip>:8554/cam_dca632112233
```

mDNS là đường chính. IP fallback được ghi một lần để hạn chế đổi URL, nhưng khi triển khai thực tế vẫn nên đặt DHCP reservation hoặc static IP nếu server dùng IP.

### RTSP pipeline

Luồng edge:

```text
rpicam-vid/libcamera-vid
  -> H.264 MPEG-TS
  -> udp://127.0.0.1:1234?pkt_size=1316
  -> ffmpeg -c:v copy
  -> rtsp://127.0.0.1:<port>/<camera_id>
  -> MediaMTX
  -> rtsp://cam-<mac>.local:<port>/<camera_id>
```

Lý do thiết kế:

- `rpicam-vid` ổn định với camera stack của Raspberry Pi và hỗ trợ tuning ảnh.
- UDP `127.0.0.1` chỉ là điểm trung chuyển nội bộ trong Pi, không lộ ra LAN.
- `ffmpeg` chỉ copy video sang RTSP, không encode lại, nên giảm tải CPU.
- MediaMTX chịu trách nhiệm phục vụ RTSP URL cố định cho backend/OpenCV.
- Nếu port RTSP đã lưu bị process khác chiếm, edge node báo lỗi thay vì tự đổi port để tránh backend mất đồng bộ URL.

### Tuning ảnh

Profile trong `image_tuning.profile`:

| Profile          | Cách dùng                                     |
| ---------------- | --------------------------------------------- |
| `normal`         | Mặc định, giữ hình tự nhiên và ổn định.       |
| `low_light`      | Tăng nhẹ brightness/contrast cho cảnh tối.    |
| `bright_scene`   | Giảm nhẹ brightness để tránh cháy sáng.       |
| `sharpness_safe` | Tăng sharpness nhẹ để rõ biên xe/biển số hơn. |
| `disabled`       | Không thêm tham số tuning.                    |

Nguyên tắc hiện tại là không dùng xử lý ảnh nặng, không CLAHE mạnh, không super-resolution, không AI enhancement và không tự động đổi profile liên tục.

### GPIO, TFT và LED

Config GPIO nằm trong:

```text
edge_camera_node/config/settings.json
```

Mặc định:

- Buttons: MODE `GPIO5`, RESTART_STREAM `GPIO6`, SAFE_SHUTDOWN `GPIO13`, RESET_WATCHDOG `GPIO19`.
- LEDs: ONLINE `GPIO17`, WARNING `GPIO27`, ERROR `GPIO22`, STREAMING `GPIO23`.
- TFT ILI9341: SPI0, DC `GPIO25`, RST `GPIO24`, backlight mặc định không điều khiển.

TFT có nhiều màn hình: network/stream, hardware, camera, diagnostics. Nếu thiếu phần cứng, thiếu quyền GPIO/SPI hoặc chạy trên PC, code fallback console/mock để stream không chết vì lỗi phụ kiện.

### Health API của edge node

Health API chạy cố định ở port `8088`. Edge config loader sẽ từ chối `health_api.port` khác `8088` để frontend luôn gọi đúng API phần cứng khi suy host từ RTSP URL.

Endpoint:

| Endpoint               | Vai trò                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------ |
| `GET /health`          | Trả trạng thái edge node, RTSP URL, mDNS, stream, nhiệt độ, CPU/RAM, uptime, lỗi gần nhất. |
| `GET /identity`        | Trả identity cố định của node.                                                             |
| `GET /stream/start`    | Bật lại RTSP pipeline.                                                                     |
| `GET /stream/stop`     | Tắt RTSP pipeline.                                                                         |
| `GET /restart-service` | Yêu cầu chương trình edge node thoát sạch; systemd tự khởi động lại do `Restart=always`.   |

`/restart-stream` đã bị loại bỏ khỏi thiết kế hiện tại vì chức năng bật/tắt stream rõ nghĩa hơn.

Các endpoint điều khiển dùng `health_api.allow_restart_endpoint`. Nếu cấu hình thêm `health_api.token`, request điều khiển phải kèm `?token=...`.

### Watchdog và systemd

`ProcessSupervisor` theo dõi MediaMTX, rpicam/libcamera và ffmpeg:

- Nếu process chết thì restart pipeline có giới hạn.
- Nếu restart quá nhiều trong cửa sổ thời gian thì latch watchdog và chuyển ERROR.
- RESET_WATCHDOG xóa trạng thái latch và thử khởi động lại.
- SIGTERM/SIGINT được handle để dừng process con sạch, tránh zombie.

Systemd service:

```ini
Restart=always
RestartSec=5
```

Khi `/restart-service` được gọi, app set trạng thái `SHUTTING_DOWN`, thoát vòng lặp chính, dừng các thành phần, `main()` trả về `0`, `SystemExit(main())` kết thúc process. Vì service dùng `Restart=always`, systemd khởi động lại chương trình sau 5 giây.

### Cấu hình edge hiện tại

`edge_camera_node/config/settings.json` đang đặt:

- `camera.width`: 2560.
- `camera.height`: 1440.
- `camera.fps`: 30.
- `image_tuning.profile`: `normal`.
- `stream.bitrate`: 6000000.
- `stream.udp_sink`: `udp://127.0.0.1:1234?pkt_size=1316`.
- `watchdog.fps_warning_threshold`: 15.
- `health_api.port`: `8088`.
- `health_api.allow_restart_endpoint`: `true`.

## 6. Backend Lifecycle

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
6. Với từng track: làm mượt loại xe, gán lane, làm mượt lane, tạo `TrackVehicle`, gọi violation logic.
7. Push `TrackMessage` theo `websocket.track_push_interval_ms`.
8. Nếu có violation candidate, tạo evidence, lưu DB và push `ViolationEvent`.
9. Prune state định kỳ theo `performance.processing.prune_interval_ms` và `event_lifecycle.state_prune_max_age_s`.
10. Tính processing FPS bằng cửa sổ trượt.

Worker nền:

- Preview worker chỉ giữ frame mới nhất, không encode mọi frame.
- OCR worker dùng queue theo `vehicle_id`; mỗi xe chỉ giữ job mới nhất để tránh backlog cũ.
- OCR worker có batch size và max pending jobs để bảo vệ pipeline realtime.

## 7. AI, Tracking Và OCR

### Detector phương tiện

File: `backend/app/vision/detector.py`

- Load YOLO từ `detection.weights_path`.
- Resolve inference backend bằng `resolve_inference_backend`.
- Resolve device bằng `resolve_inference_device`.
- Lọc class theo `detection.allowed_classes`.
- Nếu load weight `.pt` thất bại, thử fallback model cùng thư mục: `yolov8x.pt`, `yolov8l.pt`, `yolov8m.pt`, `yolov8s.pt`, `yolov8n.pt`.

Backend inference được source hỗ trợ:

| Backend       | Điều kiện weight               |
| ------------- | ------------------------------ |
| `pytorch`     | Mặc định hoặc `.pt`.           |
| `tensorrt`    | `.engine`.                     |
| `openvino`    | `.xml` hoặc `_openvino_model`. |
| `onnxruntime` | `.onnx`.                       |

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

| Trạng thái   | Ý nghĩa                                         |
| ------------ | ----------------------------------------------- |
| `pending`    | Đang chờ đủ bằng chứng OCR.                     |
| `confirmed`  | Text đủ số hit và confidence.                   |
| `uncertain`  | Có nhiều candidate cạnh tranh.                  |
| `unreadable` | Quá số lần thử nhưng không có candidate hợp lệ. |

## 8. Lane Assignment Và Geometry

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

## 9. Violation Engine

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

## 10. Evidence, DB Và Export

### Evidence image

File: `backend/app/core/evidence_images.py`

- File evidence lưu dưới `config/evidence_images/<camera_id>/<dd-mm-yyyy>/...jpg`.
- Tên file chứa camera, timestamp UTC ms, vehicle, lane và violation.
- `resolve_evidence_image_path()` chặn path traversal bằng kiểm tra candidate nằm trong base dir.
- Ảnh bằng chứng tổng quan crop quanh bbox xe; nếu crop quá nhỏ thì fallback phù hợp theo logic trong `CameraContext`.
- Ảnh crop biển số được lưu riêng với suffix violation `_license_plate`.

### SQLite schema

Model `Violation` hiện tại:

| Cột                                               | Ý nghĩa                                 |
| ------------------------------------------------- | --------------------------------------- |
| `id`                                              | Primary key tự tăng.                    |
| `camera_id`                                       | Camera phát hiện.                       |
| `road_name`, `intersection`, `gps_lat`, `gps_lng` | Vị trí camera.                          |
| `vehicle_id`                                      | Stable vehicle ID trong phiên runtime.  |
| `vehicle_type`                                    | Loại xe đã làm mượt.                    |
| `lane_id`                                         | Lane liên quan.                         |
| `violation`                                       | Mã vi phạm.                             |
| `evidence_image_path`                             | Relative path ảnh bằng chứng tổng quan. |
| `license_plate`                                   | Biển số OCR nếu có.                     |
| `license_plate_status`                            | Trạng thái OCR.                         |
| `license_plate_confidence`                        | Confidence OCR.                         |
| `license_plate_image_path`                        | Relative path ảnh crop biển số nếu có.  |
| `track_session_id`                                | Phiên runtime của camera context.       |
| `timestamp_utc`                                   | Thời điểm UTC.                          |

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

## 11. API Và WebSocket

### REST API backend

| Endpoint                                          | Vai trò                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------ |
| `GET /api/health`                                 | Healthcheck backend.                                               |
| `GET /api/cameras`                                | Danh sách camera.                                                  |
| `GET /api/cameras/{camera_id}`                    | Camera detail, lane config, validation, runtime status, UI config. |
| `POST /api/cameras`                               | Tạo camera + lane config.                                          |
| `PUT /api/cameras/{camera_id}`                    | Cập nhật camera + lane config.                                     |
| `DELETE /api/cameras/{camera_id}`                 | Xóa camera và dữ liệu file liên quan.                              |
| `POST /api/camera/{camera_id}/background-image`   | Upload ảnh nền JPG/PNG.                                            |
| `GET /api/camera/{camera_id}/background-image`    | Lấy ảnh nền.                                                       |
| `DELETE /api/camera/{camera_id}/background-image` | Xóa ảnh nền.                                                       |
| `GET /api/cameras/{camera_id}/lanes`              | Lane polygons dạng pixel, kèm validation.                          |
| `GET /api/cameras/{camera_id}/trajectories`       | Trajectory runtime gần đây.                                        |
| `GET /api/cameras/{camera_id}/preview`            | MJPEG stream.                                                      |
| `GET /api/violations/evidence/{evidence_path}`    | Evidence image.                                                    |
| `GET /api/violations/history`                     | Lịch sử vi phạm.                                                   |
| `GET /api/violations/export`                      | Export CSV/XLSX.                                                   |
| `GET /api/analytics/dashboard`                    | Dashboard analytics.                                               |
| `GET /api/stats`                                  | Count thống kê theo filter.                                        |

### Health API edge node

| Endpoint               | Vai trò                                           |
| ---------------------- | ------------------------------------------------- |
| `GET /health`          | Health edge camera node.                          |
| `GET /identity`        | Identity cố định của edge node.                   |
| `GET /stream/start`    | Bật RTSP pipeline.                                |
| `GET /stream/stop`     | Tắt RTSP pipeline.                                |
| `GET /restart-service` | Restart chương trình edge node thông qua systemd. |

### WebSocket backend

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

## 12. Frontend

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
- Mở popup edge camera từ item camera để xem Health API, identity, RTSP URL, mDNS, IP fallback, bật/tắt stream và restart edge service.

Popup edge camera hoạt động bằng cách suy host từ `rtsp_url`. Ví dụ từ `rtsp://cam-dca632112233.local:8554/cam_dca632112233`, frontend gọi `http://cam-dca632112233.local:8088`.

### Canvas

File: `frontend/src/components/CameraCanvas.jsx`

- Denormalize geometry để vẽ theo pixel.
- Vẽ background image, lane/maneuver geometry, bbox, labels, trajectory, FPS.
- Hỗ trợ thêm điểm, kéo vertex, chèn điểm vào cạnh, kéo cả polygon/line và clamp trong frame.
- Khi save, payload gửi về backend ở tọa độ normalized `[0, 1]`.

## 13. Config Hiện Tại

Server `config/settings.json` có các điểm đáng chú ý:

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

Edge `edge_camera_node/config/settings.json` có các điểm đáng chú ý:

- Camera mặc định 2560x1440, 30 FPS.
- Bitrate RTSP mặc định 6 Mbps.
- Tuning ảnh mặc định `normal`.
- Health API cố định port `8088` và bật endpoint điều khiển.
- GPIO/TFT pin mapping có thể chỉnh bằng JSON.

Config geometry:

- Lane/maneuver/direction geometry được validate normalized `[0, 1]`.
- `allowed_maneuvers` có thể được tự suy ra từ `maneuvers.*.allowed` khi không khai báo.
- Nếu maneuver `enabled=false`, backend ép `allowed=false`.

## 14. Kiểm Thử Hiện Có

`backend/tests` bao phủ các vùng quan trọng:

- Config loading/normalization.
- Lane logic và temporal lane assignment.
- Vehicle type smoothing.
- RTSP shutdown.
- Direction/line crossing/turn related behavior.
- Timezone.
- API behavior ở một số phần.

`edge_camera_node/tests` bao phủ:

- Sinh camera ID ổn định từ MAC.
- Runtime identity không đổi sau restart.
- Port allocator ổn định.
- Config JSON load/validate.
- State transition BOOTING/ONLINE/WARNING/ERROR.
- URL builder/identity behavior không cần phần cứng thật.

Các lệnh kiểm thử:

```powershell
python -m compileall backend/app
cd backend
python -m pytest tests -q
cd ../edge_camera_node
pytest -q
cd ../frontend
npm run build
```

## 15. Điểm Mạnh

- Pipeline end-to-end rõ ràng từ camera thực địa đến cảnh báo realtime.
- Edge node tách riêng phần phần cứng, giúp backend chỉ cần đọc RTSP URL như camera thông thường.
- RTSP URL ổn định theo mDNS hostname sinh từ MAC, không phụ thuộc random/timestamp.
- Edge node có fallback IP, TFT, LED, nút nhấn, watchdog và systemd auto-start.
- Backend tách module tốt: vision, tracking, lane, direction, violation, API, DB, frontend.
- Lane assignment giải thích được bằng hình học, không phụ thuộc AI lane detection.
- Có smoothing nhiều lớp: stable track ID, vehicle type, lane ID.
- Direction detection có consensus, tail segment, displacement và candidate window.
- Turn detection dùng evidence fusion thay vì single signal.
- Lifecycle chống phát trùng trong runtime.
- OCR biển số tách worker nền, có temporal voting và degrade an toàn khi OCR lỗi.
- UI có công cụ cấu hình lane/maneuver trực quan, validator và popup health edge node.
- Có evidence image, dashboard, history và export CSV/XLSX.

## 16. Rủi Ro Và Hạn Chế

- Chưa có authentication/authorization/user role cho backend/frontend.
- Health API edge node có thể dùng token, nhưng frontend popup hiện chưa có UI nhập/lưu token.
- Edge node phụ thuộc rpicam/libcamera, ffmpeg, MediaMTX và Avahi trên Raspberry Pi OS; cần kiểm tra môi trường khi triển khai.
- Nếu mạng hoặc router chặn multicast/mDNS, `.local` có thể không resolve; phải dùng IP fallback hoặc cấu hình mạng.
- Nếu port RTSP đã lưu bị process lạ chiếm, node báo lỗi và không tự đổi port để giữ ổn định URL.
- Mỗi `CameraContext` tự load detector/tracker, nhiều camera có thể tốn RAM/VRAM.
- YOLO/ByteTrack vẫn là bottleneck chính khi nhiều camera hoặc model lớn.
- SQLite phù hợp demo/đồ án, chưa tối ưu cho nhiều writer hoặc triển khai nhiều instance.
- Dedup lifecycle nằm trong memory, restart backend sẽ mất trạng thái chống trùng.
- `evidence_summary` chưa được lưu trong DB schema hiện tại.
- Xóa camera sẽ xóa evidence image của camera, chưa có retention/archive policy.
- Geometry phụ thuộc người dùng vẽ đúng; validator cảnh báo nhưng không thể loại toàn bộ cấu hình rủi ro.
- Frontend trajectory live chủ yếu dựng từ WebSocket track; reload màn hình sẽ mất trajectory live đang có.

## 17. False Positive Và False Negative Tiềm Ẩn

False positive:

- Lane polygon overlap hoặc vẽ sai biên lane.
- BBox rung mạnh làm đáy bbox nhảy lane.
- Exit/commit line đặt quá gần vùng nhiễu.
- Turn zone/exit zone overlap giữa nhiều maneuver.
- OCR đọc sai nhưng đủ confidence trong vài lần liên tiếp.
- Edge camera rung, quá tối, quá sáng hoặc FPS thấp làm input kém ổn định.

False negative:

- Maneuver bị cấm nhưng thiếu `turn_zone`, `exit_line` hoặc `exit_zone`.
- Xe vi phạm không đi qua vùng/vạch đã vẽ.
- Track mất ID ngay trước/sau commit.
- Xe đi quá chậm hoặc đứng yên làm direction vector không đủ displacement.
- Model YOLO bỏ sót xe nhỏ/xa khi confidence threshold cao.
- Biển số quá nhỏ/mờ/góc xiên làm detector/OCR không đủ confidence.
- RTSP stream mất tạm thời khiến backend bỏ lỡ một đoạn hành vi.

## 18. Hướng Phát Triển

- Thêm auth, role và audit log.
- Thêm UI cấu hình token cho popup edge node nếu dùng Health API điều khiển có bảo vệ.
- Thêm camera health endpoint chi tiết phía backend để tổng hợp trạng thái stream/server/edge trên một màn hình.
- Chia sẻ model/worker pool hoặc GPU scheduler cho nhiều camera.
- Lưu `evidence_summary` vào DB để giải thích quyết định sau này.
- Thêm violation detail endpoint theo ID.
- Thêm DB-level dedup nếu cần chống trùng sau restart.
- Thêm retention/archive policy cho evidence images.
- Thêm migration tool có version thay vì schema patch thủ công.
- Thêm replay/trajectory history từ backend vào Monitoring.
- Tối ưu OCR theo batch lớn hơn hoặc queue ưu tiên khi mật độ xe cao.
- Chuẩn hóa profile OCR riêng cho biển số Việt Nam nếu dùng production.
- Thêm kiểm thử tích hợp dài hạn trên Raspberry Pi 5 để đo nhiệt độ, FPS, restart count và độ ổn định mDNS/RTSP.

## 19. Nội Dung Tóm Tắt Đưa Vào Báo Cáo

Hệ thống Traffic Warning gồm edge camera node trên Raspberry Pi 5, backend FastAPI và frontend React. Edge node có nhiệm vụ đọc camera 2K, mã hóa H.264 và phát RTSP ổn định bằng `rpicam-vid`, `ffmpeg` và MediaMTX. Node tự sinh `camera_id`, hostname mDNS và port ổn định từ MAC address, hiển thị trạng thái trên TFT, điều khiển LED/nút nhấn, có watchdog và tự chạy lại bằng systemd sau khi boot.

Backend đọc RTSP từ edge node hoặc nguồn video khác bằng OpenCV, sau đó dùng YOLOv8, ByteTrack và luật hình học lane/maneuver để giám sát giao thông thời gian thực. Hệ thống không dùng AI để nhận diện làn; lane được cấu hình thủ công bằng polygon và xử lý bằng Shapely. Vi phạm được phát hiện bằng state machine và evidence fusion: sai làn dựa trên stable lane transition, ngược chiều dựa trên vector trajectory và direction path, rẽ sai dựa trên turn/exit zone, line crossing, heading, curvature và temporal continuity.

OCR biển số là nhánh bổ sung, có detector riêng và temporal voting để tránh chốt kết quả từ một frame đơn lẻ. Frontend cung cấp màn hình giám sát realtime, thống kê/lịch sử vi phạm, công cụ cấu hình lane/maneuver bằng canvas và popup quản lý edge node. Thiết kế hiện phù hợp đồ án/demo kỹ thuật, có khả năng giải thích quyết định và chỉnh cấu hình trực quan. Khi triển khai production, cần bổ sung auth, audit log, health monitoring tập trung, retention policy, tối ưu đa camera/GPU và kiểm thử vận hành dài hạn trên Raspberry Pi 5.
