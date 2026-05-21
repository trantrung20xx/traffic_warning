# Edge Camera Node Cho Raspberry Pi 5

`edge_camera_node` là chương trình chạy trên Raspberry Pi 5 để lấy hình từ camera, mã hóa H.264 và phát RTSP ổn định cho server Traffic Warning đọc bằng OpenCV.

Node này không chạy YOLO, OCR, tracking, logic vi phạm, database, WebSocket của server. Backend chỉ cần đọc RTSP URL như một camera thông thường; frontend quản lý edge node thông qua các API `/api/edge-cameras...` của backend, backend sẽ discovery/proxy sang Health API của Raspberry Pi.

## 1. Luồng Hoạt Động

```text
Camera Pi 5
  -> rpicam-vid/libcamera-vid encode H.264
  -> UDP nội bộ 127.0.0.1
  -> ffmpeg copy video, không encode lại
  -> MediaMTX phát RTSP
  -> Backend Traffic Warning đọc rtsp://...
```

Lý do dùng `rpicam-vid` là để ổn định với camera stack của Raspberry Pi, giữ được tuning ảnh như brightness, contrast, sharpness. `ffmpeg` chỉ làm cầu nối sang RTSP bằng `-c:v copy`, nên không mã hóa lại video.

## 2. URL RTSP Ổn Định

Lần chạy đầu tiên, node tự tạo identity từ MAC address thật, ưu tiên `eth0`, nếu không có thì dùng `wlan0`.

File được tạo và giữ cố định:

```text
config/runtime_identity.json
```

Ví dụ:

```json
{
  "camera_id": "cam_dca632112233",
  "node_id": "dca632112233",
  "mac_address": "dca632112233",
  "interface": "eth0",
  "mdns_hostname": "cam-dca632112233.local",
  "rtsp_port": 8554,
  "stream_path": "/cam_dca632112233",
  "fallback_ip": "192.168.1.50",
  "created_at": "2026-05-09T10:00:00+07:00"
}
```

`rtsp_port` được sinh ổn định trong dải `8554-8654` nếu không cấu hình `identity.fixed_rtsp_port`. RTSP port không liên quan đến Health API port.

URL chính nên dùng:

```text
rtsp://cam-dca632112233.local:8554/cam_dca632112233
```

URL IP fallback:

```text
rtsp://<ip-da-luu>:8554/cam_dca632112233
```

mDNS là lựa chọn chính để tránh phụ thuộc DHCP. IP fallback được lưu để không đổi sau restart, nhưng nên đặt DHCP reservation/static IP nếu muốn dùng IP lâu dài.

## 3. Cấu Hình Chính

File cấu hình:

```text
config/settings.json
```

Cấu hình hiện tại:

```json
{
  "camera": {
    "width": 1920,
    "height": 1080,
    "fps": 30
  },
  "image_tuning": {
    "profile": "normal"
  },
  "gpio": {
    "enabled": true,
    "buttons": {
      "mode": 5,
      "restart_stream": 6,
      "safe_shutdown": 13,
      "reset_watchdog": 19
    },
    "leds": {
      "online": 17,
      "warning": 27,
      "error": 22,
      "streaming": 23
    }
  },
  "display": {
    "enabled": true,
    "update_hz": 1,
    "spi_bus": 0,
    "spi_device": 0,
    "dc_pin": 25,
    "reset_pin": 24,
    "backlight_pin": null,
    "width": 240,
    "height": 320,
    "madctl": "0x48",
    "spi_max_speed_hz": 16000000,
    "font_size": 14
  },
  "stream": {
    "bitrate": 6000000,
    "udp_sink": "udp://127.0.0.1:1234?pkt_size=1316",
    "pipeline_mode": "auto",
    "source": "usb_v4l2",
    "usb_device": "auto",
    "usb_input_format": "auto"
  },
  "watchdog": {
    "fps_warning_threshold": 15
  },
  "health_api": {
    "port": 8088,
    "allow_restart_endpoint": true
  }
}
```

Thông thường chỉ cần chỉnh:

- `camera.width`, `camera.height`, `camera.fps`
- `image_tuning.profile`
- `gpio.buttons.*`, `gpio.leds.*` nếu đổi dây GPIO
- `display.*` nếu đổi chân TFT
- `stream.bitrate` nếu mạng yếu hoặc hình chưa đủ nét
- `stream.pipeline_mode`:
  - `auto`: ưu tiên `libav_mpegts`, tự fallback sang `h264` nếu mode đầu lỗi
  - `libav_mpegts`: ép dùng `--codec libav --libav-format mpegts`
  - `h264`: ép dùng H264 thuần từ `rpicam-vid`
- `stream.source`:
  - `auto`: tự chọn CSI (`rpicam`) nếu có, fallback USB (`/dev/video*`) nếu không có CSI
  - `rpi_csi`: ép dùng camera CSI qua `rpicam-vid`
  - `usb_v4l2`: ép dùng webcam USB qua `ffmpeg -f v4l2`
- `stream.usb_device`: `auto` (khuyến nghị) hoặc đường dẫn cụ thể như `/dev/video2`
- `stream.usb_input_format`: `auto` (khuyến nghị), hoặc `mjpeg`/`yuyv422` tùy webcam

`udp_sink` là điểm trung chuyển nội bộ trong Pi. Giữ mặc định nếu không bị xung đột cổng nội bộ.

`health_api.port` hiện được cố định ở `8088` để frontend luôn gọi đúng API phần cứng. Nếu cấu hình khác `8088`, node sẽ từ chối cấu hình khi khởi động.

## 4. Image Tuning

Các profile hỗ trợ:

- `normal`: mặc định, giữ hình tự nhiên và ổn định.
- `low_light`: tăng nhẹ sáng/contrast cho cảnh tối.
- `bright_scene`: giảm nhẹ sáng để tránh cháy hình.
- `sharpness_safe`: tăng nét nhẹ, không dùng xử lý nặng.
- `disabled`: không thêm tham số tuning.

Không dùng AI enhancement, OCR, CLAHE mạnh hoặc xử lý từng frame trên Pi.

## 5. GPIO Mặc Định

TFT ILI9341:

- SPI0 MOSI: `GPIO10`
- SPI0 MISO: `GPIO9` nếu module cần đọc
- SPI0 SCLK: `GPIO11`
- SPI0 CE0: `GPIO8`
- DC: `GPIO25`
- RST: `GPIO24`
- Backlight: optional, mặc định `null`

Buttons:

- MODE: `GPIO5`
- RESTART_STREAM: `GPIO6`
- SAFE_SHUTDOWN: `GPIO13`
- RESET_WATCHDOG: `GPIO19`

LEDs:

- ONLINE: `GPIO17`
- WARNING: `GPIO27`
- ERROR: `GPIO22`
- STREAMING: `GPIO23`

Chức năng nút:

- MODE: đổi trang TFT.
- RESTART_STREAM: yêu cầu supervisor restart pipeline RTSP.
- SAFE_SHUTDOWN: nhấn giữ 3 giây để shutdown Raspberry Pi an toàn.
- RESET_WATCHDOG: xóa watchdog latched và thử chạy lại stream.

Trạng thái của đèn:

- ONLINE: nhấp nháy khi boot, sáng khi service đang chạy.
- STREAMING: sáng khi RTSP pipeline đang chạy.
- WARNING: nhấp nháy chậm khi FPS thấp, mDNS lỗi hoặc stream dừng tạm thời.
- ERROR: nhấp nháy nhanh khi lỗi nghiêm trọng hoặc watchdog đã latch.
- SHUTTING_DOWN: tắt toàn bộ đèn.

Lưu ý an toàn:

- Không đưa 5V vào chân GPIO signal.
- LED phải có điện trở hạn dòng.
- Nút nhấn dùng pull-up nội, nhấn xuống GND.
- Quạt 5V cấp từ 5V/GND, không điều khiển trực tiếp bằng GPIO nếu không có transistor/MOSFET.

## 6. Cài Trên Raspberry Pi 5

Yêu cầu:

- Raspberry Pi OS 64-bit khuyến nghị.
- Camera đã gắn đúng cổng và được Pi nhận.
- Pi có mạng LAN/Wi-Fi.
- Nguồn USB-C 5V 5A.

### Bước 1: Lấy source

Nếu dùng git:

```bash
cd /home/pi
git clone https://github.com/trantrung20xx/traffic_warning.git
cd /home/pi/traffic_warning/edge_camera_node
```

Nếu copy thủ công, đặt thư mục tại ví dụ:

```text
/home/pi/edge_camera_node
```

Sau đó:

```bash
cd /home/pi/edge_camera_node
```

### Bước 2: Bật Camera, SPI, I2C

```bash
bash scripts/enable_interfaces.sh
sudo reboot
```

Sau khi Pi khởi động lại:

```bash
cd /home/pi/edge_camera_node
```

### Bước 3: Kiểm tra môi trường và tạo config nếu thiếu

```bash
bash scripts/check_environment.sh
```

Script này kiểm tra Python, MediaMTX, ffmpeg, rpicam-vid, Avahi, mạng và tạo `config/settings.json` nếu file chưa có.

### Bước 4: Cài dependency

```bash
bash scripts/install_dependencies.sh
```

Script sẽ cài apt package, MediaMTX, tạo `.venv` và cài Python dependencies.

### Bước 5: Chạy test

```bash
source .venv/bin/activate
pytest -q
```

### Bước 6: Chạy thử thủ công

```bash
bash scripts/run_dev.sh
```

Xem log trên terminal. Trên màn hình TFT sẽ có camera ID, mDNS URL, IP fallback, trạng thái stream, FPS và nhiệt độ.

### Bước 7: Cài systemd auto-start

```bash
bash scripts/install_service.sh
```

Kiểm tra service:

```bash
sudo systemctl status traffic-camera-node.service
```

Xem log realtime:

```bash
journalctl -u traffic-camera-node.service -f
```

Restart service:

```bash
sudo systemctl restart traffic-camera-node.service
```

Gỡ service:

```bash
bash scripts/uninstall_service.sh
```

## 7. Kiểm Tra RTSP Và mDNS

Lấy identity:

```bash
cat config/runtime_identity.json
```

Kiểm tra mDNS:

```bash
ping cam-dca632112233.local
avahi-resolve -n cam-dca632112233.local
avahi-browse -a
```

Kiểm tra RTSP bằng ffprobe hoặc VLC:

```bash
ffprobe rtsp://cam-dca632112233.local:8554/cam_dca632112233
```

Nếu `.local` không resolve được, dùng IP fallback hiển thị trên TFT hoặc trong `/health`.

## 8. Health API

Health API chạy cố định ở:

```text
http://<mdns-hostname>:8088
```

Backend dùng cùng chuẩn này khi discovery/proxy edge camera. Frontend không cần gọi trực tiếp Raspberry Pi; frontend gọi backend `/api/edge-cameras...`.

Endpoint:

- `GET /health`
- `GET /api/health`
- `GET /api/identity`
- `POST /api/stream/start`
- `POST /api/stream/stop`
- `POST /api/stream/restart`
- `POST /api/image-tuning/cycle`

Ví dụ:

```bash
curl http://cam-dca632112233.local:8088/health
curl http://cam-dca632112233.local:8088/api/identity
curl -X POST http://cam-dca632112233.local:8088/api/stream/stop
curl -X POST http://cam-dca632112233.local:8088/api/stream/start
curl -X POST http://cam-dca632112233.local:8088/api/stream/restart
curl -X POST http://cam-dca632112233.local:8088/api/image-tuning/cycle
```

`/api/stream/restart` chỉ restart RTSP pipeline do supervisor quản lý, không restart toàn bộ systemd service. Service vẫn dùng systemd để tự chạy lại nếu process edge node thoát.

Nếu muốn khóa endpoint điều khiển:

```json
{
  "health_api": {
    "port": 8088,
    "allow_restart_endpoint": false
  }
}
```

Nếu muốn dùng token:

```json
{
  "health_api": {
    "port": 8088,
    "allow_restart_endpoint": true,
    "token": "doi_chuoi_bi_mat_o_day"
  }
}
```

Khi có token, đặt token trên query string của lệnh POST:

```bash
curl -X POST "http://cam-dca632112233.local:8088/api/stream/restart?token=doi_chuoi_bi_mat_o_day"
```

## 9. Tích Hợp Với Server Traffic Warning

Không cần sửa backend server. Trong cấu hình camera của server, nhập RTSP URL chính:

```json
{
  "camera_id": "cam_dca632112233",
  "rtsp_url": "rtsp://cam-dca632112233.local:8554/cam_dca632112233",
  "frame_width": 1920,
  "frame_height": 1080
}
```

Nếu môi trường không resolve được `.local`, dùng IP fallback:

```json
{
  "camera_id": "cam_dca632112233",
  "rtsp_url": "rtsp://192.168.1.50:8554/cam_dca632112233",
  "frame_width": 1920,
  "frame_height": 1080
}
```

`camera_id` trong server là định danh phần mềm dùng cho lane config, evidence và thống kê; nó có thể khác `camera_id` do edge node tự sinh. Liên kết đến phần cứng được xác định qua `rtsp_url`.

Frontend có màn hình `Edge cameras` để xem health, identity, bật/tắt stream, restart stream và đổi image tuning từ xa. Frontend gọi backend, backend proxy sang Health API trên Raspberry Pi.

## 10. Lỗi Thường Gặp

Không thấy camera:

```bash
rpicam-hello --list-cameras
rpicam-vid -t 5000 -o test.h264
```

Không resolve được `.local`:

```bash
sudo systemctl status avahi-daemon
sudo systemctl restart avahi-daemon
avahi-resolve -n <hostname>.local
```

Windows không resolve `.local`: dùng IP fallback hoặc cài Bonjour/Avahi-compatible resolver trên máy Windows.

Không mở được RTSP:

```bash
sudo systemctl status traffic-camera-node.service
journalctl -u traffic-camera-node.service -n 100 --no-pager
ss -lntp | grep 8554
```

Port bị chiếm: node sẽ báo lỗi và không tự đổi port để tránh server mất đồng bộ URL.

FPS thấp:

- Giảm `camera.fps`.
- Giảm `camera.width`/`camera.height`.
- Kiểm tra nguồn 5V 5A và nhiệt độ Pi.
- Kiểm tra mạng nếu server đọc qua Wi-Fi.

Ảnh quá tối hoặc cháy sáng:

- Thử `image_tuning.profile = "low_light"` khi tối.
- Thử `image_tuning.profile = "bright_scene"` khi nắng gắt.
- Tránh đổi profile liên tục ngoài thực địa.

TFT/GPIO lỗi:

- Service vẫn tiếp tục phát stream.
- Kiểm tra quyền GPIO/SPI/I2C và wiring.

Service restart liên tục:

```bash
journalctl -u traffic-camera-node.service -f
systemctl show traffic-camera-node.service -p NRestarts
```

Watchdog latched:

- Khi pipeline chết/no-frame quá nhiều lần trong cửa sổ restart, watchdog sẽ latch để tránh restart vô hạn.
- Nhấn nút RESET_WATCHDOG hoặc gọi lệnh restart sau khi clear thủ công nếu đã xử lý nguyên nhân.
- Kiểm tra `watchdog_latched`, `restart_count`, `last_error` trong `/api/health`.

## 11. Ghi Nhớ Khi Triển Khai

- URL chính là mDNS URL.
- Reboot không đổi `camera_id`, hostname, port, stream path.
- RTSP port nằm trong dải `8554-8654`; Health API port cố định `8088`.
- Không xóa `config/runtime_identity.json` nếu camera đã được khai báo trên server.
- Nếu thay Pi hoặc thay interface mạng, MAC có thể đổi và identity mới sẽ được tạo.
- Edge node được thiết kế để boot lên là tự chạy lại.
