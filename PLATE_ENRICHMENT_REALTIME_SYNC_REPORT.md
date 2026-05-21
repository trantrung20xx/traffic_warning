# PLATE_ENRICHMENT_REALTIME_SYNC_REPORT

## 1. Root cause vì sao UI trước đó không update ngay
- Frontend `MonitoringView` đang append sự kiện vi phạm realtime thay vì upsert theo `id`, nên cùng một violation khi enrich lại có thể bị stale/duplicate.
- `selectedViolation` (đang mở modal) không được đồng bộ tức thời theo event enrich mới, nên có cảm giác phải reload mới thấy.
- Backend đã có late enrichment nhưng payload update chưa chuẩn hóa đầy đủ alias evidence trong payload realtime để client dễ tiêu thụ nhất quán.

## 2. Luồng backend sau khi sửa
- Vẫn giữ nguyên `Violation first, plate later`.
- Violation emit ngay trong `_handle_violations()` như cũ, không delay luồng phát hiện.
- Tách thêm luồng độc lập `_attempt_evidence_upgrade()`:
  - không phụ thuộc `license_plate_status == confirmed`;
  - không cần `license_plate` text;
  - không cần `license_plate_image_path`;
  - chỉ cần candidate evidence tốt hơn baseline + qua gate an toàn track.
- Khi OCR/resolver đủ điều kiện confirmed an toàn, `_attempt_late_plate_enrichment()` chỉ xử lý cập nhật plate.
- Sau khi update thành công, backend emit lại event violation realtime với dữ liệu mới nhất.

## 3. Luồng frontend sau khi sửa
- Realtime event ở `MonitoringView` và `AnalyticsView` được **upsert theo khóa violation id** thay vì append thô.
- Nếu violation đang mở trong modal, state `selectedViolation` được cập nhật ngay theo event mới.
- Polling detail modal vẫn giữ làm fallback, nhưng không còn là cơ chế duy nhất.

## 4. Cách upsert realtime theo violation_id
- Thêm helper `violationRowKey()`:
  - Ưu tiên `id`.
  - Fallback theo `camera_id + vehicle_id + violation + timestamp` khi thiếu id.
- `MonitoringView`: `setViolations(prev => upsertViolationRows(prev, normalizedEvent, maxRows))`.
- `AnalyticsView`: `setHistory(prev => upsertHistoryRows(prev, normalizedEvent))`.

## 5. Điều kiện update plate
- Không thay đổi nguyên tắc an toàn hiện có:
  - cùng `camera_id + track_session_id + vehicle_id`;
  - snapshot OCR `confirmed`;
  - đủ `consensus_min_hits`;
  - đủ `violation_update_min_confidence`;
  - trong `violation_update_window_ms`;
  - track continuity sạch;
  - có `license_plate_image_path` hợp lệ;
  - không overwrite confirmed khác text.
- Nếu không có crop biển số mới:
  - chỉ cho phép update tiếp (ví dụ nâng evidence) khi đã có plate image hợp lệ trước đó.

## 6. Điều kiện nâng cấp evidence image
- Bổ sung baseline evidence vào pending state ngay khi tạo violation (kể cả đã confirmed plate):
  - `best_violation_image_path`
  - `best_violation_image_quality`
- Luồng evidence độc lập (`_attempt_evidence_upgrade`) chỉ thay `evidence_image_path` khi ảnh candidate mới có quality cao hơn baseline theo ngưỡng delta.
- Không tự động thay nếu ảnh mới kém hơn hoặc lưu ảnh mới thất bại.

## 7. Cách tránh gán nhầm xe/biển số
- Giữ chặt key liên kết: `camera_id + track_session_id + vehicle_id`.
- Không cho update nếu đã `confirmed` với text khác.
- Track continuity gate giữ nguyên (dirty track, gap lớn, overlap rủi ro đều chặn update).

## 8. Cách tránh giảm hiệu năng
- Không OCR đồng bộ trong hot path.
- Không OCR toàn frame.
- Không tăng queue vô hạn, vẫn giữ cơ chế latest job theo vehicle.
- Chỉ emit realtime khi DB thực sự có thay đổi (`updated_rows > 0`).
- Upsert frontend tại chỗ, tránh refresh cưỡng bức toàn trang.

## 9. File đã sửa
- `backend/app/db/repository.py`
- `backend/app/schemas/events.py`
- `backend/app/managers/camera_context.py`
- `backend/tests/test_violation_plate_enrichment_repository.py`
- `backend/tests/test_camera_context_late_plate_enrichment.py`
- `frontend/src/views/MonitoringView.jsx`
- `frontend/src/views/AnalyticsView.jsx`

## 10. Test/build đã chạy
- `python -m compileall backend/app` -> pass
- `cd backend && python -m pytest tests -q` -> pass (`136 passed, 8 subtests passed`)
- `cd frontend && npm run build` -> pass

## 11. Rủi ro còn lại
- Nếu cấu hình `violation_update_window_ms` quá ngắn so với thực tế camera/scene dày xe, có thể vẫn bỏ lỡ một số enrichment muộn (đây là trade-off an toàn hiện hữu của cấu hình).
- Ảnh evidence nâng cấp hiện dùng scoring nhẹ (area + sharpness) để đảm bảo chi phí thấp; nếu cần forensic-level ranking sâu hơn thì nên làm offline hoặc worker riêng để không ảnh hưởng realtime.
