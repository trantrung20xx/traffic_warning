# Traffic Warning

Traffic Warning là hệ thống giám sát giao thông bằng camera/video. Hệ thống đọc hình ảnh từ camera, nhận diện xe, theo dõi xe qua từng khung hình, xác định xe đang ở làn nào, kiểm tra các lỗi giao thông và hiển thị kết quả trên trình duyệt.

Ngày cập nhật tài liệu: 2026-05-09.

## Mục Lục

- [Hệ thống làm gì](#hệ-thống-làm-gì)
- [Luồng xử lý dễ hiểu](#luồng-xử-lý-dễ-hiểu)
- [Cấu trúc dự án](#cấu-trúc-dự-án)
- [Chạy hệ thống](#chạy-hệ-thống)
- [Model AI và OCR biển số](#model-ai-và-ocr-biển-số)
- [Cấu hình nhanh](#cấu-hình-nhanh)
- [Các lỗi có thể phát hiện](#các-lỗi-có-thể-phát-hiện)
- [API và màn hình](#api-và-màn-hình)
- [Kiểm tra](#kiểm-tra)
- [Tài liệu chi tiết](#tài-liệu-chi-tiết)

## Hệ Thống Làm Gì

| Phần | Nhiệm vụ |
|---|---|
| `backend` | Đọc video, nhận diện xe, theo dõi xe, kiểm tra vi phạm, đọc biển số, lưu dữ liệu và gửi kết quả realtime. |
| `frontend` | Hiển thị camera, vẽ làn/khung xe/quỹ đạo, xem vi phạm, thống kê và cấu hình camera/làn bằng giao diện web. |
| `config` | Lưu cấu hình camera, cấu hình làn, tham số hệ thống, ảnh nền, ảnh bằng chứng và database SQLite. |

Hệ thống hiện hỗ trợ:

- Nhận diện xe máy, ô tô, xe tải, xe buýt bằng YOLOv8.
- Theo dõi xe qua nhiều khung hình bằng ByteTrack.
- Gán xe vào làn bằng các vùng làn do người dùng vẽ.
- Phát hiện sai làn, đi ngược chiều, xe sai loại làn và các hướng đi bị cấm.
- Đọc biển số bằng detector biển số kết hợp PaddleOCR hoặc EasyOCR.
- Lưu lịch sử vi phạm, ảnh bằng chứng, ảnh crop biển số và xuất CSV/XLSX.
- Cấu hình camera, làn, vùng rẽ, vạch kiểm tra và ảnh nền trên giao diện.

## Luồng Xử Lý Dễ Hiểu

### Luồng tổng quát

```text
Camera hoặc file video
        |
        v
Backend đọc từng khung hình
        |
        v
Nhận diện xe bằng YOLOv8
        |
        v
Theo dõi cùng một xe qua nhiều khung hình
        |
        v
Xác định xe thuộc làn nào theo vùng làn đã vẽ
        |
        v
Kiểm tra luật: sai làn, ngược chiều, sai loại xe, rẽ sai
        |
        v
Lưu vi phạm + ảnh bằng chứng + biển số nếu đọc được
        |
        v
Frontend hiển thị realtime và thống kê
```

### Khi xe đi qua camera

```text
1. Backend thấy một xe trong khung hình.
2. Hệ thống gán cho xe một ID tạm thời để theo dõi.
3. Hệ thống nhìn phần đáy khung xe để xem xe đang nằm trong làn nào.
4. Nếu xe chuyển làn, hệ thống chờ đủ lâu trước khi kết luận để tránh báo nhầm do khung hình rung.
5. Nếu xe đi qua vùng/vạch đã cấu hình, hệ thống kiểm tra hướng đi và hướng rẽ.
6. Nếu đủ điều kiện vi phạm, backend tạo sự kiện, lưu ảnh và gửi lên giao diện.
```

### Dữ liệu đi qua hệ thống

```text
Video input
  -> Track xe realtime
  -> Lane + hướng + biển số
  -> Violation event
  -> SQLite + evidence images
  -> Dashboard + WebSocket + export file
```

## Cấu Trúc Dự Án

| Đường dẫn | Nội dung |
|---|---|
| `backend/app` | Toàn bộ server, xử lý video, AI, logic vi phạm, API và database. |
| `backend/tests` | Bộ test backend. |
| `frontend/src` | Giao diện React. |
| `config/cameras.json` | Danh sách camera. |
| `config/lane_configs` | Cấu hình làn theo từng camera. |
| `config/settings.json` | Tham số chung của hệ thống. |
| `config/background_images` | Ảnh nền để căn chỉnh làn trên UI. |
| `config/evidence_images` | Ảnh bằng chứng vi phạm. |
| `config/traffic_warning.sqlite` | Database SQLite lưu lịch sử vi phạm. |

## Chạy Hệ Thống

### 1. Chạy backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Backend chạy tại:

```text
http://localhost:8000
```

Kiểm tra backend:

```text
http://localhost:8000/api/health
```

### 2. Chạy frontend

```powershell
cd frontend
npm install
npm run dev
```

Frontend chạy tại:

```text
http://localhost:5173
```

### 3. Kết nối frontend với backend

Nếu backend chạy đúng port `8000`, thường không cần cấu hình thêm.

Nếu backend chạy ở địa chỉ khác, tạo file `frontend/.env`:

```env
VITE_API_BASE=http://localhost:8000
VITE_API_PORT=8000
```

Sau khi sửa `.env`, chạy lại `npm run dev`.

## Model AI Và OCR Biển Số

### Model nhận diện phương tiện

Hệ thống dùng YOLOv8. File model hiện đang được cấu hình trong `config/settings.json`:

```json
{
  "detection": {
    "weights_path": "backend/yolov8m.pt"
  }
}
```

Các model YOLOv8 phổ biến:

| Model | Link tải | Khi nên dùng |
|---|---|---|
| `yolov8n.pt` | [Tải](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) | Máy yếu, cần tốc độ cao. |
| `yolov8s.pt` | [Tải](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt) | Cân bằng tốc độ và chất lượng. |
| `yolov8m.pt` | [Tải](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt) | Chất lượng tốt hơn, máy cần khỏe hơn. |
| `yolov8l.pt` | [Tải](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8l.pt) | Ưu tiên độ chính xác. |
| `yolov8x.pt` | [Tải](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8x.pt) | Nặng nhất, nên dùng GPU mạnh. |

Ví dụ tải `yolov8m.pt`:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt" -OutFile ".\backend\yolov8m.pt"
```

### Model biển số

Biển số được xử lý theo 2 bước:

```text
Crop xe -> tìm vùng biển số -> đọc chữ/số trên biển số
```

Cấu hình chính:

```json
{
  "license_plate": {
    "enabled": true,
    "detector_weights_path": "backend/license_plate_yolov8.pt",
    "ocr_backend": "paddleocr"
  }
}
```

Đổi OCR sang EasyOCR:

```json
{
  "license_plate": {
    "enabled": true,
    "ocr_backend": "easyocr",
    "easyocr_lang": "en",
    "easyocr_use_gpu": true
  }
}
```

Nếu không cần biển số, đặt:

```json
{
  "license_plate": {
    "enabled": false
  }
}
```

## Cấu Hình Nhanh

### Các file cần biết

| File | Dùng để |
|---|---|
| `config/cameras.json` | Thêm/sửa nguồn camera hoặc video. |
| `config/lane_configs/<camera_id>.json` | Lưu các vùng làn, vùng rẽ, vạch kiểm tra của từng camera. Nên chỉnh qua UI. |
| `config/settings.json` | Chỉnh model, ngưỡng nhận diện, tốc độ gửi dữ liệu, OCR, thống kê. |

### Cấu hình camera

Ví dụ một camera:

```json
{
  "camera_id": "cam_01",
  "rtsp_url": "rtsp://user:pass@192.168.1.10/stream",
  "camera_type": "intersection",
  "view_direction": "northbound",
  "frame_width": 1280,
  "frame_height": 720,
  "monitored_lanes": [1, 2, 3]
}
```

| Trường | Cách hiểu |
|---|---|
| `camera_id` | Tên duy nhất của camera. Tên này cũng dùng để tìm file lane config tương ứng. |
| `rtsp_url` | Nguồn video. Có thể là RTSP, HTTP hoặc đường dẫn file video local. |
| `camera_type` | Loại góc nhìn: ven đường, từ trên cao hoặc nút giao. |
| `view_direction` | Ghi chú hướng nhìn để dễ quản lý. |
| `frame_width`, `frame_height` | Kích thước ảnh backend dùng khi xử lý. Nên khớp với kích thước khi vẽ làn. |
| `monitored_lanes` | Các ID làn camera này theo dõi. |

### Cấu hình làn

Nên cấu hình làn bằng màn hình `Quản lý` trên frontend, vì các điểm hình học cần vẽ trực quan.

Các điểm trong lane config được lưu theo tỉ lệ `0` đến `1` thay vì pixel. Ví dụ `x = 0.5` nghĩa là giữa chiều ngang ảnh.

| Trường | Cách hiểu |
|---|---|
| `polygon` | Vùng bao quanh làn. Hệ thống dùng vùng này để biết xe đang ở làn nào. |
| `approach_zone` | Vùng xe đi vào trước khi rẽ. Giúp hệ thống nhớ làn ban đầu của xe. |
| `commit_gate` | Vùng xác nhận xe đã bắt đầu đi theo một hướng. |
| `commit_line` | Vạch xác nhận xe đã bắt đầu đi theo một hướng. |
| `allowed_lane_changes` | Danh sách làn xe được phép chuyển sang. |
| `allowed_vehicle_types` | Loại xe được phép trong làn này. |
| `direction_rule` | Cấu hình chiều đi đúng của làn, dùng để phát hiện ngược chiều. |
| `maneuvers` | Cấu hình đi thẳng, rẽ trái, rẽ phải, quay đầu. |

### Cấu hình hệ thống

Các nhóm hay chỉnh trong `settings.json`:

| Nhóm | Khi nào cần chỉnh |
|---|---|
| `detection` | Đổi model YOLO, đổi GPU/CPU, tăng/giảm độ nhạy nhận diện. |
| `tracking` | Xe bị đổi ID nhiều hoặc loại xe nhảy liên tục. |
| `lane_assignment` | Xe nằm sát ranh giới làn bị nhảy làn. |
| `wrong_lane` | Muốn báo sai làn nhanh hơn hoặc chậm hơn. |
| `direction_detection` | Muốn chỉnh phát hiện ngược chiều. |
| `turn_detection` | Muốn chỉnh nhận diện đi thẳng/rẽ/quay đầu. |
| `evidence_fusion` | Muốn chỉnh cách gộp các dấu hiệu trước khi kết luận rẽ sai. |
| `license_plate` | Bật/tắt biển số, đổi OCR, chỉnh tần suất đọc biển số. |
| `performance` | Chỉnh FPS preview hoặc cách tính FPS xử lý. |
| `websocket` | Chỉnh tần suất gửi dữ liệu realtime lên frontend. |
| `analytics` | Chỉnh cách chia mốc thời gian trên biểu đồ thống kê. |

Chi tiết từng trường nằm trong [Backend README](backend/README.md) và [Frontend README](frontend/README.md).

## Các Lỗi Có Thể Phát Hiện

| Mã lỗi | Ý nghĩa |
|---|---|
| `wrong_lane` | Xe chuyển sang làn không được phép. |
| `wrong_direction` | Xe đi ngược chiều so với hướng đã cấu hình. |
| `vehicle_type_not_allowed` | Loại xe không được phép đi trong làn. |
| `turn_left_not_allowed` | Rẽ trái khi làn không cho phép. |
| `turn_right_not_allowed` | Rẽ phải khi làn không cho phép. |
| `turn_straight_not_allowed` | Đi thẳng khi làn không cho phép. |
| `turn_u_turn_not_allowed` | Quay đầu khi làn không cho phép. |

## API Và Màn Hình

Frontend có 3 màn hình chính:

| Màn hình | Dùng để |
|---|---|
| `Giám sát` | Xem camera realtime, xe đang theo dõi, biển số, hướng đi và vi phạm mới. |
| `Thống kê` | Xem biểu đồ, lịch sử vi phạm, lọc theo thời gian/camera/biển số, export CSV/XLSX. |
| `Quản lý` | Thêm camera, upload ảnh nền, vẽ làn, vẽ vùng rẽ, đặt luật cho từng làn. |

API chính:

| Loại | Đường dẫn |
|---|---|
| Camera | `/api/cameras`, `/api/cameras/{camera_id}` |
| Preview | `/api/cameras/{camera_id}/preview` |
| Làn | `/api/cameras/{camera_id}/lanes` |
| Vi phạm | `/api/violations/history`, `/api/violations/export`, `/api/violations/evidence/...` |
| Thống kê | `/api/analytics/dashboard`, `/api/stats` |
| Realtime | `WS /ws/tracks`, `WS /ws/violations` |

## Kiểm Tra

Backend:

```powershell
cd backend
python -m pytest tests -q
```

Kiểm tra cú pháp backend:

```powershell
python -m compileall backend/app
```

Frontend:

```powershell
cd frontend
npm run build
```

## Tài Liệu Chi Tiết

- [Backend README](backend/README.md)
- [Frontend README](frontend/README.md)
- [Phân tích kỹ thuật hệ thống](SYSTEM_TECHNICAL_ANALYSIS.md)
