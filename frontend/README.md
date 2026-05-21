# Frontend README

Frontend là giao diện web để vận hành Traffic Warning. Frontend không tự nhận diện xe và không chạy model AI. Mọi xử lý nặng như đọc video, nhận diện xe, theo dõi xe, kiểm tra vi phạm và đọc biển số đều nằm ở backend.

## Mục Lục

- [Frontend dùng để làm gì](#frontend-dùng-để-làm-gì)
- [Chạy frontend](#chạy-frontend)
- [Cấu hình kết nối backend](#cấu-hình-kết-nối-backend)
- [Các màn hình](#các-màn-hình)
- [Cách cấu hình camera và làn trên UI](#cách-cấu-hình-camera-và-làn-trên-ui)
- [Ý nghĩa các trường trên UI](#ý-nghĩa-các-trường-trên-ui)
- [Dữ liệu frontend nhận từ backend](#dữ-liệu-frontend-nhận-từ-backend)
- [Tổ chức source](#tổ-chức-source)
- [Build production](#build-production)

## Frontend Dùng Để Làm Gì

| Việc | Giải thích |
|---|---|
| Giám sát realtime | Xem camera, xe đang được theo dõi, làn xe, quỹ đạo, biển số, vi phạm mới và update plate/evidence realtime. |
| Thống kê | Xem tổng số vi phạm, biểu đồ, lịch sử, chi tiết vi phạm và xuất CSV/XLSX. |
| Quản lý camera | Tạo/sửa/xóa camera, upload ảnh nền. |
| Cấu hình làn | Vẽ vùng làn, vùng rẽ, vạch kiểm tra, chiều đi đúng. |
| Quản lý edge node | Xem health/identity edge camera, bật/tắt/restart stream và đổi image tuning qua backend proxy. |
| Kiểm tra cấu hình | Hiển thị cảnh báo nếu vùng làn/vùng rẽ dễ gây nhầm. |

Nguồn dữ liệu:

```text
REST API      -> camera, cấu hình, lịch sử, thống kê, export
WebSocket     -> xe realtime, vi phạm mới và update plate/evidence
MJPEG preview -> ảnh camera trực tiếp
Edge API      -> trạng thái phần cứng và điều khiển stream edge node
```

## Chạy Frontend

```powershell
cd frontend
npm install
npm run dev
```

Mở trình duyệt:

```text
http://localhost:5173
```

## Cấu Hình Kết Nối Backend

Frontend dùng 2 biến trong `frontend/.env`:

| Biến | Ý nghĩa | Ví dụ |
|---|---|---|
| `VITE_API_BASE` | Địa chỉ backend đầy đủ. | `http://localhost:8000` |
| `VITE_API_PORT` | Port backend khi không đặt `VITE_API_BASE`. | `8000` |

Ví dụ `frontend/.env`:

```env
VITE_API_BASE=http://localhost:8000
VITE_API_PORT=8000
```

Cách frontend tự chọn backend:

```text
1. Nếu có VITE_API_BASE -> dùng giá trị này.
2. Nếu không có -> lấy host đang mở frontend và ghép với VITE_API_PORT.
3. Nếu vẫn không xác định được -> dùng http://localhost:8000.
```

Ví dụ:

| Bạn mở frontend tại | Frontend sẽ gọi backend |
|---|---|
| `http://localhost:5173` | `http://localhost:8000` |
| `http://192.168.1.20:5173` | `http://192.168.1.20:8000` |
| Có `VITE_API_BASE=http://10.0.0.5:9000` | `http://10.0.0.5:9000` |

Sau khi sửa `.env`, cần dừng và chạy lại:

```powershell
npm run dev
```

## Các Màn Hình

### 1. Giám sát

Màn hình này dùng để xem camera đang chạy.

Hiển thị:

- Ảnh camera realtime.
- Vùng làn đã vẽ.
- Khung xe đang được backend theo dõi.
- ID xe, loại xe, làn hiện tại.
- Biển số và trạng thái đọc biển số nếu backend bật OCR.
- Trạng thái hướng đi nếu làn có cấu hình hướng đúng chiều.
- Quỹ đạo gần đây của xe.
- Vi phạm mới.
- Cập nhật realtime khi backend enrich biển số hoặc nâng cấp ảnh evidence.
- FPS xử lý do backend gửi.

Các trạng thái biển số:

| Trạng thái | Nghĩa |
|---|---|
| `pending` | Đang chờ đọc thêm. |
| `confirmed` | Đã đủ số lần xác nhận. |
| `uncertain` | Có kết quả nhưng chưa chắc. |
| `unreadable` | Đã thử nhiều lần nhưng không đọc được. |

Lưu ý: UI chỉ hiển thị text biển số như bằng chứng hợp lệ khi backend gửi kèm ảnh crop biển số. Nếu có text nhưng thiếu ảnh crop, frontend sẽ giữ trạng thái chờ/không đọc được để tránh làm người vận hành hiểu nhầm.

### 2. Thống kê

Màn hình này dùng để xem dữ liệu đã lưu.

Chức năng:

- Lọc theo camera.
- Lọc theo khoảng thời gian.
- Tìm theo biển số.
- Xem biểu đồ theo thời gian.
- Xem thống kê theo camera, loại xe, loại vi phạm và khu vực.
- Xem bảng lịch sử.
- Mở chi tiết một vi phạm.
- Nhận update realtime cho item lịch sử khi backend enrich plate/evidence.
- Tải CSV hoặc XLSX.

### 3. Quản lý

Màn hình này dùng để cấu hình hệ thống mà không cần sửa JSON bằng tay.

Chức năng:

- Thêm/sửa/xóa camera.
- Upload ảnh nền để vẽ làn dễ hơn.
- Vẽ vùng làn.
- Vẽ vùng đi vào, vùng/vạch xác nhận.
- Vẽ chiều đi đúng để phát hiện ngược chiều.
- Vẽ vùng rẽ/vạch ra/vùng ra cho từng hướng.
- Cấu hình xe nào được phép đi trong làn.
- Cấu hình xe được phép đổi sang làn nào.
- Bật/tắt hoặc cho phép/cấm từng hướng đi.
- Undo/redo thao tác vẽ.

### 4. Edge cameras

Màn hình này dùng để theo dõi các Raspberry Pi edge camera mà backend discovery được.

Chức năng:

- Xem camera ID, node ID, mDNS hostname, IP, RTSP URL và trạng thái stream.
- Xem nhiệt độ, FPS estimate, restart count, watchdog latched và lỗi gần nhất.
- Rescan edge camera.
- Bật, tắt hoặc restart stream.
- Đổi profile image tuning của edge node.

## Cách Cấu Hình Camera Và Làn Trên UI

### Quy trình khuyến nghị

```text
1. Tạo camera hoặc chọn camera cần sửa
2. Nhập nguồn video và kích thước frame
3. Upload ảnh nền chụp từ camera
4. Vẽ polygon cho từng làn
5. Vẽ vùng approach/commit nếu cần nhận diện rẽ
6. Vẽ direction path/check zone nếu cần phát hiện ngược chiều
7. Cấu hình loại xe, đổi làn và hướng đi được phép
8. Xem cảnh báo validation
9. Lưu cấu hình
10. Mở màn hình Giám sát để kiểm tra realtime
```

### Khi vẽ làn

Vẽ `polygon` bao sát phần mặt đường của làn. Không nên để hai làn chồng lên nhau quá nhiều, vì xe ở gần vạch có thể bị gán nhầm làn.

### Khi vẽ phát hiện ngược chiều

Vẽ `direction_path` theo đúng chiều xe được phép chạy.

```text
Điểm đầu -> điểm cuối = chiều đúng
```

`check_zone` nên là vùng xe chạy ổn định trong làn, tránh vùng giao nhau hoặc vùng rẽ phức tạp.

### Khi vẽ hướng rẽ

Mỗi hướng có thể dùng:

- `turn_zone`: vùng xe đi qua khi thực hiện hướng đó.
- `exit_line`: vạch xe cắt qua khi đi ra nhánh đó.
- `exit_zone`: vùng xe đi vào sau khi ra nhánh đó.

Không bắt buộc lúc nào cũng đủ cả 3 vùng, nhưng càng rõ thì backend càng dễ kết luận đúng.

## Ý Nghĩa Các Trường Trên UI

### Camera

| Trường | Người dùng cần hiểu |
|---|---|
| `camera_id` | Tên duy nhất của camera. Không nên đổi tùy tiện sau khi đã có dữ liệu. |
| `rtsp_url` | Link camera hoặc đường dẫn file video backend đọc. |
| `camera_type` | Loại camera: ven đường, từ trên cao hoặc nút giao. |
| `view_direction` | Mô tả hướng nhìn để người vận hành dễ nhớ. |
| `road_name` | Tên đường, dùng trong lịch sử và thống kê. |
| `intersection_name` | Tên nút giao, có thể để trống. |
| `gps_lat`, `gps_lng` | Tọa độ GPS, có thể để trống. |
| `frame_width`, `frame_height` | Kích thước ảnh dùng khi vẽ và xử lý. Đổi giá trị này có thể làm lệch vùng đã vẽ. |
| `monitored_lanes` | Danh sách ID làn camera theo dõi. |

### Lane

| Trường | Người dùng cần hiểu |
|---|---|
| `lane_id` | Số của làn. Dùng để đặt luật đổi làn và lưu vi phạm. |
| `polygon` | Vùng chính của làn. Backend dựa vào đây để gán xe vào làn. |
| `approach_zone` | Vùng xe đi vào trước khi rẽ. Giúp backend biết xe bắt đầu từ làn nào. |
| `commit_gate` | Vùng xác nhận xe đã bắt đầu đi theo một hướng. |
| `commit_line` | Vạch xác nhận xe đã bắt đầu đi theo một hướng. |
| `allowed_lane_changes` | Xe từ làn này được phép chuyển sang những làn nào. |
| `allowed_vehicle_types` | Những loại xe được phép đi trong làn này. |
| `direction_rule` | Quy tắc chiều đi đúng để bắt xe đi ngược chiều. |
| `maneuvers` | Cấu hình đi thẳng, rẽ trái, rẽ phải, quay đầu. |

### Loại xe

Các giá trị đang dùng:

| Giá trị | Hiển thị |
|---|---|
| `motorcycle` | Xe máy |
| `car` | Ô tô |
| `truck` | Xe tải |
| `bus` | Xe buýt |

Lưu ý:

- `detection.allowed_classes` trong backend quyết định loại xe nào được nhận diện.
- `allowed_vehicle_types` trên UI chỉ quyết định loại xe nào được phép trong làn.

Ví dụ: Nếu backend vẫn nhận diện `truck`, nhưng lane chỉ cho `car`, xe tải vào lane đó có thể bị báo `vehicle_type_not_allowed`.

### Hướng đi

Các hướng đang dùng:

| Giá trị | Hiển thị |
|---|---|
| `straight` | Đi thẳng |
| `right` | Rẽ phải |
| `left` | Rẽ trái |
| `u_turn` | Quay đầu |

| Trường | Ý nghĩa |
|---|---|
| `enabled` | Backend có cần nhận diện hướng này không. |
| `allowed` | Hướng này có được phép không. |
| `turn_zone` | Vùng xe đi qua khi thực hiện hướng này. |
| `exit_line` | Vạch xác nhận xe ra đúng hướng này. |
| `exit_zone` | Vùng xác nhận xe ra đúng hướng này. |

Ví dụ:

```text
Lane 1 cấm rẽ trái:
  left.enabled = true
  left.allowed = false
```

Nếu muốn backend không xét rẽ trái ở làn đó:

```text
left.enabled = false
```

### Ảnh nền

Ảnh nền chỉ dùng để vẽ cấu hình dễ hơn. Nó không phải video realtime.

| Nút | Ý nghĩa |
|---|---|
| Upload ảnh nền | Tải ảnh JPG/PNG làm nền để vẽ làn. |
| Xóa ảnh nền | Xóa ảnh nền của camera. |

Nên dùng ảnh chụp đúng cùng góc nhìn và kích thước gần với video đang xử lý.

### Validation

Backend trả về cảnh báo cấu hình. Các cảnh báo thường gặp:

| Cảnh báo | Ý nghĩa |
|---|---|
| Lane overlap | Hai làn chồng lên nhau nhiều, dễ gán nhầm làn. |
| Thiếu vùng/vạch | Một hướng bị bật nhưng thiếu vùng hỗ trợ nhận diện. |
| Direction path thiếu | Bật bắt ngược chiều nhưng chưa vẽ đủ đường chỉ hướng. |
| Vùng rẽ chồng nhau | Các hướng rẽ có vùng quá giống nhau, dễ kết luận nhầm. |

Validation giúp giảm lỗi cấu hình, nhưng không thay thế việc kiểm tra bằng video thật trên màn hình Giám sát.

## Dữ Liệu Frontend Nhận Từ Backend

### REST API

| Endpoint | Dùng ở đâu |
|---|---|
| `GET /api/cameras` | Load danh sách camera. |
| `GET /api/cameras/{camera_id}` | Load chi tiết camera, lane config, UI config và validation. |
| `POST /api/cameras` | Tạo camera. |
| `PUT /api/cameras/{camera_id}` | Lưu camera/lane config. |
| `DELETE /api/cameras/{camera_id}` | Xóa camera. |
| `GET /api/cameras/{camera_id}/preview` | Hiển thị video preview. |
| `POST /api/camera/{camera_id}/background-image` | Upload ảnh nền. |
| `GET /api/camera/{camera_id}/background-image` | Hiển thị ảnh nền. |
| `DELETE /api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET /api/violations/history` | Bảng lịch sử vi phạm. |
| `GET /api/violations/detail/{violation_id}` | Chi tiết một vi phạm đang mở trong modal. |
| `GET /api/violations/export` | Tải CSV/XLSX. |
| `GET /api/violations/evidence/{path}` | Xem ảnh bằng chứng. |
| `GET /api/analytics/dashboard` | Biểu đồ và thống kê. |
| `GET /api/edge-cameras` | Danh sách edge camera đã discovery. |
| `POST /api/edge-cameras/rescan` | Quét lại edge camera. |
| `GET /api/edge-cameras/{camera_id}` | Chi tiết health/identity edge camera. |
| `POST /api/edge-cameras/{camera_id}/stream/start` | Bật stream edge camera. |
| `POST /api/edge-cameras/{camera_id}/stream/stop` | Tắt stream edge camera. |
| `POST /api/edge-cameras/{camera_id}/stream/restart` | Restart stream edge camera. |
| `POST /api/edge-cameras/{camera_id}/image-tuning/cycle` | Đổi image tuning profile. |

### WebSocket

| WebSocket | Dữ liệu |
|---|---|
| `/ws/tracks?camera_id=...` | Xe realtime, khung xe, làn, biển số, hướng, FPS. |
| `/ws/violations?camera_id=...` | Vi phạm mới realtime và update plate/evidence cho vi phạm đã có. |

## Tổ Chức Source

| File | Vai trò |
|---|---|
| `src/api.js` | Gọi REST API, mở WebSocket, tạo URL ảnh preview/evidence. |
| `src/utils.js` | Nhãn tiếng Việt, xử lý thời gian, helper geometry, helper biểu đồ. |
| `src/violationDetails.js` | Chuẩn hóa dữ liệu chi tiết vi phạm và ẩn plate text nếu thiếu ảnh crop biển số. |
| `src/App.jsx` | Khung chính của ứng dụng và chuyển tab. |
| `src/views/MonitoringView.jsx` | Màn hình giám sát realtime. |
| `src/views/AnalyticsView.jsx` | Màn hình thống kê và lịch sử. |
| `src/views/ManagementView.jsx` | Màn hình quản lý camera/lane/maneuver. |
| `src/views/EdgeCamerasView.jsx` | Màn hình edge camera discovery, health và điều khiển stream. |
| `src/components/CameraCanvas.jsx` | Canvas vẽ camera, làn, xe, quỹ đạo và editor hình học. |
| `src/components/ViolationDetailModal.jsx` | Modal chi tiết vi phạm, ảnh evidence và ảnh crop biển số. |
| `src/components/canvas/PolygonLayer.js` | Hàm vẽ polygon, line và điểm kéo thả. |
| `src/components/canvas/BackgroundImageLayer.js` | Vẽ ảnh nền khi cấu hình. |
| `src/components/AppIcon.jsx` | Icon dùng trong UI. |

## Build Production

```powershell
cd frontend
npm run build
```

Xem bản build:

```powershell
npm run preview
```

## Tài Liệu Liên Quan

- [README tổng quan](../README.md)
- [Backend README](../backend/README.md)
- [Phân tích kỹ thuật](../SYSTEM_TECHNICAL_ANALYSIS.md)
