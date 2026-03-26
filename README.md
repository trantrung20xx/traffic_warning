# Traffic Warning (Multi-Camera)

## Tổng quan
Hệ thống giám sát nhiều camera cố định (mỗi camera gắn với một vị trí địa lý và một tập lane được cấu hình thủ công bằng polygon). Backend chạy AI (YOLOv8 + ByteTrack) để phát hiện/phân loại và theo dõi phương tiện, sau đó áp dụng logic hình học để gán phương tiện vào `lane_id` và phát hiện vi phạm (sai làn / rẽ sai theo quy định của làn). Kết quả được đẩy realtime lên Web App qua WebSocket, đồng thời lưu SQLite để thống kê theo `camera`, `location` và toàn hệ thống.

## Kiến trúc triển khai (Text-based)
```
   Pi_1 ... Pi_N (mỗi Pi: 1 camera cố định)
      |          |
      | RTSP     | RTSP
      v          v
  ┌─────────┐  ┌─────────┐         (xử lý độc lập từng camera)
  │ Camera_1│  │ Camera_2│  ...  │ Camera_N│
  │ Context │  │ Context │         │ Context │
  └────┬────┘  └────┬────┘         └────┬────┘
       |            |                     |
       | Frames     | Frames              | Frames
       v            v                     v
  [Detector YOLOv8] [Detector YOLOv8]  [Detector YOLOv8]
          |              |                    |
          v              v                    v
   [Tracker ByteTrack] (track_id, bbox)
          |
          v
   [Lane Logic: point-in-polygon]
          |
          v
   [Violation Logic + Trajectory]
          |
          v
   [DB SQLite + In-memory Stats]
          |
          v
   WebSocket/REST (push events + tracks)
          |
          v
   Web App (React + Canvas vẽ lane polygon)
```

## Giả định kỹ thuật (cần xác nhận)
1. `lane` polygon được cấu hình trong hệ tọa độ ảnh (pixel) của khung hình mà camera backend xử lý (điểm polygon dùng trực tiếp với bbox).
2. Khi camera stream có độ phân giải thay đổi, bạn cần đảm bảo backend resize về một `frame_width/frame_height` cố định trước khi gán lane.
3. "Đi sai làn" (sai lệch lane) được định nghĩa theo logic hình học: phương tiện có `primary_lane_id` (lane tại thời điểm vào camera) và nếu sau đó bottom-center bbox vào lane khác không được phép trong một khoảng thời gian thì tạo violation `wrong_lane`.
4. "Rẽ sai hướng" được định nghĩa theo logic hình học thông qua các *turn/exit regions* (polygon hoặc vùng quyết định) cấu hình thủ công trong mỗi camera/lane. Nếu maneuver thực tế không thuộc `allowed_maneuvers` thì violation loại `turn_left_not_allowed`, `turn_right_not_allowed`, `turn_...`.

> Lưu ý: các rule trên được thiết kế để KHÔNG phải end-to-end AI quyết định vi phạm và KHÔNG dùng AI để nhận diện làn.

## Cấu trúc thư mục
- `backend/`: FastAPI, YOLOv8 + ByteTrack, lane/violation logic, SQLite, WebSocket.
- `frontend/`: React + Canvas, hiển thị lane polygon, track bbox, violation events và dashboard thống kê.

## Tài liệu chạy (sẽ có trong từng phần)
- Xem `backend/README.md` và `frontend/README.md`.

