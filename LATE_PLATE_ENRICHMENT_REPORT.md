# LATE_PLATE_ENRICHMENT_REPORT

## 1. Thiết kế đã triển khai
- Triển khai cơ chế `violation first, plate later`:
- Violation vẫn được emit/lưu ngay tại luồng `_handle_violations()` như hiện tại.
- Bổ sung nhánh enrich biển số muộn trong OCR worker:
- Khi OCR resolver đã `confirmed` và thỏa điều kiện an toàn, hệ thống cập nhật các violation đang `pending/unreadable/null` tương ứng.
- Cập nhật DB đi qua method repository mới `update_pending_violation_plate(...)`.
- Bổ sung state nhẹ trong `CameraContext` để theo dõi:
- pending violation theo `vehicle_id`,
- track continuity safety,
- ưu tiên OCR cho xe có pending violation.

## 2. Vì sao không delay violation
- `_handle_violations()` vẫn tạo event ngay khi logic vi phạm đủ điều kiện.
- Không chờ OCR đồng bộ trong vòng lặp chính.
- OCR enrichment chạy ở worker riêng và cập nhật DB sau khi có bằng chứng đủ mạnh.

## 3. Khóa liên kết an toàn sử dụng
- Dùng bắt buộc bộ khóa:
- `camera_id + track_session_id + vehicle_id`.
- Method DB update filter đầy đủ 3 trường này trước khi update.

## 4. Điều kiện update biển số
- Chỉ update khi tất cả điều kiện chính đúng:
- snapshot OCR `status == confirmed`,
- `consensus_hits >= violation_update_consensus_min_hits`,
- `confidence >= violation_update_min_confidence`,
- violation hiện có status thuộc `null/pending/unreadable`,
- cùng `camera_id`, `track_session_id`, `vehicle_id`,
- trong `violation_update_window_ms`,
- có `license_plate_image_path` hợp lệ từ crop biển số,
- track continuity sạch (nếu bật `require_clean_track_for_violation_update`).

## 5. Cách chống gán nhầm
- Không dùng riêng `vehicle_id`, bắt buộc thêm `track_session_id`.
- Không overwrite violation đã `confirmed` khác text vì query update chỉ nhắm vào `pending/unreadable/null`.
- Có safety gate continuity:
- reject nếu track có gap lớn bất thường,
- reject nếu bbox nhảy quá mạnh,
- reject nếu có nguy cơ overlap/xe quá gần (rủi ro occlusion/ambiguity).
- Nếu không chắc chắn thì không update (ưu tiên thiếu biển số hơn biển số sai).

## 6. Cách tránh giảm hiệu năng
- Không đổi logic detection/tracking/violation emit.
- Không OCR toàn frame, vẫn OCR trên crop vehicle.
- Queue OCR vẫn coalesce theo `vehicle_id` (mỗi xe giữ job mới nhất).
- Bổ sung ưu tiên xe có pending violation mà không tăng backlog.
- DB update chỉ trigger khi OCR đã confirmed + đủ gate, tránh ghi DB vô ích liên tục.
- State pending/continuity được prune theo TTL để tránh leak RAM.

## 7. File đã sửa
- `backend/app/logic/license_plate_logic.py`
- `backend/app/core/config.py`
- `backend/app/db/repository.py`
- `backend/app/managers/camera_context.py`
- `backend/app/managers/camera_manager.py`
- `backend/tests/test_app_config_loading.py`
- `backend/tests/test_camera_context_queue.py`
- `config/settings.json`

### File test mới
- `backend/tests/test_violation_plate_enrichment_repository.py`
- `backend/tests/test_camera_context_late_plate_enrichment.py`

## 8. Test đã chạy
- `python -m compileall backend/app` -> pass.
- `cd backend && python -m pytest tests -q` -> `115 passed, 8 subtests passed`.

## 9. Rủi ro còn lại
- Gate continuity hiện là heuristic bảo thủ (gap/jump/overlap-distance). Điều này giảm rủi ro gán nhầm nhưng có thể làm giảm tỷ lệ enrich trong cảnh cực đông xe.
- Vì ưu tiên an toàn gán đúng, một số violation có thể vẫn giữ trạng thái pending/unreadable.

## 10. Cách rollback nếu cần
1. Tắt enrich bằng config:
- `license_plate.violation_update_enabled = false`.
2. Hoặc tắt luôn OCR biển số:
- `license_plate.enabled = false`.
3. Revert code các file đã sửa ở mục 7 nếu cần quay về behavior trước thay đổi.
