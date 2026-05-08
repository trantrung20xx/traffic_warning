# Edge Camera Node Cho Raspberry Pi 5

`edge_camera_node` là chương trình chạy trên Raspberry Pi 5 để lấy hình từ camera, mã hóa H.264 và phát RTSP ổn định cho server Traffic Warning đọc bằng OpenCV.

Node này không chạy YOLO, OCR, tracking, logic vi phạm, database, WebSocket của server. Backend và frontend server hiện có chỉ cần dùng RTSP URL.

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
  "mdns_hostname": "cam-dca632112233.local",
  "rtsp_port": 8554,
  "stream_path": "/cam_dca632112233"
}
```

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
    "width": 2560,
    "height": 1440,
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
    "backlight_pin": null
  },
  "stream": {
    "bitrate": 6000000,
    "udp_sink": "udp://127.0.0.1:1234?pkt_size=1316"
  },
  "watchdog": {
    "fps_warning_threshold": 15
  },
  "health_api": {
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

`udp_sink` là điểm trung chuyển nội bộ trong Pi. Giữ mặc định nếu không bị xung đột cổng nội bộ.

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

Quy tắc an toàn:

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

Health API chạy mặc định ở:

```text
http://<mdns-hostname>:8088
```

Endpoint:

- `GET /health`
- `GET /identity`
- `GET /stream/start`
- `GET /stream/stop`
- `GET /restart-service`

Ví dụ:

```bash
curl http://cam-dca632112233.local:8088/health
curl http://cam-dca632112233.local:8088/identity
curl http://cam-dca632112233.local:8088/stream/stop
curl http://cam-dca632112233.local:8088/stream/start
curl http://cam-dca632112233.local:8088/restart-service
```

`/restart-service` chỉ tự chạy lại khi chương trình đang chạy dưới systemd. Service hiện dùng `Restart=always`, nên process thoát sạch rồi systemd khởi động lại sau `RestartSec=5`.

Nếu muốn khóa endpoint điều khiển:

```json
{
  "health_api": {
    "allow_restart_endpoint": false
  }
}
```

Nếu muốn dùng token:

```json
{
  "health_api": {
    "allow_restart_endpoint": true,
    "token": "doi_chuoi_bi_mat_o_day"
  }
}
```

Khi có token, gọi:

```bash
curl "http://cam-dca632112233.local:8088/restart-service?token=doi_chuoi_bi_mat_o_day"
```

## 9. Tích Hợp Với Server Traffic Warning

Không cần sửa backend server. Trong cấu hình camera của server, nhập RTSP URL chính:

```json
{
  "camera_id": "cam_dca632112233",
  "rtsp_url": "rtsp://cam-dca632112233.local:8554/cam_dca632112233",
  "frame_width": 2560,
  "frame_height": 1440
}
```

Nếu môi trường không resolve được `.local`, dùng IP fallback:

```json
{
  "camera_id": "cam_dca632112233",
  "rtsp_url": "rtsp://192.168.1.50:8554/cam_dca632112233",
  "frame_width": 2560,
  "frame_height": 1440
}
```

Frontend quản lý camera có popup edge node để xem health, identity, bật/tắt stream và restart service từ xa. Popup gọi trực tiếp Health API trên Raspberry Pi, không đi qua backend server.

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

## 11. Ghi Nhớ Khi Triển Khai

- URL chính là mDNS URL.
- Reboot không đổi `camera_id`, hostname, port, stream path.
- Không xóa `config/runtime_identity.json` nếu camera đã được khai báo trên server.
- Nếu thay Pi hoặc thay interface mạng, MAC có thể đổi và identity mới sẽ được tạo.
- Edge node được thiết kế để boot lên là tự chạy lại.
