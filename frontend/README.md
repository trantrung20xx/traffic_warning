# Frontend (React)

## Mục tiêu
- Chỉ hiển thị dữ liệu realtime (không inference AI).
- Vẽ lane polygon lên Canvas theo cấu hình của từng camera.
- Nhận track/violation events từ backend qua WebSocket và hiển thị rõ `camera_id`, `vehicle_id`, `vehicle_type`, `lane_id`, `violation`.
- Dashboard: thống kê theo camera/khu vực/tuyến đường bằng dữ liệu từ backend (`/api/stats`).

Frontend này dùng **JavaScript thuần** (không dùng TypeScript).

## Cài đặt
```powershell
cd frontend
node -v
npm install
```

## Chạy
```powershell
npm run dev
```

## Config
Sửa `VITE_API_BASE` trong `.env` (nếu cần).

