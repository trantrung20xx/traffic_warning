from __future__ import annotations

import math
import threading
import textwrap
import time
from dataclasses import dataclass
from logging import Logger

from ..state import HealthSnapshot
from ..utils.time_utils import format_uptime
from .gpio_pins import DisplayPins


class ConsoleRenderer:
    def __init__(self, logger: Logger) -> None:
        self._logger = logger
        self._last_payload = ""

    def render(self, lines: list[str]) -> None:
        payload = "\n".join(lines)
        if payload == self._last_payload:
            return
        self._last_payload = payload
        self._logger.info("Display update:\n%s", payload)

    def close(self) -> None:
        return


class ILI9341Renderer:
    def __init__(
        self,
        pins: DisplayPins,
        width: int,
        height: int,
        madctl: int,
        spi_max_speed_hz: int,
        font_size: int,
        logger: Logger,
    ) -> None:
        self._logger = logger
        self._pins = pins
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._madctl = madctl & 0xFF
        self._font_size = max(8, int(font_size))
        self._spi = None
        self._dc = None
        self._rst = None
        self._backlight = None

        try:
            import spidev
            from gpiozero import DigitalOutputDevice
            from PIL import Image, ImageDraw, ImageFont
        except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
            raise RuntimeError(f"Missing display dependencies: {exc}") from exc

        self._Image = Image
        self._ImageDraw = ImageDraw
        self._ImageFont = ImageFont

        self._spi = spidev.SpiDev()
        self._spi.open(pins.spi_bus, pins.spi_device)
        self._spi.max_speed_hz = max(1, int(spi_max_speed_hz))
        self._spi.mode = 0

        self._dc = DigitalOutputDevice(pins.dc_pin)
        self._rst = DigitalOutputDevice(pins.reset_pin)
        if pins.backlight_pin is not None:
            self._backlight = DigitalOutputDevice(pins.backlight_pin)

        self._font = self._load_font()
        self._line_char_limit = max(
            12,
            (self._width - 8) // max(6, int(self._font_size * 0.62)),
        )
        self._line_height = self._resolve_line_height()
        self._init_panel()

    def _load_font(self) -> object:
        font_candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        for path in font_candidates:
            try:
                return self._ImageFont.truetype(path, self._font_size)
            except Exception:
                continue
        return self._ImageFont.load_default()

    def _resolve_line_height(self) -> int:
        try:
            left, top, right, bottom = self._font.getbbox("Ag")
            height = bottom - top
            width = right - left
            if height > 0 and width > 0:
                return max(12, height + 2)
        except Exception:
            pass
        return max(12, self._font_size + 2)

    def _wrap_line(self, line: str) -> list[str]:
        raw = str(line or "")
        if not raw:
            return [""]

        if ": " in raw:
            key, value = raw.split(": ", 1)
            prefix = f"{key}: "
            available = max(8, self._line_char_limit - len(prefix))
            wrapped_value = textwrap.wrap(
                value,
                width=available,
                break_long_words=True,
                break_on_hyphens=False,
            )
            if not wrapped_value:
                return [prefix.rstrip()]
            continuation_prefix = " " * len(prefix)
            result = [prefix + wrapped_value[0]]
            result.extend(f"{continuation_prefix}{part}" for part in wrapped_value[1:])
            return result

        return textwrap.wrap(
            raw,
            width=self._line_char_limit,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [raw]

    def _command(self, cmd: int) -> None:
        self._dc.off()
        self._spi.xfer2([cmd & 0xFF])

    def _data(self, payload: bytes) -> None:
        self._dc.on()
        chunk_size = 4096
        for idx in range(0, len(payload), chunk_size):
            self._spi.xfer3(payload[idx : idx + chunk_size])

    def _reset(self) -> None:
        self._rst.on()
        time.sleep(0.05)
        self._rst.off()
        time.sleep(0.05)
        self._rst.on()
        time.sleep(0.12)

    def _init_panel(self) -> None:
        self._reset()
        self._command(0x01)  # reset mềm panel
        time.sleep(0.1)
        self._command(0x28)  # tắt hiển thị tạm thời

        self._command(0x3A)  # cấu hình định dạng điểm ảnh
        self._data(bytes([0x55]))  # chế độ màu 16-bit RGB565

        self._command(0x36)  # điều khiển thứ tự truy cập bộ nhớ
        self._data(bytes([self._madctl]))  # thứ tự hàng/cột theo cấu hình panel

        self._command(0x11)  # thoát chế độ ngủ
        time.sleep(0.12)
        self._command(0x29)  # bật hiển thị
        time.sleep(0.05)
        if self._backlight:
            self._backlight.on()

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._command(0x2A)
        self._data(bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._command(0x2B)
        self._data(bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self._command(0x2C)

    @staticmethod
    def _rgb888_to_565_bytes(rgb_bytes: bytes) -> bytes:
        out = bytearray((len(rgb_bytes) // 3) * 2)
        out_idx = 0
        for idx in range(0, len(rgb_bytes), 3):
            r = rgb_bytes[idx]
            g = rgb_bytes[idx + 1]
            b = rgb_bytes[idx + 2]
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[out_idx] = (value >> 8) & 0xFF
            out[out_idx + 1] = value & 0xFF
            out_idx += 2
        return bytes(out)

    def render(self, lines: list[str]) -> None:
        image = self._Image.new("RGB", (self._width, self._height), (0, 0, 0))
        draw = self._ImageDraw.Draw(image)
        flattened_lines: list[str] = []
        for raw in lines:
            flattened_lines.extend(self._wrap_line(raw))

        y = 4
        for line in flattened_lines:
            draw.text(
                (4, y),
                line,
                fill=(255, 255, 255),
                font=self._font,
            )
            y += self._line_height
            if y > self._height - self._line_height:
                break

        rgb_bytes = image.tobytes()
        frame = self._rgb888_to_565_bytes(rgb_bytes)
        self._set_window(0, 0, self._width - 1, self._height - 1)
        self._data(frame)

    def close(self) -> None:
        try:
            if self._backlight:
                self._backlight.off()
        except Exception:
            pass
        for dev in (self._dc, self._rst, self._backlight):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass


@dataclass
class DisplayContext:
    camera_width: int
    camera_height: int
    camera_fps: int
    image_tuning_profile: str


@dataclass(frozen=True)
class DisplayRenderConfig:
    width: int = 320
    height: int = 240
    madctl: int = 0x48
    spi_max_speed_hz: int = 32_000_000
    font_size: int = 14


class TFTDisplayManager:
    def __init__(
        self,
        enabled: bool,
        update_hz: int,
        pins: DisplayPins,
        render_config: DisplayRenderConfig,
        context: DisplayContext,
        logger: Logger,
    ) -> None:
        self._enabled = enabled
        self._update_hz = max(1, update_hz)
        self._pins = pins
        self._render_config = render_config
        self._context = context
        self._logger = logger
        self._renderer: ConsoleRenderer | ILI9341Renderer | None = None
        self._screen_idx = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self._enabled:
            self._renderer = ConsoleRenderer(self._logger)
            self._logger.info("TFT disabled by config; console renderer enabled.")
            return
        try:
            self._renderer = ILI9341Renderer(
                pins=self._pins,
                width=self._render_config.width,
                height=self._render_config.height,
                madctl=self._render_config.madctl,
                spi_max_speed_hz=self._render_config.spi_max_speed_hz,
                font_size=self._render_config.font_size,
                logger=self._logger,
            )
            self._logger.info("ILI9341 renderer initialized successfully.")
        except Exception as exc:
            self._logger.warning("Unable to initialize ILI9341 renderer, fallback to console: %s", exc)
            self._renderer = ConsoleRenderer(self._logger)

    def next_screen(self) -> None:
        with self._lock:
            self._screen_idx = (self._screen_idx + 1) % 4

    def set_image_tuning_profile(self, profile: str) -> None:
        with self._lock:
            self._context.image_tuning_profile = str(profile).strip().lower()

    def render_snapshot(self, snapshot: HealthSnapshot) -> None:
        if self._renderer is None:
            return
        with self._lock:
            idx = self._screen_idx
        lines = self._build_lines(idx, snapshot)
        try:
            self._renderer.render(lines)
        except Exception:  # pragma: no cover - phụ thuộc phần cứng
            self._logger.exception("Display render failed; stream service remains active.")

    def _build_lines(self, idx: int, snapshot: HealthSnapshot) -> list[str]:
        if idx == 0:
            ip = snapshot.ip_address or "N/A"
            fallback = snapshot.ip_fallback_rtsp_url or "N/A"
            path = snapshot.primary_rtsp_url.split("/")[-1] if snapshot.primary_rtsp_url else "N/A"
            port = "N/A"
            if snapshot.primary_rtsp_url and ":" in snapshot.primary_rtsp_url:
                try:
                    port = snapshot.primary_rtsp_url.split(":")[2].split("/")[0]
                except Exception:
                    port = "N/A"
            return [
                "NET + RTSP [1/4]",
                "",
                f"ID: {snapshot.camera_id}",
                f"mDNS: {snapshot.mdns_hostname}",
                f"RTSP: {snapshot.primary_rtsp_url or 'N/A'}",
                f"IP: {ip}",
                f"Fallback: {fallback}",
                "",
                f"Port: {port}",
                f"Path: /{path}",
                f"mDNS status: {snapshot.mdns_status}",
                f"Stream: {'RUNNING' if snapshot.stream_running else 'STOPPED'}",
                f"FPS: {snapshot.fps_estimate:.1f}",
                f"Uptime: {format_uptime(snapshot.uptime_s)}",
            ]

        if idx == 1:
            temp = (
                f"{snapshot.temperature_c:.1f}C"
                if snapshot.temperature_c is not None
                else "N/A"
            )
            uv = "YES" if snapshot.undervoltage else "NO"
            return [
                "HARDWARE [2/4]",
                "",
                f"CPU: {snapshot.cpu_percent if snapshot.cpu_percent is not None else math.nan:.1f}%",
                f"RAM: {snapshot.ram_percent if snapshot.ram_percent is not None else math.nan:.1f}%",
                f"Disk: {snapshot.disk_percent if snapshot.disk_percent is not None else math.nan:.1f}%",
                f"Temp: {temp}",
                f"Throttled: {snapshot.throttled_raw or 'N/A'}",
                f"Undervolt: {uv}",
                "",
                f"Status: {snapshot.status}",
            ]

        if idx == 2:
            return [
                "CAMERA [3/4]",
                "",
                f"Resolution: {self._context.camera_width}x{self._context.camera_height}",
                f"FPS target: {self._context.camera_fps}",
                "Codec: h264",
                "Bitrate: auto default",
                f"Tuning: {self._context.image_tuning_profile}",
                f"Camera: {'OK' if snapshot.stream_running else 'CHECK'}",
            ]

        return [
            "DIAGNOSTICS [4/4]",
            "",
            f"Status: {snapshot.status}",
            f"Error: {snapshot.last_error or 'None'}",
            f"Restarts: {snapshot.restart_count}",
            f"Watchdog: {'LATCHED' if snapshot.watchdog_latched else 'OK'}",
            f"Iface: {snapshot.active_interface or 'N/A'}",
            f"Version: {snapshot.service_version}",
            f"mDNS: {snapshot.mdns_status}",
        ]

    def stop(self) -> None:
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass
        self._renderer = None

    @property
    def update_interval_s(self) -> float:
        return 1.0 / self._update_hz
