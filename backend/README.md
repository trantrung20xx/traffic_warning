# Backend README

Backend là phần xử lý chính của hệ thống Traffic Warning. Backend đọc video, nhận diện xe, theo dõi xe, xác định xe thuộc làn nào, kiểm tra vi phạm, đọc biển số nếu bật, lưu dữ liệu và gửi kết quả cho frontend.

## Mục Lục

- [Backend làm gì](#backend-làm-gì)
- [Chạy backend](#chạy-backend)
- [Luồng xử lý](#luồng-xử-lý)
- [Các file cấu hình](#các-file-cấu-hình)
- [Giải thích `cameras.json`](#giải-thích-camerasjson)
- [Giải thích lane config](#giải-thích-lane-config)
- [Giải thích `settings.json`](#giải-thích-settingsjson)
- [Model và OCR](#model-và-ocr)
- [API và WebSocket](#api-và-websocket)
- [Kiểm thử](#kiểm-thử)

## Backend Làm Gì

| Việc | Giải thích ngắn |
|---|---|
| Đọc video | Lấy từng khung hình từ camera RTSP/HTTP hoặc file video local. |
| Nhận diện xe | Dùng YOLOv8 để tìm xe máy, ô tô, xe tải, xe buýt. |
| Theo dõi xe | Gán ID cho xe để biết cùng một xe đi qua nhiều khung hình. |
| Gán làn | Dùng vùng làn đã vẽ để biết xe đang ở làn nào. |
| Kiểm tra vi phạm | Kiểm tra sai làn, ngược chiều, sai loại xe, rẽ sai. |
| Đọc biển số | Tìm vùng biển số và đọc chữ/số bằng worker riêng, có voting theo thời gian và late enrichment cho vi phạm đã lưu. |
| Lưu dữ liệu | Ghi vi phạm vào SQLite, lưu ảnh evidence tổng quan và ảnh crop biển số khi có. |
| Gửi realtime | Gửi xe đang theo dõi, vi phạm mới và update enrichment/evidence qua WebSocket. |

## Chạy Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
scoop install ffmpeg
ffmpeg -version
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

`ffmpeg` là dependency hệ thống dùng để giải mã RTSP nên không thể cài bằng
`requirements.txt`; package `ffmpeg-python` cũng không cung cấp `ffmpeg.exe`.
Nếu Windows không có Scoop, dùng `winget install --id Gyan.FFmpeg -e`.

Kiểm tra server:

```text
http://localhost:8000/api/health
```

Nếu chạy lần đầu, cần có các file model được trỏ trong `config/settings.json`, ví dụ:

- `backend/yolov8s.pt`
- `backend/license_plate_yolov8.pt` nếu bật biển số

## Luồng Xử Lý

```text
1. RtspFrameReader đọc frame mới nhất
2. YOLOv8 + ByteTrack tìm và theo dõi xe
3. StableTrackIdAssigner giữ ID xe ổn định hơn
4. TemporalVehicleTypeAssigner làm mượt loại xe
5. LaneLogic tính làn từ đáy khung xe và vùng làn
6. TemporalLaneAssigner làm mượt kết quả làn
7. ViolationLogic kiểm tra các luật vi phạm
8. License plate worker đọc biển số ở luồng riêng nếu bật
9. CameraContext lưu vi phạm ngay, đăng ký pending enrichment, gửi WebSocket
10. OCR/evidence update sau nếu đủ điều kiện an toàn và UI được đồng bộ realtime
```

Nói đơn giản:

```text
Frame -> Xe -> Làn -> Luật -> Vi phạm ngay -> Evidence/OCR cập nhật sau -> UI
```

## Các File Cấu Hình

Backend đọc cấu hình từ thư mục `config` ở repo root.

| File | Nên sửa bằng gì | Dùng để |
|---|---|---|
| `config/cameras.json` | Có thể sửa tay hoặc qua UI | Khai báo camera/video. |
| `config/lane_configs/<camera_id>.json` | Nên sửa qua UI | Lưu vùng làn, vùng rẽ, vạch kiểm tra, hướng đúng chiều. |
| `config/settings.json` | Sửa tay khi cần chỉnh hệ thống | Model, ngưỡng nhận diện, OCR, realtime, thống kê. |

## Giải Thích `cameras.json`

Ví dụ:

```json
{
  "camera_id": "cam_01",
  "rtsp_url": "rtsp://user:pass@192.168.1.10/stream",
  "camera_type": "intersection",
  "view_direction": "northbound",
  "frame_width": 1280,
  "frame_height": 720,
  "location": {
    "road_name": "Đường A",
    "intersection_name": "Ngã tư B",
    "gps_lat": 21.0,
    "gps_lng": 105.0
  },
  "monitored_lanes": [1, 2, 3]
}
```

| Trường | Giải thích | Khi nào cần chú ý |
|---|---|---|
| `camera_id` | Tên duy nhất của camera. | Phải trùng với tên file lane config: `lane_configs/<camera_id>.json`. |
| `rtsp_url` | Nguồn video backend đọc. | Dùng RTSP/HTTP hoặc đường dẫn file video local. |
| `camera_type` | Loại camera: `roadside`, `overhead`, `intersection`. | Chủ yếu để quản lý và hiển thị. |
| `view_direction` | Mô tả hướng nhìn camera. | Ghi rõ để người cấu hình biết chiều xe chạy trong ảnh. |
| `frame_width` | Chiều rộng frame backend xử lý. | Nên khớp kích thước ảnh khi vẽ làn. |
| `frame_height` | Chiều cao frame backend xử lý. | Đổi giá trị này có thể làm lệch vùng đã vẽ nếu không cấu hình lại. |
| `location.road_name` | Tên đường. | Xuất hiện trong lịch sử, thống kê và export. |
| `location.intersection_name` | Tên nút giao. | Có thể để trống nếu không phải nút giao. |
| `location.gps_lat` | Vĩ độ. | Dùng cho báo cáo/vị trí, có thể để trống. |
| `location.gps_lng` | Kinh độ. | Dùng cho báo cáo/vị trí, có thể để trống. |
| `monitored_lanes` | Các ID làn camera quản lý. | Phải khớp với `lanes[].lane_id` trong lane config. |

## Giải Thích Lane Config

File lane nằm tại:

```text
config/lane_configs/<camera_id>.json
```

Tọa độ trong file này là số từ `0` đến `1`.

Ví dụ:

```text
x = 0.0 là mép trái ảnh
x = 1.0 là mép phải ảnh
y = 0.0 là mép trên ảnh
y = 1.0 là mép dưới ảnh
```

Nên vẽ bằng màn hình `Quản lý` của frontend thay vì sửa tay.

### Trường cấp camera

| Trường | Giải thích |
|---|---|
| `camera_id` | Camera mà file này thuộc về. |
| `frame_width` | Chiều rộng ảnh dùng khi chuyển tọa độ từ tỉ lệ sang pixel. |
| `frame_height` | Chiều cao ảnh dùng khi chuyển tọa độ từ tỉ lệ sang pixel. |
| `lanes` | Danh sách làn trong camera. |

### Trường của từng làn

| Trường | Giải thích dễ hiểu | Ảnh hưởng |
|---|---|---|
| `lane_id` | Số ID của làn. | Dùng trong UI, luật đổi làn, DB và export. |
| `polygon` | Vùng bao quanh làn. | Quan trọng nhất để biết xe đang ở làn nào. |
| `approach_zone` | Vùng xe đi vào trước khi rẽ. | Giúp hệ thống nhớ làn ban đầu của xe. |
| `commit_gate` | Vùng xác nhận xe bắt đầu đi theo một hướng. | Hỗ trợ nhận diện rẽ/đi thẳng/quay đầu. |
| `commit_line` | Vạch xác nhận xe bắt đầu đi theo một hướng. | Hữu ích khi vẽ một vạch rõ hơn vẽ cả vùng. |
| `allowed_lane_changes` | Danh sách làn được phép chuyển sang. | Nếu xe sang làn ngoài danh sách, có thể báo `wrong_lane`. |
| `allowed_vehicle_types` | Loại xe được phép trong làn. | Nếu xe khác loại đi vào, có thể báo `vehicle_type_not_allowed`. |
| `allowed_maneuvers` | Các hướng đi được phép. | Nếu không khai báo, backend tự suy từ `maneuvers.*.allowed`. |
| `direction_rule` | Chiều đi đúng của làn. | Dùng để phát hiện `wrong_direction`. |
| `maneuvers` | Cấu hình đi thẳng/rẽ/quay đầu. | Dùng để phát hiện hướng đi bị cấm. |

### `direction_rule`

| Trường | Giải thích | Gợi ý cấu hình |
|---|---|---|
| `enabled` | Bật/tắt kiểm tra đi ngược chiều. | Bật khi đã vẽ đúng `direction_path`. |
| `direction_path` | Đường chỉ chiều đi đúng. | Vẽ từ đầu làn đến cuối làn theo chiều xe được phép chạy. |
| `check_zone` | Vùng kiểm tra hướng. | Chỉ nên bao vùng xe đi ổn định, tránh vùng rẽ/giao cắt phức tạp. |

### `maneuvers`

Các hướng hợp lệ:

- `straight`: đi thẳng
- `right`: rẽ phải
- `left`: rẽ trái
- `u_turn`: quay đầu

| Trường | Giải thích | Gợi ý cấu hình |
|---|---|---|
| `enabled` | Có nhận diện hướng này không. | Tắt nếu camera/làn không đủ góc nhìn để nhận diện. |
| `allowed` | Hướng này có được phép không. | `false` nghĩa là nếu xe đi hướng này thì báo vi phạm. |
| `turn_zone` | Vùng xe đi qua khi thực hiện hướng đó. | Vẽ ôm theo đường xe thường đi. |
| `exit_line` | Vạch xe cắt qua khi ra khỏi hướng đó. | Vẽ ở cuối nhánh đi ra. |
| `exit_zone` | Vùng xe đi vào sau khi hoàn tất hướng đó. | Dùng khi vùng ra dễ vẽ hơn vạch. |

## Giải Thích `settings.json`

### `database`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `path` | Đường dẫn file SQLite lưu vi phạm. | Mặc định `config/traffic_warning.sqlite`. |

### `camera.stream`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `rtsp_reconnect_delay_s` | Số giây chờ trước khi thử mở lại camera nếu mất kết nối. | Tăng lên nếu camera hay timeout; giảm nếu muốn thử lại nhanh hơn. |

### `detection`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `weights_path` | File model YOLO nhận diện phương tiện. | Ví dụ `backend/yolov8s.pt`. |
| `backend` | Cách chạy model: `pytorch`, `onnxruntime`, `openvino`, `tensorrt`. | Nếu dùng `.pt`, để `pytorch` hoặc bỏ trống. |
| `device` | Thiết bị chạy AI: `auto`, `cpu`, `cuda`, `cuda:0`. | `auto` tự dùng GPU nếu có. |
| `confidence_threshold` | Điểm tin cậy tối thiểu để nhận một xe. | Tăng để bớt nhận nhầm, giảm để bớt bỏ sót. |
| `iou_threshold` | Mức gộp các khung xe bị trùng. | Thường giữ `0.7`. |
| `allowed_classes` | Loại xe YOLO được phép nhận diện. | Xóa class khỏi danh sách nếu không muốn theo dõi loại đó. |

### `tracking`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `tracker_config` | File cấu hình ByteTrack. | Thường dùng `bytetrack.yaml`. |
| `vehicle_type_history.window_ms` | Khoảng thời gian nhớ các dự đoán loại xe gần đây. | Tăng nếu nhãn xe nhảy nhiều. |
| `vehicle_type_history.size` | Số mẫu loại xe tối đa giữ lại cho mỗi xe. | Giá trị lớn hơn ổn định hơn nhưng đổi nhãn chậm hơn. |
| `vehicle_type_history.recency_weight_bias` | Mức ưu tiên mẫu mới hơn. | Tăng nhẹ nếu muốn loại xe cập nhật nhanh hơn. |
| `stable_track.max_idle_ms` | Thời gian giữ xe khi xe tạm mất dấu. | Tăng nếu xe hay bị che khuất ngắn. |
| `stable_track.min_iou_for_rebind` | Mức chồng lấn tối thiểu để nối lại ID xe. | Giảm quá thấp có thể nối nhầm xe. |
| `stable_track.max_normalized_distance` | Khoảng cách tâm tối đa để nối lại ID xe. | Tăng nếu xe di chuyển nhanh giữa hai frame. |

### `lane_assignment`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `temporal.observation_window_ms` | Khoảng thời gian xem lại các kết quả làn gần đây. | Tăng nếu lane bị nhảy do khung xe rung. |
| `temporal.min_majority_hits` | Số lần tối thiểu một làn phải xuất hiện để được tin. | Tăng để chắc hơn, giảm để phản ứng nhanh hơn. |
| `temporal.switch_min_duration_ms` | Thời gian chờ trước khi chấp nhận xe đổi làn. | Tăng để tránh báo sai khi xe sát vạch. |
| `overlap_preference.preferred_lane_overlap_ratio` | Mức ưu tiên giữ làn cũ khi xe nằm sát ranh giới. | Thường giữ `0.8`. |
| `overlap_preference.preferred_lane_overlap_margin_px` | Khoảng lệch pixel vẫn cho phép giữ làn cũ. | Tăng nếu xe hay bị nhảy làn ở biên. |

### `wrong_lane`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `min_duration_ms` | Xe phải ở trạng thái sai làn tối thiểu bao lâu mới báo. | Giảm để báo nhanh, tăng để bớt báo nhầm. |

### `direction_detection.defaults`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `same_direction_cos_threshold` | Ngưỡng coi là đi đúng chiều. | Giá trị cao hơn đòi hỏi xe đi đúng hướng rõ hơn. |
| `opposite_direction_cos_threshold` | Ngưỡng coi là đi ngược chiều. | Giá trị âm hơn sẽ chặt hơn. |
| `min_duration_ms` | Thời gian nghi ngờ ngược chiều trước khi báo. | Tăng để bớt báo nhầm. |
| `min_displacement_px` | Xe phải di chuyển tối thiểu bao nhiêu pixel mới xét hướng. | Tránh kết luận khi xe đứng yên. |
| `min_samples` | Số điểm quỹ đạo tối thiểu để xét hướng. | Tăng để chắc hơn. |
| `evaluation_window_samples` | Số điểm gần nhất dùng để đánh giá. | Cửa sổ ngắn phản ứng nhanh hơn. |
| `segment_min_displacement_px` | Đoạn di chuyển quá ngắn sẽ bị bỏ qua. | Tránh nhiễu do khung xe rung. |
| `segment_max_gap_ms` | Khoảng cách thời gian tối đa giữa hai điểm liên tiếp. | Vượt quá thì coi như đứt đoạn. |
| `warmup_min_duration_ms` | Thời gian chờ sau khi xe vào làn trước khi xét hướng. | Dùng khi lane dễ bị dính quỹ đạo cũ. |
| `warmup_min_samples` | Số mẫu tối thiểu sau khi vào làn. | Tránh kết luận quá sớm. |
| `opposite_consensus_min_segments` | Số đoạn gần cuối phải cùng ủng hộ ngược chiều. | Tăng để chặt hơn. |
| `opposite_consensus_ratio_min` | Tỷ lệ đoạn ủng hộ ngược chiều tối thiểu. | Tăng để bớt báo nhầm. |
| `opposite_min_displacement_px` | Quãng đi ngược chiều tối thiểu. | Tránh báo từ dịch chuyển rất nhỏ. |
| `opposite_min_displacement_lane_ratio` | Quãng đi ngược chiều theo tỷ lệ kích thước làn. | Hữu ích khi làn lớn/nhỏ khác nhau. |
| `lane_consensus_sample_window` | Số mẫu hướng đúng chiều dùng để học hướng làn. | Dùng cho ổn định hướng tham chiếu. |
| `lane_consensus_min_samples` | Số mẫu tối thiểu để tin hướng học được. | Tăng để chắc hơn. |
| `lane_consensus_inlier_dot_min` | Mức cùng hướng để giữ mẫu học. | Tăng để loại mẫu lệch mạnh. |
| `lane_consensus_blend_weight` | Mức pha hướng học được vào hướng cấu hình. | Thường để thấp. |
| `lane_consensus_alignment_min_dot` | Chỉ pha khi hai hướng đủ gần nhau. | Tránh làm lệch hướng cấu hình. |
| `lane_consensus_max_age_ms` | Tuổi tối đa của mẫu hướng đã học. | Hết hạn thì bỏ mẫu cũ. |
| `trajectory_blend_weight` | Mức pha hướng xe hiện tại vào hướng tham chiếu. | Giữ thấp để tránh tự kéo sai. |
| `trajectory_blend_min_alignment_dot` | Chỉ pha khi hướng xe đủ gần hướng tham chiếu. | Tránh pha khi xe đang đi sai. |

### `turn_detection`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `turn_region_min_hits` | Số lần xe phải đi qua vùng rẽ khi chưa có dấu hiệu mạnh khác. | Tăng để bớt báo nhầm. |
| `turn_state_timeout_ms` | Nếu quá lâu không có dấu hiệu mới thì reset trạng thái rẽ. | Tăng nếu xe đi chậm. |
| `trajectory_history_window_ms` | Thời gian giữ quỹ đạo xe để xét rẽ. | Tăng nếu cần nhìn đường đi dài hơn. |
| `heading.straight_max_deg` | Góc lệch tối đa vẫn coi là đi thẳng. | Tăng nếu đường cong nhẹ vẫn là đi thẳng. |
| `heading.turn_min_deg` | Góc đổi hướng tối thiểu để coi là rẽ. | Giảm nếu góc rẽ trong camera nhỏ. |
| `heading.turn_max_deg` | Góc đổi hướng tối đa cho rẽ trái/phải. | Quá lớn dễ giống quay đầu. |
| `heading.u_turn_min_change_deg` | Góc đổi hướng tối thiểu để coi là quay đầu. | Tăng để chặt hơn. |
| `heading.side_sign_tolerance` | Sai số khi xét trái/phải. | Thường không cần chỉnh. |
| `heading.value_sign_tolerance` | Sai số khi xét chiều quay. | Thường không cần chỉnh. |
| `heading.straight_curvature_max_for_heading_support` | Độ cong tối đa vẫn hỗ trợ đi thẳng. | Tăng nếu làn đi thẳng hơi cong. |
| `curvature.u_turn_min` | Độ cong tối thiểu để hỗ trợ quay đầu. | Tăng để bớt nhầm rẽ thường thành quay đầu. |
| `curvature.straight_max` | Độ cong tối đa để hỗ trợ đi thẳng. | Giảm nếu đi thẳng bị nhận nhầm. |
| `curvature.turn_min` | Độ cong tối thiểu để hỗ trợ rẽ. | Giảm nếu camera nhìn góc rẽ nhỏ. |
| `curvature.fallback_min` | Ngưỡng dự phòng cho hướng không thuộc nhóm chính. | Thường không cần chỉnh. |
| `opposite_direction.cos_threshold` | Ngưỡng hỗ trợ quay đầu khi xe quay ngược hướng. | Âm hơn là chặt hơn. |
| `trajectory.sample_inside_polygon_min_hits` | Số điểm đáy khung xe phải nằm trong vùng mới tính là hit. | Tăng để chắc hơn. |
| `trajectory.entry_heading_lookback_points` | Số điểm nhìn lại để đo hướng xe lúc vào vùng. | Tăng nếu xe đi chậm. |
| `trajectory.entry_heading_min_displacement_px` | Quãng đi tối thiểu để lấy hướng vào. | Tránh lấy hướng từ rung nhẹ. |
| `trajectory.heading_local_window_points` | Số điểm gần nhất dùng để đo hướng hiện tại. | Ít điểm phản ứng nhanh hơn. |
| `trajectory.fallback_reference.sample_window` | Số mẫu dùng để học hướng làn khi thiếu hướng rõ. | Dùng cho lane thiếu `direction_path`. |
| `trajectory.fallback_reference.min_samples` | Số mẫu tối thiểu để học hướng làn. | Tăng để chắc hơn. |
| `trajectory.fallback_reference.consensus_min` | Mức đồng thuận tối thiểu của mẫu hướng. | Tăng để chặt hơn. |
| `trajectory.fallback_reference.inlier_dot_min` | Mức cùng hướng để giữ mẫu. | Tăng để loại mẫu lệch. |
| `trajectory.fallback_reference.inlier_ratio_min` | Tỷ lệ mẫu tốt tối thiểu. | Tăng để chặt hơn. |
| `trajectory.fallback_reference.max_age_ms` | Tuổi tối đa của mẫu hướng học được. | Mẫu cũ quá sẽ bị bỏ. |
| `trajectory.fallback_reference.trajectory_blend_max_weight` | Mức pha hướng xe vào hướng học được. | Giữ thấp để tránh lệch. |
| `trajectory.fallback_reference.trajectory_blend_min_alignment_dot` | Chỉ pha khi hai hướng đủ gần. | Tránh pha sai hướng. |

### `evidence_fusion`

Trong tài liệu này gọi là “gộp dấu hiệu”: hệ thống không dựa vào một điểm duy nhất, mà cộng nhiều dấu hiệu như vùng rẽ, vạch ra, hướng xe và độ cong đường đi.

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `line_crossing.side_tolerance_px` | Vùng đệm quanh vạch, điểm nằm trong vùng này coi như đang chạm vạch. | Tăng nếu khung xe rung quanh vạch. |
| `line_crossing.min_pre_frames` | Số frame xe phải ổn định trước vạch. | Tăng để chắc hơn. |
| `line_crossing.min_post_frames` | Số frame xe phải ổn định sau vạch. | Tăng để chắc hơn. |
| `line_crossing.min_displacement_px` | Xe phải đi xa khỏi vạch tối thiểu bao nhiêu pixel. | Tránh báo từ rung nhẹ. |
| `line_crossing.min_displacement_ratio` | Quãng đi tối thiểu theo chiều dài vạch. | Hữu ích khi vạch dài/ngắn khác nhau. |
| `line_crossing.max_gap_ms` | Khoảng ngắt tối đa giữa các mẫu khi xét cắt vạch. | Vượt quá thì reset. |
| `line_crossing.cooldown_ms` | Thời gian nghỉ sau một lần cắt vạch. | Tránh một lần cắt bị đếm nhiều lần. |
| `evidence_expire_ms` | Dấu hiệu cũ tồn tại bao lâu trước khi bị bỏ. | Tăng nếu xe đi chậm. |
| `motion_window_samples` | Số điểm quỹ đạo dùng để xét chuyển động gần đây. | Tăng để mượt hơn. |
| `turn_scoring.decay_per_frame` | Mức giảm điểm dấu hiệu qua mỗi frame. | Tăng để dấu hiệu cũ mất nhanh hơn. |
| `turn_scoring.score_cap` | Điểm tối đa của một hướng. | Tránh điểm tăng vô hạn. |
| `turn_scoring.turn_zone_hit_weight` | Điểm khi xe đi qua vùng rẽ. | Tăng nếu vùng rẽ rất đáng tin. |
| `turn_scoring.exit_zone_hit_weight` | Điểm khi xe đi vào vùng ra. | Thường là dấu hiệu mạnh. |
| `turn_scoring.exit_line_hit_weight` | Điểm khi xe cắt vạch ra. | Thường là dấu hiệu rất mạnh. |
| `turn_scoring.heading_support_weight` | Điểm khi hướng xe ủng hộ maneuver. | Tăng nếu hướng xe đo ổn định. |
| `turn_scoring.curvature_support_weight` | Điểm khi độ cong đường đi ủng hộ maneuver. | Tăng nếu quỹ đạo rõ. |
| `turn_scoring.opposite_direction_weight` | Điểm hỗ trợ quay đầu khi xe đổi ngược hướng. | Dùng cho `u_turn`. |
| `turn_scoring.temporal_continuity_bonus` | Điểm cộng khi dấu hiệu liên tục nhiều frame. | Tăng để ưu tiên hành vi ổn định. |
| `turn_scoring.no_signal_penalty` | Điểm trừ khi frame hiện tại không có dấu hiệu. | Tăng để bỏ nhanh dấu hiệu yếu. |
| `turn_scoring.temporal_hits_min` | Số hit liên tục tối thiểu. | Tăng để chắc hơn. |
| `turn_scoring.strong_exit_min_temporal_hits` | Số hit tối thiểu khi đã có dấu hiệu ra mạnh. | Dùng cho exit line/zone. |
| `turn_scoring.strong_exit_min_turn_zone_hits` | Số hit vùng rẽ tối thiểu khi đã có dấu hiệu ra mạnh. | Tránh chỉ dựa vào vạch ra. |
| `turn_scoring.threshold_turn` | Điểm tối thiểu để xác nhận rẽ trái/phải khi chưa có exit mạnh. | Tăng để chặt hơn. |
| `turn_scoring.threshold_turn_with_exit` | Điểm tối thiểu khi đã có exit mạnh. | Có thể thấp hơn hoặc bằng threshold thường. |
| `turn_scoring.threshold_u_turn` | Điểm tối thiểu để xác nhận quay đầu. | Tăng nếu hay nhầm quay đầu. |
| `turn_scoring.threshold_u_turn_with_exit` | Điểm quay đầu khi có exit mạnh. | Dùng khi vùng/vạch quay đầu rõ. |
| `turn_scoring.threshold_straight` | Điểm tối thiểu để xác nhận đi thẳng. | Tăng nếu hay nhầm đi thẳng. |

### `event_lifecycle`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `violation_rearm_window_ms` | Sau khi báo một lỗi, cần chờ bao lâu mới cho báo lại cùng lỗi của xe đó. | Tăng để tránh trùng sự kiện. |
| `state_prune_max_age_s` | Sau bao lâu không thấy xe thì xóa state khỏi bộ nhớ. | Tăng nếu track bị mất ngắn rồi quay lại. |

### `geometry`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `evidence_crop.expand_x_ratio` | Nới ảnh bằng chứng sang trái/phải theo chiều rộng xe. | Tăng nếu ảnh crop quá sát xe. |
| `evidence_crop.expand_y_top_ratio` | Nới ảnh lên trên theo chiều cao xe. | Tăng để thấy thêm đầu xe/biển báo. |
| `evidence_crop.expand_y_bottom_ratio` | Nới ảnh xuống dưới theo chiều cao xe. | Tăng để thấy thêm mặt đường. |
| `evidence_crop.min_size_px` | Crop nhỏ hơn mức này sẽ không dùng crop hẹp. | Tránh lưu ảnh quá nhỏ. |
| `evidence_image.jpeg_quality` | Chất lượng JPEG ảnh bằng chứng. | Tăng chất lượng sẽ tăng dung lượng file. |

### `performance`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `preview.max_fps` | FPS tối đa của ảnh preview trên UI. | Giảm nếu mạng yếu hoặc CPU cao. |
| `preview.jpeg_quality` | Chất lượng JPEG preview. | Giảm để nhẹ mạng hơn. |
| `processing.fps_window_s` | Khoảng thời gian dùng để tính FPS xử lý. | Tăng để FPS hiển thị mượt hơn. |
| `processing.prune_interval_ms` | Chu kỳ dọn state cũ trong runtime. | Thường giữ mặc định. |
| `processing.license_plate_worker_max_pending_jobs` | Số job OCR tối đa đang chờ. | Tăng nếu xe đông và máy đủ khỏe. |
| `processing.license_plate_worker_batch_size` | Số job OCR xử lý mỗi lượt. | Tăng để tăng thông lượng OCR. |

### `websocket`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `track_push_interval_ms` | Khoảng cách giữa hai lần gửi danh sách xe realtime. | Giảm để UI mượt hơn, tăng để nhẹ hệ thống hơn. |
| `listener_queue_maxsize` | Số message tối đa giữ cho mỗi client. | Client quá chậm có thể bị loại khỏi listener. |

### `ui.monitoring`

Backend gửi nhóm này cho frontend để UI không phải hard-code.

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `trajectory.default_limit` | Số quỹ đạo mặc định muốn xem. | Dùng cho màn hình giám sát. |
| `trajectory.min_limit` | Giới hạn nhỏ nhất khi chọn số quỹ đạo. | Tránh chọn quá ít. |
| `trajectory.max_limit` | Giới hạn lớn nhất khi chọn số quỹ đạo. | Tránh UI quá nặng. |
| `trajectory.max_points_per_vehicle` | Số điểm tối đa giữ cho mỗi xe trên UI. | Tăng để đường đi dài hơn. |
| `trajectory.stale_ms` | Sau bao lâu không cập nhật thì quỹ đạo coi là cũ. | Tăng nếu camera FPS thấp. |
| `trajectory.min_point_distance_px` | Khoảng cách tối thiểu giữa hai điểm quỹ đạo. | Tăng để đường vẽ bớt rối. |
| `violation.list_max_rows` | Số dòng vi phạm realtime giữ trên UI. | Tăng nếu muốn xem nhiều hơn. |
| `violation.highlight_duration_ms` | Thời gian highlight xe vừa vi phạm. | Tăng để dễ quan sát. |
| `processing_fps.stale_after_ms` | Sau bao lâu FPS được coi là cũ. | Tăng nếu WebSocket gửi chậm. |
| `processing_fps.poll_interval_ms` | Chu kỳ UI kiểm tra trạng thái FPS. | Thường giữ mặc định. |

### `license_plate`

| Trường | Ý nghĩa | Gợi ý |
|---|---|---|
| `enabled` | Bật/tắt đọc biển số. | Tắt nếu không có model hoặc muốn tăng FPS. |
| `detector_weights_path` | File model tìm vùng biển số. | Ví dụ `backend/license_plate_yolov8.pt`. |
| `detector_backend` | Cách chạy model biển số. | Thường dùng `pytorch`. |
| `detector_confidence_threshold` | Điểm tin cậy tối thiểu để nhận vùng biển số. | Tăng để bớt bắt nhầm. |
| `detector_allowed_classes` | Tên class biển số được giữ lại. | Cần khớp model biển số đang dùng. |
| `ocr_backend` | Công cụ đọc chữ: `paddleocr` hoặc `easyocr`. | Đổi khi một công cụ đọc kém trong môi trường thực tế. |
| `easyocr_lang` | Ngôn ngữ cho EasyOCR. | Có thể nhập một hoặc nhiều mã ngôn ngữ, phân tách bằng dấu phẩy. |
| `easyocr_use_gpu` | EasyOCR có dùng GPU không. | Bật nếu có GPU phù hợp. |
| `paddle_ocr_version` | Phiên bản OCR PaddleOCR. | Mặc định `PP-OCRv5`. |
| `paddle_text_detection_model_name` | Model PaddleOCR tìm vùng chữ. | Giữ mặc định nếu không tùy biến. |
| `paddle_text_recognition_model_name` | Model PaddleOCR đọc chữ. | Giữ mặc định nếu không tùy biến. |
| `paddle_lang` | Ngôn ngữ PaddleOCR. | Mặc định `en`. |
| `paddle_use_gpu` | PaddleOCR có dùng GPU không. | Bật nếu cài PaddlePaddle GPU đúng môi trường. |
| `read_interval_ms` | Bao lâu mới đọc lại biển số cho cùng một xe. | Tăng để giảm tải OCR. |
| `min_ocr_confidence` | Điểm OCR tối thiểu để giữ kết quả. | Tăng để bớt sai, giảm để bớt bỏ sót. |
| `consensus_min_hits` | Số lần đọc trùng nhau để xác nhận biển số. | Tăng để chắc hơn. |
| `candidate_window_ms` | Khoảng thời gian giữ các kết quả OCR tạm. | Tăng nếu xe đi chậm. |
| `max_attempts_before_unreadable` | Số lần thử trước khi đánh dấu không đọc được. | Tăng nếu camera khó đọc biển số. |
| `crop_expand_x_ratio` | Nới crop xe sang ngang trước khi tìm biển số. | Tăng nếu biển số hay nằm sát mép crop. |
| `crop_expand_y_ratio` | Nới crop xe theo chiều dọc trước khi tìm biển số. | Tăng nếu crop bị thiếu biển số. |
| `image_jpeg_quality` | Chất lượng ảnh crop biển số. | Tăng để ảnh rõ hơn, file lớn hơn. |
| `violation_update_enabled` | Bật/tắt cập nhật biển số muộn vào violation đã lưu. | Nên bật để violation tạo trước, plate bổ sung sau. |
| `violation_update_min_confidence` | Ngưỡng confidence riêng khi cập nhật plate vào violation. | Tăng để chặt hơn, giảm để nhạy hơn. |
| `violation_update_consensus_min_hits` | Số lần OCR đồng thuận để coi là confirmed khi enrich. | Giữ >=2 nếu muốn tránh gán nhầm. |
| `violation_update_window_ms` | Khoảng thời gian cho phép enrich violation sau khi tạo. | Tăng nếu xe còn trong khung lâu. |
| `require_clean_track_for_violation_update` | Bắt buộc track continuity sạch mới update plate/evidence. | Nên bật để tránh gán nhầm. |
| `prioritize_pending_violation_ocr` | Ưu tiên OCR xe đã có violation pending. | Nên bật để UI chi tiết sớm có biển số. |

### `analytics.chart`

| Trường | Ý nghĩa |
|---|---|
| `minute_granularity_max_range_hours` | Khoảng thời gian tối đa để biểu đồ chia theo phút. |
| `hour_granularity_max_range_days` | Khoảng thời gian tối đa để biểu đồ chia theo giờ. |
| `day_granularity_max_range_days` | Khoảng thời gian tối đa để biểu đồ chia theo ngày. |
| `week_granularity_max_range_days` | Khoảng thời gian tối đa để biểu đồ chia theo tuần. |
| `minute_axis_label_interval_minutes` | Khoảng cách nhãn trục thời gian khi xem theo phút. |
| `minute_axis_max_ticks` | Số nhãn tối đa trên trục khi xem theo phút. |
| `hour_axis_max_ticks` | Số nhãn tối đa trên trục khi xem theo giờ. |
| `overview_axis_max_ticks` | Số nhãn tối đa trên biểu đồ tổng quan. |
| `point_markers_max_points` | Nếu nhiều điểm hơn số này, UI giảm marker để biểu đồ dễ nhìn. |

### `logging`

| Trường | Ý nghĩa |
|---|---|
| `level` | Mức log mong muốn. Hiện là key dự phòng. |
| `verbose_violation_trace` | Bật log chi tiết vi phạm. Hiện là key dự phòng. |

## Model Và OCR

### Tải model YOLO phương tiện

Ví dụ:

```powershell
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt" -OutFile ".\backend\yolov8s.pt"
```

Sau đó chỉnh:

```json
{
  "detection": {
    "weights_path": "backend/yolov8s.pt",
    "device": "auto"
  }
}
```

### Chọn CPU hoặc GPU

| Giá trị `detection.device` | Ý nghĩa |
|---|---|
| `auto` | Tự dùng GPU nếu PyTorch thấy GPU, nếu không thì dùng CPU. |
| `cpu` | Luôn chạy CPU. |
| `cuda` hoặc `cuda:0` | Chạy GPU NVIDIA đầu tiên. |

#### Cài PyTorch GPU trên Windows

Kiểm tra Windows nhận GPU và NVIDIA driver:

```powershell
nvidia-smi
```

Sau khi cài `requirements.txt`, thay PyTorch mặc định bằng wheel CUDA:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pip install --force-reinstall torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
python -c "import torch; print('torch:', torch.__version__); print('CUDA wheel:', torch.version.cuda); print('GPU:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

Wheel PyTorch đã chứa CUDA runtime cần thiết; thông thường chỉ cần NVIDIA
driver tương thích, không cần cài toàn bộ CUDA Toolkit. Sau đó đặt
`detection.device` thành `auto` hoặc `cuda:0` trong `config/settings.json`.

#### Dùng GPU cho OCR biển số

EasyOCR dùng cùng PyTorch, vì vậy chỉ cần đặt:

```json
{
  "license_plate": {
    "easyocr_use_gpu": true
  }
}
```

PaddleOCR dùng package GPU riêng. Không cài đồng thời `paddlepaddle` và
`paddlepaddle-gpu`:

```powershell
python -m pip uninstall paddlepaddle
python -m pip install paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -c "import paddle; paddle.utils.run_check(); print('Paddle CUDA:', paddle.device.is_compiled_with_cuda())"
```

Lệnh trên dùng wheel CUDA 12.6. Nếu môi trường cần CUDA 12.9, đổi `cu126`
thành `cu129`. Khi kiểm tra thành công, đặt `license_plate.paddle_use_gpu`
thành `true`. Chỉ khóa GPU của OCR backend đang chọn mới có hiệu lực:
`easyocr_use_gpu` cho EasyOCR hoặc `paddle_use_gpu` cho PaddleOCR.

### Đổi sang EasyOCR

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

### Tắt OCR biển số

```json
{
  "license_plate": {
    "enabled": false
  }
}
```

### Late plate enrichment và ảnh bằng chứng

Vi phạm được tạo ngay khi logic vi phạm đủ điều kiện. Nếu lúc đó chưa có biển số hoặc chưa có ảnh crop biển số hợp lệ, bản ghi vẫn được lưu với trạng thái chờ/không đọc được. Worker OCR tiếp tục đọc xe đó trong thời gian track còn an toàn; khi có text và ảnh crop hợp lệ, backend cập nhật đúng bản ghi theo `camera_id + track_session_id + vehicle_id` rồi phát lại event WebSocket để UI cập nhật ngay. Nếu bằng chứng text có ích nhưng chưa đủ chốt tuyệt đối, hệ thống lưu trạng thái `uncertain` để hỗ trợ tham khảo mà không đánh tráo thành `confirmed`.

Ảnh `evidence_image_path` là ảnh tổng quan xe vi phạm. Ảnh `license_plate_image_path` là crop biển số. Hai loại ảnh không thay thế vai trò cho nhau. Backend có thể nâng cấp evidence image nếu candidate sau đó tốt hơn ảnh baseline, nhưng không phụ thuộc vào plate confirmed.

## API Và WebSocket

### REST API

| Method | Endpoint | Dùng để |
|---|---|---|
| `GET` | `/api/health` | Kiểm tra backend còn sống. |
| `GET` | `/api/cameras` | Lấy danh sách camera. |
| `GET` | `/api/cameras/{camera_id}` | Lấy chi tiết camera, lane config, validation và UI config. |
| `POST` | `/api/cameras` | Tạo camera mới. |
| `PUT` | `/api/cameras/{camera_id}` | Cập nhật camera và lane config. |
| `DELETE` | `/api/cameras/{camera_id}` | Xóa camera và dữ liệu file liên quan. |
| `GET` | `/api/cameras/{camera_id}/stream-endpoints` | Lấy endpoint video realtime cho browser (`webrtc`/`hls`) + fallback `mjpeg`, kèm `edge_runtime`. |
| `GET` | `/api/cameras/{camera_id}/preview` | Luồng fallback MJPEG cho trình duyệt khi transport realtime không khả dụng. |
| `POST` | `/api/camera/{camera_id}/background-image` | Upload ảnh nền JPG/PNG. |
| `GET` | `/api/camera/{camera_id}/background-image` | Lấy ảnh nền. |
| `DELETE` | `/api/camera/{camera_id}/background-image` | Xóa ảnh nền. |
| `GET` | `/api/violations/evidence/{evidence_path}` | Lấy ảnh bằng chứng. |
| `GET` | `/api/violations/history` | Lấy lịch sử vi phạm. |
| `GET` | `/api/violations/detail/{violation_id}` | Lấy chi tiết một vi phạm theo ID. |
| `GET` | `/api/violations/export` | Tải CSV/XLSX. |
| `GET` | `/api/analytics/dashboard` | Lấy dữ liệu dashboard. |
| `GET` | `/api/stats` | Lấy thống kê dạng count. |
| `GET` | `/api/edge-cameras` | Lấy danh sách edge camera đã discovery. |
| `POST` | `/api/edge-cameras/rescan` | Quét lại edge camera. |
| `GET` | `/api/edge-cameras/{camera_id}` | Lấy trạng thái một edge camera. |
| `POST` | `/api/edge-cameras/{camera_id}/stream/start` | Bật stream edge camera. |
| `POST` | `/api/edge-cameras/{camera_id}/stream/stop` | Tắt stream edge camera. |
| `POST` | `/api/edge-cameras/{camera_id}/stream/restart` | Restart stream edge camera. |
| `POST` | `/api/edge-cameras/{camera_id}/image-tuning/cycle` | Chuyển profile image tuning và restart stream nếu đang bật. |

`GET /api/cameras/{camera_id}` và `GET /api/cameras/{camera_id}/stream-endpoints` hiện có thêm `edge_runtime` để frontend đồng bộ trạng thái `stream_state/profile_change_pending` khi edge chuyển profile image tuning.

### WebSocket

| Endpoint | Dữ liệu |
|---|---|
| `WS /ws/tracks?camera_id=...` | Danh sách xe realtime: khung xe, làn, biển số, hướng, FPS. |
| `WS /ws/violations?camera_id=...` | Vi phạm mới realtime và các update plate/evidence của vi phạm đã có. |

## Kiểm Thử

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

Kiểm tra cú pháp:

```powershell
python -m compileall app
```

## Tài Liệu Liên Quan

- [README tổng quan](../README.md)
- [Frontend README](../frontend/README.md)
- [Phân tích kỹ thuật](../SYSTEM_TECHNICAL_ANALYSIS.md)
