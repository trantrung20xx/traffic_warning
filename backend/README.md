# Backend (FastAPI)

## Chức năng
- Nhận RTSP từ nhiều camera (mỗi camera độc lập).
- YOLOv8 phát hiện phương tiện (motorcycle, car, truck, bus).
- ByteTrack theo dõi `vehicle_id` theo thời gian.
- Gán `lane_id` bằng polygon (point-in-polygon với bottom-center bbox).
- Phát hiện vi phạm bằng logic + hình học (wrong lane / wrong turn theo rule cấu hình).
- Lưu SQLite và push realtime tới Web App qua WebSocket.

## Cài đặt (skeleton)
```powershell
cd backend
python -m venv .venv
.\\venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

## Cấu hình
- `../config/cameras.json`: danh sách camera và metadata vị trí.
- `../config/lane_configs/<camera_id>.json`: polygon lane + rule vi phạm liên quan.
- `../config/settings.json`: các tham số runtime (confidence, resize, DB path...).

## Chạy
```powershell
cd backend
.\\venv\\Scripts\\Activate.ps1
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

## Endpoint chính (skeleton)
- `GET /api/health`
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}/lanes`
- `WS /ws/tracks` (phát bbox/vehicle_id/lane_id realtime)
- `WS /ws/violations` (phát violation events realtime)
- `GET /api/stats` (thống kê từ SQLite)

