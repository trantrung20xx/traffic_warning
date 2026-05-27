from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from logging import Logger
from pathlib import Path

from ..config import AppConfig, normalize_image_tuning_profile
from ..identity import RuntimeIdentity, get_port_listeners


@dataclass(frozen=True)
class PipelineHealth:
    # Giá trị True khi toàn bộ tiến trình của đường ống còn sống.
    running: bool
    # Mô tả lỗi ngắn gọn khi đường ống không ổn định.
    detail: str | None = None


class PipelineStartError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        count_toward_watchdog: bool = True,
        retry_after_s: float = 0.5,
    ) -> None:
        super().__init__(message)
        self.count_toward_watchdog = count_toward_watchdog
        self.retry_after_s = max(0.0, float(retry_after_s))


class PipelineMode(str, Enum):
    AUTO = "auto"
    LIBAV_MPEGTS = "libav_mpegts"
    H264 = "h264"


class CameraSourceKind(str, Enum):
    RPI_CSI = "rpi_csi"
    USB_V4L2 = "usb_v4l2"


VIDEO_CAPTURE_CAPABILITIES = (0x00000001, 0x00001000)
V4L2_FPS_PATTERN = re.compile(r"\((?P<fps>\d+(?:\.\d+)?)\s+fps\)")
V4L2_FORMAT_PATTERN = re.compile(r"\[\d+\]:\s+'(?P<format>[^']+)'")
V4L2_SIZE_PATTERN = re.compile(r"Size:\s+Discrete\s+(?P<width>\d+)x(?P<height>\d+)")
V4L2_TO_FFMPEG_INPUT_FORMAT = {
    "MJPG": "mjpeg",
    "YUYV": "yuyv422",
}
FFMPEG_TO_V4L2_INPUT_FORMAT = {value: key for key, value in V4L2_TO_FFMPEG_INPUT_FORMAT.items()}
V4L2_CTRL_LINE_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_]+)\s+0x[0-9a-fA-F]+\s+\((?P<type>[^)]+)\)\s*:\s*(?P<body>.+)$"
)
V4L2_CTRL_VALUE_PATTERN = re.compile(
    r"\b(?P<key>min|max|step|default|value)=(?P<value>[-+]?(?:0x[0-9a-fA-F]+|\d+))\b"
)
V4L2_CTRL_MENU_ITEM_PATTERN = re.compile(r"^\s*(?P<index>-?\d+)\s*:\s*(?P<label>.+?)\s*$")
SUPPORTED_IMAGE_TUNING_PROFILES = (
    "normal",
    "low_light",
    "bright_scene",
    "sharpness_safe",
    "disabled",
)
CSI_IMAGE_TUNING_ARGS: dict[str, list[str]] = {
    "normal": [
        "--ev",
        "0.00",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.00",
        "--contrast",
        "1.03",
        "--sharpness",
        "1.08",
        "--saturation",
        "1.00",
    ],
    "low_light": [
        "--ev",
        "0.30",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.04",
        "--contrast",
        "1.08",
        "--sharpness",
        "1.03",
        "--saturation",
        "0.94",
    ],
    "bright_scene": [
        "--ev",
        "-0.45",
        "--metering",
        "average",
        "--exposure",
        "sport",
        "--awb",
        "daylight",
        "--brightness",
        "-0.06",
        "--contrast",
        "0.98",
        "--sharpness",
        "1.02",
        "--saturation",
        "0.94",
    ],
    "sharpness_safe": [
        "--ev",
        "0.00",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.00",
        "--contrast",
        "1.05",
        "--sharpness",
        "1.14",
        "--saturation",
        "0.98",
    ],
}
USB_V4L2_IMAGE_TUNING_CONTROLS: dict[str, dict[str, int | str]] = {
    "normal": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 50,
        "contrast": 52,
        "saturation": 50,
        "sharpness": 53,
        "gain": 9,
        "backlight_compensation": 0,
    },
    "low_light": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 56,
        "contrast": 56,
        "saturation": 47,
        "sharpness": 51,
        "gain": 15,
        "backlight_compensation": 1,
    },
    "bright_scene": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 44,
        "contrast": 48,
        "saturation": 46,
        "sharpness": 48,
        "gain": 4,
        "backlight_compensation": 0,
    },
    "sharpness_safe": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 50,
        "contrast": 54,
        "saturation": 49,
        "sharpness": 63,
        "gain": 10,
        "backlight_compensation": 0,
    },
}
USB_PERCENT_CONTROLS = {"brightness", "contrast", "saturation", "sharpness", "gain"}
USB_V4L2_CONTROL_ALIASES: dict[str, tuple[str, ...]] = {
    "white_balance_temperature_auto": ("white_balance_automatic",),
    "gain": ("analogue_gain", "analog_gain"),
}


@dataclass(frozen=True)
class UsbCaptureMode:
    width: int
    height: int
    fps: float
    input_format: str


@dataclass(frozen=True)
class V4L2Control:
    name: str
    min_value: int | None = None
    max_value: int | None = None
    default: int | None = None
    value: int | None = None
    control_type: str = ""
    step: int | None = None
    menu_items: dict[int, str] = field(default_factory=dict)


def _normalize_tuning_profile_for_helpers(profile: str) -> str:
    normalized = str(profile or "").strip().lower()
    if normalized in SUPPORTED_IMAGE_TUNING_PROFILES:
        return normalized
    return "normal"


def _image_tuning_args(profile: str) -> list[str]:
    normalized = _normalize_tuning_profile_for_helpers(profile)
    if normalized == "disabled":
        return []
    return list(CSI_IMAGE_TUNING_ARGS[normalized])


def _v4l2_image_tuning_controls(profile: str) -> dict[str, int | str]:
    normalized = _normalize_tuning_profile_for_helpers(profile)
    if normalized == "disabled":
        return {}
    return dict(USB_V4L2_IMAGE_TUNING_CONTROLS[normalized])


class RtspPipeline:
    def __init__(self, config: AppConfig, identity: RuntimeIdentity, logger: Logger) -> None:
        # Lưu cấu hình và định danh dùng xuyên suốt vòng đời đường ống.
        self._config = config
        self._identity = identity
        self._logger = logger

        # Ba tiến trình lõi: MediaMTX server, camera source, ffmpeg phát luồng.
        self._mediamtx_process: subprocess.Popen | None = None
        self._source_process: subprocess.Popen | None = None
        self._ffmpeg_process: subprocess.Popen | None = None

        # Khóa để tuần tự hóa thao tác khởi động/dừng, tránh tranh chấp luồng.
        self._lock = threading.Lock()

        # Port RTSP cố định lấy từ runtime identity đã lưu.
        self._port = identity.rtsp_port

        # Tìm tệp thực thi ngay lúc khởi tạo để báo lỗi sớm nếu thiếu phụ thuộc.
        self._mediamtx_binary = self._resolve_binary(self._config.stream.mediamtx_binary)
        self._ffmpeg_binary = self._resolve_binary(self._config.stream.ffmpeg_binary)
        # Có thể None nếu chạy nguồn USB và máy không có CSI stack.
        self._camera_binary = self._resolve_camera_binary(required=False)

        # Lưu tail stderr để báo đúng nguyên nhân lỗi ngay ở mức INFO/WARNING.
        self._stderr_tails: dict[str, deque[str]] = {
            "mediamtx": deque(maxlen=20),
            "source": deque(maxlen=20),
            "ffmpeg": deque(maxlen=20),
        }

        # Giữ mode đang ổn định để lần restart sau không thử lại mode đã lỗi.
        self._active_mode: PipelineMode | None = None

        # Xác định loại nguồn camera theo cấu hình và tình trạng thực tế.
        self._source_kind = self._resolve_source_kind()
        self._active_usb_device: str | None = None
        self._active_usb_input_format: str | None = None
        self._image_tuning_profile = config.image_tuning.profile

    def _resolve_binary(self, configured_binary: str) -> str:
        found = shutil.which(configured_binary)
        if not found:
            raise RuntimeError(f"Required binary not found: {configured_binary}")
        return found

    def _resolve_camera_binary(self, required: bool = True) -> str | None:
        # Ưu tiên rpicam-vid, dùng libcamera-vid dự phòng nếu hệ thống dùng tên cũ.
        preferred = self._config.stream.rpicam_vid_binary
        found = shutil.which(preferred)
        if found:
            return found

        fallback = shutil.which("libcamera-vid")
        if fallback:
            self._logger.warning("%s not found, falling back to libcamera-vid.", preferred)
            return fallback

        if required:
            raise RuntimeError("Neither rpicam-vid nor libcamera-vid is available.")

        return None

    def _detect_rpi_camera_available(self) -> bool:
        if not self._camera_binary:
            return False
        try:
            proc = subprocess.run(
                [self._camera_binary, "--list-cameras"],
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
        except Exception:
            return False

        output = f"{proc.stdout}\n{proc.stderr}".lower()
        if "no cameras available" in output:
            return False

        # Nếu không báo "no cameras available" thì coi như có camera CSI hợp lệ.
        return bool(output.strip())

    def _resolve_source_kind(self) -> CameraSourceKind:
        configured = self._config.stream.source

        if configured == CameraSourceKind.RPI_CSI.value:
            if not self._camera_binary:
                raise RuntimeError("stream.source=rpi_csi but rpicam-vid/libcamera-vid is not available.")
            return CameraSourceKind.RPI_CSI

        if configured == CameraSourceKind.USB_V4L2.value:
            if not self._list_v4l2_video_devices():
                raise RuntimeError("stream.source=usb_v4l2 but no /dev/video* device is available.")
            return CameraSourceKind.USB_V4L2

        # auto: ưu tiên CSI nếu camera CSI thật sự sẵn sàng, fallback USB nếu có /dev/video*.
        if self._detect_rpi_camera_available():
            self._logger.info("Camera source auto-detected: rpi_csi")
            return CameraSourceKind.RPI_CSI

        if self._list_v4l2_video_devices():
            self._logger.warning(
                "No CSI camera detected by rpicam; falling back to USB source.",
            )
            return CameraSourceKind.USB_V4L2

        raise RuntimeError(
            "No camera source available: no CSI camera detected and no USB video device found under /dev/video*."
        )

    @staticmethod
    def _video_device_sort_key(device_path: Path) -> tuple[int, int | str]:
        suffix = device_path.name[5:]
        if suffix.isdigit():
            return (0, int(suffix))
        return (1, suffix)

    def _list_v4l2_video_devices(self) -> list[Path]:
        return sorted(Path("/dev").glob("video*"), key=self._video_device_sort_key)

    def _read_v4l2_capabilities(self, device_name: str) -> int | None:
        # Một số distro lưu ở .../videoX/capabilities, một số ở .../videoX/device/capabilities.
        candidates = (
            Path("/sys/class/video4linux") / device_name / "capabilities",
            Path("/sys/class/video4linux") / device_name / "device" / "capabilities",
        )
        for path in candidates:
            if not path.exists():
                continue
            try:
                raw = path.read_text(encoding="utf-8").strip().lower()
                if raw.startswith("0x"):
                    raw = raw[2:]
                return int(raw, 16)
            except Exception:
                return None
        return None

    def _is_probably_capture_device(self, device_path: Path) -> bool | None:
        caps = self._read_v4l2_capabilities(device_path.name)
        if caps is None:
            return None
        return any(bool(caps & bit) for bit in VIDEO_CAPTURE_CAPABILITIES)

    def _resolve_usb_device_path(self) -> str:
        configured = self._config.stream.usb_device.strip() or "auto"
        configured_is_auto = configured.lower() == "auto"

        discovered = self._list_v4l2_video_devices()
        if not discovered:
            raise RuntimeError("No /dev/video* device found for USB webcam source.")

        capture_first: list[Path] = []
        unknown_caps: list[Path] = []
        non_capture: list[Path] = []
        for device in discovered:
            is_capture = self._is_probably_capture_device(device)
            if is_capture is True:
                capture_first.append(device)
            elif is_capture is None:
                unknown_caps.append(device)
            else:
                non_capture.append(device)

        ranked = [*capture_first, *unknown_caps, *non_capture]
        if not ranked:
            raise RuntimeError("No usable /dev/video* device found for USB webcam source.")

        preferred = Path(configured) if not configured_is_auto else None
        selected: Path

        if preferred is not None and preferred.exists():
            selected = preferred
            if preferred not in ranked:
                # Nếu path cấu hình không nằm trong sysfs list, vẫn thử dùng theo yêu cầu.
                selected = preferred
            elif self._is_probably_capture_device(preferred) is False:
                selected = ranked[0]
                self._logger.warning(
                    "Configured USB device %s is not a capture node; falling back to %s.",
                    preferred,
                    selected,
                )
        else:
            selected = ranked[0]
            if not configured_is_auto:
                self._logger.warning(
                    "Configured USB device %s not found; falling back to %s.",
                    configured,
                    selected,
                )

        selected_path = str(selected)
        if self._active_usb_device != selected_path:
            self._active_usb_device = selected_path
            self._logger.info("Using USB video device: %s", selected_path)
        return selected_path

    def _build_mediamtx_command(self) -> list[str]:
        # Chạy MediaMTX bằng cấu hình mặc định và biến môi trường ghi đè.
        return [self._mediamtx_binary]

    def _mediamtx_env(self) -> dict[str, str]:
        # Cấu hình MediaMTX bằng biến môi trường để không cần file cấu hình động.
        env = dict(os.environ)
        env["MTX_RTSPADDRESS"] = f":{self._port}"
        env["MTX_RTMP"] = "false"
        env["MTX_HLS"] = "false"
        env["MTX_WEBRTC"] = "false"
        env["MTX_SRT"] = "false"
        # Cho phép publish vào mọi path, tránh lỗi \"path is not configured\" khi không có mediamtx.yml.
        env["MTX_PATHS_ALL_OTHERS_SOURCE"] = "publisher"
        return env

    def _preferred_mode_order(self) -> list[PipelineMode]:
        # USB nguồn đã encode/publish trực tiếp bằng ffmpeg, chỉ cần một mode ổn định.
        if self._source_kind == CameraSourceKind.USB_V4L2:
            return [PipelineMode.LIBAV_MPEGTS]

        configured_mode = PipelineMode(self._config.stream.pipeline_mode)
        if configured_mode != PipelineMode.AUTO:
            return [configured_mode]

        # Auto: ưu tiên mode đang sống ổn định; nếu chưa có thì thử libav trước.
        fallback_order = [PipelineMode.LIBAV_MPEGTS, PipelineMode.H264]
        if self._active_mode is None:
            return fallback_order
        return [self._active_mode, *[mode for mode in fallback_order if mode != self._active_mode]]

    def _build_rpicam_source_command(self, mode: PipelineMode) -> list[str]:
        # rpicam mã hóa và đẩy ra đích UDP nội bộ.
        if not self._camera_binary:
            raise RuntimeError("rpicam source selected but camera binary is unavailable")

        camera = self._config.camera
        stream = self._config.stream
        cmd = [
            self._camera_binary,
            "-n",
            "-t",
            "0",
            "--width",
            str(camera.width),
            "--height",
            str(camera.height),
            "--framerate",
            str(camera.fps),
            "--bitrate",
            str(stream.bitrate),
            "--inline",
            "--low-latency",
            "-o",
            stream.udp_sink,
        ]

        if mode == PipelineMode.LIBAV_MPEGTS:
            cmd[10:10] = ["--codec", "libav", "--libav-format", "mpegts"]
        else:
            cmd[10:10] = ["--codec", "h264"]

        cmd.extend(_image_tuning_args(self._image_tuning_profile))
        return cmd

    def _build_usb_source_command(self, mode: PipelineMode) -> list[str]:
        # USB webcam: ffmpeg đọc V4L2 và publish RTSP trực tiếp vào MediaMTX.
        del mode
        stream = self._config.stream
        usb_device = self._resolve_usb_device_path()
        self._apply_usb_image_tuning(usb_device)
        capture_mode = self._resolve_usb_capture_mode(usb_device)
        stream_name = self._identity.stream_path.lstrip("/")
        target_rtsp = f"rtsp://127.0.0.1:{self._port}/{stream_name}"

        cmd = [
            self._ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
            "-framerate",
            self._format_fps_value(capture_mode.fps),
            "-video_size",
            f"{capture_mode.width}x{capture_mode.height}",
        ]

        if capture_mode.input_format:
            cmd.extend(["-input_format", capture_mode.input_format])

        cmd.extend(
            [
                "-i",
                usb_device,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                str(stream.bitrate),
                "-g",
                str(max(10, int(round(capture_mode.fps)))),
                "-x264-params",
                "repeat-headers=1",
                "-f",
                "rtsp",
                "-rtsp_transport",
                "tcp",
                target_rtsp,
            ]
        )
        return cmd

    @staticmethod
    def _format_fps_value(fps: float) -> str:
        rounded = round(float(fps), 3)
        if abs(rounded - round(rounded)) < 1e-6:
            return str(int(round(rounded)))
        return f"{rounded:g}"

    def _resolve_usb_capture_mode(self, usb_device: str) -> UsbCaptureMode:
        camera = self._config.camera
        configured_input = str(self._config.stream.usb_input_format or "").strip().lower()
        if configured_input == "auto":
            configured_input = ""

        requested_fps = float(camera.fps)
        requested = UsbCaptureMode(
            width=int(camera.width),
            height=int(camera.height),
            fps=requested_fps,
            input_format=configured_input,
        )

        listing = self._query_v4l2_formats(usb_device)
        if not listing:
            return requested

        modes = self._parse_v4l2_modes(listing)
        if not modes:
            return requested

        preferred_formats: tuple[str, ...] = ("MJPG", "YUYV")
        configured_v4l2 = FFMPEG_TO_V4L2_INPUT_FORMAT.get(configured_input)
        if configured_input and configured_v4l2:
            preferred_formats = (configured_v4l2,)
        elif configured_input and not configured_v4l2:
            self._logger.warning(
                "Unsupported configured usb_input_format=%s; auto-selecting supported format.",
                configured_input,
            )

        selected_format, selected_width, selected_height, selected_fps = self._select_usb_mode(
            modes=modes,
            requested_width=int(camera.width),
            requested_height=int(camera.height),
            requested_fps=float(camera.fps),
            preferred_formats=preferred_formats,
        )

        selected_input = V4L2_TO_FFMPEG_INPUT_FORMAT.get(selected_format, "")
        if configured_input and configured_v4l2 and configured_v4l2 != selected_format:
            self._logger.warning(
                "Configured USB input format %s is not available at %sx%s@%sfps; using %s.",
                configured_input,
                selected_width,
                selected_height,
                self._format_fps_value(selected_fps),
                selected_input or selected_format,
            )

        if (
            selected_width != int(camera.width)
            or selected_height != int(camera.height)
            or abs(selected_fps - float(camera.fps)) > 0.5
        ):
            self._logger.warning(
                "Requested USB mode %sx%s@%sfps is unsupported on %s; using %sx%s@%sfps.",
                camera.width,
                camera.height,
                camera.fps,
                usb_device,
                selected_width,
                selected_height,
                self._format_fps_value(selected_fps),
            )

        if selected_input and selected_input != self._active_usb_input_format:
            self._active_usb_input_format = selected_input
            self._logger.info(
                "Using USB input format: %s for %sx%s@%sfps",
                selected_input,
                selected_width,
                selected_height,
                self._format_fps_value(selected_fps),
            )

        return UsbCaptureMode(
            width=selected_width,
            height=selected_height,
            fps=selected_fps,
            input_format=selected_input,
        )

    @staticmethod
    def _parse_v4l2_modes(listing: str) -> list[tuple[str, int, int, float]]:
        modes: list[tuple[str, int, int, float]] = []
        current_format = ""
        current_width: int | None = None
        current_height: int | None = None

        for line in listing.splitlines():
            format_match = V4L2_FORMAT_PATTERN.search(line)
            if format_match:
                current_format = format_match.group("format")
                current_width = None
                current_height = None
                continue

            size_match = V4L2_SIZE_PATTERN.search(line)
            if size_match:
                current_width = int(size_match.group("width"))
                current_height = int(size_match.group("height"))
                continue

            if not current_format or current_width is None or current_height is None:
                continue

            fps_match = V4L2_FPS_PATTERN.search(line)
            if not fps_match:
                continue
            modes.append(
                (
                    current_format,
                    current_width,
                    current_height,
                    float(fps_match.group("fps")),
                )
            )

        return modes

    @staticmethod
    def _select_usb_mode(
        *,
        modes: list[tuple[str, int, int, float]],
        requested_width: int,
        requested_height: int,
        requested_fps: float,
        preferred_formats: tuple[str, ...],
    ) -> tuple[str, int, int, float]:
        candidates = [mode for mode in modes if mode[0] in preferred_formats] if preferred_formats else []
        if not candidates:
            candidates = modes

        def _score(mode: tuple[str, int, int, float]) -> tuple[float, float, float, float]:
            _, width, height, fps = mode
            size_delta = abs(float(width) - float(requested_width)) + abs(float(height) - float(requested_height))
            fps_delta = abs(float(fps) - float(requested_fps))
            # Ưu tiên gần cấu hình mong muốn; nếu bằng nhau thì giữ mode lớn hơn/fps cao hơn.
            return (
                size_delta,
                fps_delta,
                -float(width * height),
                -float(fps),
            )

        return min(candidates, key=_score)

    def _query_v4l2_formats(self, usb_device: str) -> str:
        v4l2_ctl = shutil.which("v4l2-ctl")
        if not v4l2_ctl:
            return ""
        try:
            result = subprocess.run(
                [v4l2_ctl, "--device", usb_device, "--list-formats-ext"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception as exc:
            self._logger.debug("Unable to query V4L2 formats for %s: %s", usb_device, exc)
            return ""
        if result.returncode != 0:
            self._logger.debug("v4l2-ctl format query failed for %s: %s", usb_device, result.stderr.strip())
            return ""
        return result.stdout

    @staticmethod
    def _parse_v4l2_int(raw: str) -> int | None:
        try:
            return int(raw, 0)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_v4l2_controls_listing(cls, listing: str) -> dict[str, V4L2Control]:
        controls: dict[str, V4L2Control] = {}
        current_menu_control: str | None = None

        for line in listing.splitlines():
            ctrl_match = V4L2_CTRL_LINE_PATTERN.match(line)
            if ctrl_match:
                raw_values: dict[str, int] = {}
                for value_match in V4L2_CTRL_VALUE_PATTERN.finditer(ctrl_match.group("body")):
                    parsed = cls._parse_v4l2_int(value_match.group("value"))
                    if parsed is None:
                        continue
                    raw_values[value_match.group("key")] = parsed

                control_type = ctrl_match.group("type").strip().lower()
                control = V4L2Control(
                    name=ctrl_match.group("name"),
                    min_value=raw_values.get("min"),
                    max_value=raw_values.get("max"),
                    default=raw_values.get("default"),
                    value=raw_values.get("value"),
                    control_type=control_type,
                    step=raw_values.get("step"),
                    menu_items={},
                )
                controls[control.name] = control
                current_menu_control = control.name if "menu" in control_type else None
                continue

            if not current_menu_control:
                continue

            menu_match = V4L2_CTRL_MENU_ITEM_PATTERN.match(line)
            if not menu_match:
                if line.strip():
                    current_menu_control = None
                continue

            control = controls.get(current_menu_control)
            if not control:
                continue
            menu_items = dict(control.menu_items)
            menu_items[int(menu_match.group("index"))] = menu_match.group("label").strip()
            controls[current_menu_control] = V4L2Control(
                name=control.name,
                min_value=control.min_value,
                max_value=control.max_value,
                default=control.default,
                value=control.value,
                control_type=control.control_type,
                step=control.step,
                menu_items=menu_items,
            )

        return controls

    def _query_v4l2_controls(self, usb_device: str) -> dict[str, V4L2Control]:
        v4l2_ctl = shutil.which("v4l2-ctl")
        if not v4l2_ctl:
            self._logger.warning("v4l2-ctl not found; skipping USB image tuning for %s.", usb_device)
            return {}

        try:
            result = subprocess.run(
                [v4l2_ctl, "--device", usb_device, "--list-ctrls", "--list-ctrls-menus"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except Exception as exc:
            self._logger.warning("Unable to query V4L2 controls on %s: %s. Skipping tuning.", usb_device, exc)
            return {}

        if result.returncode != 0:
            self._logger.warning(
                "v4l2-ctl control query failed on %s (exit=%s): %s. Skipping tuning.",
                usb_device,
                result.returncode,
                result.stderr.strip(),
            )
            return {}

        return self._parse_v4l2_controls_listing(result.stdout)

    @staticmethod
    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, value))

    @staticmethod
    def _percent_to_v4l2_value(percent: int, minimum: int, maximum: int) -> int:
        value = minimum + round((float(percent) / 100.0) * float(maximum - minimum))
        return RtspPipeline._clamp(int(value), minimum, maximum)

    @staticmethod
    def _resolve_discrete_control_value(control: V4L2Control, desired: int) -> int | None:
        if control.menu_items:
            valid_values = sorted(control.menu_items.keys())
        elif control.min_value is not None and control.max_value is not None:
            valid_values = list(range(control.min_value, control.max_value + 1))
        else:
            return None

        if not valid_values:
            return None
        if desired in valid_values:
            return desired
        return min(valid_values, key=lambda candidate: abs(candidate - desired))

    @staticmethod
    def _resolve_power_line_frequency_value(control: V4L2Control) -> int | None:
        menu_by_index = {index: label.lower() for index, label in control.menu_items.items()}
        if 1 in menu_by_index and "50" in menu_by_index[1]:
            return 1

        for index, label in menu_by_index.items():
            if "50" in label and "hz" in label:
                return index

        return RtspPipeline._resolve_discrete_control_value(control, desired=1)

    def _resolve_v4l2_target_value(
        self,
        control_name: str,
        target: int | str,
        control: V4L2Control,
    ) -> tuple[int | None, str | None]:
        control_type = control.control_type.lower()

        if control_name in USB_PERCENT_CONTROLS:
            if control.min_value is None or control.max_value is None:
                return None, "missing min/max range"
            try:
                percent = int(target)
            except (TypeError, ValueError):
                return None, f"invalid percent target: {target}"
            percent = self._clamp(percent, 0, 100)
            return self._percent_to_v4l2_value(percent, control.min_value, control.max_value), None

        if control_name == "power_line_frequency":
            value = self._resolve_power_line_frequency_value(control)
            if value is None:
                return None, "cannot resolve 50Hz value"
            return value, None

        desired = self._parse_v4l2_int(str(target))
        if desired is None:
            return None, f"invalid discrete target: {target}"

        if "bool" in control_type:
            desired = 1 if desired else 0
            if not control.menu_items and control.min_value is None and control.max_value is None:
                return desired, None
            resolved = self._resolve_discrete_control_value(control, desired=desired)
            if resolved is None:
                return None, "missing bool range/menu values"
            return resolved, None

        if "menu" in control_type:
            resolved = self._resolve_discrete_control_value(control, desired=desired)
            if resolved is None:
                return None, "missing menu values"
            return resolved, None

        if control.min_value is None or control.max_value is None:
            return None, "missing min/max range"
        return self._clamp(desired, control.min_value, control.max_value), None

    def _set_v4l2_control(self, usb_device: str, control_name: str, value: int) -> bool:
        v4l2_ctl = shutil.which("v4l2-ctl")
        if not v4l2_ctl:
            self._logger.warning("v4l2-ctl not found while applying USB tuning; skipping.")
            return False
        try:
            result = subprocess.run(
                [v4l2_ctl, "--device", usb_device, "--set-ctrl", f"{control_name}={value}"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception as exc:
            self._logger.warning("Failed to set USB control %s on %s: %s", control_name, usb_device, exc)
            return False

        if result.returncode != 0:
            self._logger.warning(
                "Failed to set USB control %s=%s on %s (exit=%s): %s",
                control_name,
                value,
                usb_device,
                result.returncode,
                result.stderr.strip(),
            )
            return False
        return True

    def _apply_usb_image_tuning(self, usb_device: str) -> None:
        requested_profile = self._image_tuning_profile
        normalized_profile = _normalize_tuning_profile_for_helpers(requested_profile)
        controls_target = _v4l2_image_tuning_controls(normalized_profile)
        self._logger.info(
            "USB image tuning profile request=%s normalized=%s on %s",
            requested_profile,
            normalized_profile,
            usb_device,
        )

        if not controls_target:
            self._logger.info("USB image tuning disabled; no V4L2 controls applied on %s.", usb_device)
            return

        controls = self._query_v4l2_controls(usb_device)
        if not controls:
            return

        applied_controls: list[str] = []
        skipped_controls: list[str] = []
        for control_name, target in controls_target.items():
            available = controls.get(control_name)
            chosen_control_name = control_name
            if not available:
                for alias in USB_V4L2_CONTROL_ALIASES.get(control_name, ()):
                    available = controls.get(alias)
                    if available:
                        chosen_control_name = alias
                        self._logger.info(
                            "Using USB control alias %s -> %s on %s",
                            control_name,
                            alias,
                            usb_device,
                        )
                        break
            if not available:
                skipped_controls.append(f"{control_name}=missing")
                self._logger.debug("Skipping USB control %s: not supported on %s", control_name, usb_device)
                continue

            resolved_value, skip_reason = self._resolve_v4l2_target_value(control_name, target, available)
            if resolved_value is None:
                skipped_controls.append(f"{control_name}={skip_reason or 'unresolved'}")
                self._logger.debug(
                    "Skipping USB control %s on %s: %s",
                    control_name,
                    usb_device,
                    skip_reason or "cannot resolve target value",
                )
                continue

            if self._set_v4l2_control(usb_device, chosen_control_name, resolved_value):
                applied_controls.append(f"{chosen_control_name}={resolved_value}")

        if applied_controls:
            self._logger.info(
                "Applied USB tuning controls on %s profile=%s: %s",
                usb_device,
                normalized_profile,
                ", ".join(applied_controls),
            )
        if skipped_controls:
            self._logger.info(
                "Skipped USB tuning controls on %s profile=%s: %s",
                usb_device,
                normalized_profile,
                ", ".join(skipped_controls),
            )

    def _build_source_command(self, mode: PipelineMode) -> list[str]:
        if self._source_kind == CameraSourceKind.RPI_CSI:
            return self._build_rpicam_source_command(mode)
        return self._build_usb_source_command(mode)

    def _build_ffmpeg_command(self, mode: PipelineMode) -> list[str]:
        # ffmpeg đọc từ đích UDP và phát vào đường dẫn RTSP cố định.
        stream_name = self._identity.stream_path.lstrip("/")
        target_rtsp = f"rtsp://127.0.0.1:{self._port}/{stream_name}"
        input_format = "mpegts" if mode == PipelineMode.LIBAV_MPEGTS else "h264"

        return [
            self._ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "+genpts+nobuffer+igndts+discardcorrupt",
            "-flags",
            "low_delay",
            "-f",
            input_format,
            "-i",
            self._config.stream.udp_sink,
            "-an",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            target_rtsp,
        ]

    def _clear_stderr_tails(self) -> None:
        for tail in self._stderr_tails.values():
            tail.clear()

    def _read_stderr(self, proc: subprocess.Popen, tag: str) -> None:
        # Đọc stderr liên tục để tránh đầy bộ đệm và giữ log chẩn đoán gần nhất.
        if proc.stderr is None:
            return
        for raw_line in proc.stderr:
            if isinstance(raw_line, bytes):
                text = raw_line.decode("utf-8", errors="replace").strip()
            else:
                text = raw_line.strip()
            if text:
                self._stderr_tails[tag].append(text)
                self._logger.debug("%s: %s", tag, text)

    def _attach_stderr_logger(self, proc: subprocess.Popen, tag: str) -> None:
        # Mỗi tiến trình có một luồng riêng để theo dõi stderr.
        threading.Thread(
            target=self._read_stderr,
            args=(proc, tag),
            daemon=True,
        ).start()

    def _stderr_summary(self, tag: str, max_lines: int = 3) -> str | None:
        lines = list(self._stderr_tails.get(tag, ()))
        if not lines:
            return None
        summary = " | ".join(lines[-max_lines:])
        return summary

    def _classify_start_failure(
        self,
        *,
        source_tag: str,
        base_message: str,
        returncode: int | None,
    ) -> PipelineStartError:
        summary = self._stderr_summary(source_tag)
        details = f"{base_message}"
        if returncode is not None:
            details += f" (exit={returncode})"
        if summary:
            details += f". stderr: {summary}"

        lower = (summary or "").lower()

        # Lỗi thiếu camera/đang bị process khác giữ: không tính watchdog để tránh lock cứng.
        if any(
            token in lower
            for token in (
                "no cameras available",
                "device or resource busy",
                "unable to set controls",
                "failed to acquire camera",
                "cannot open camera",
                "cannot open video device",
                "no such file or directory",
                "input/output error",
            )
        ):
            return PipelineStartError(
                details,
                count_toward_watchdog=False,
                retry_after_s=10.0,
            )

        # Lỗi cấu hình/codec thường không tự hồi trong vài giây; retry thưa để tránh spam.
        if any(
            token in lower
            for token in (
                "unable to open video codec",
                "unrecognized option",
                "unknown option",
                "invalid argument",
                "not supported",
            )
        ):
            return PipelineStartError(
                details,
                count_toward_watchdog=False,
                retry_after_s=15.0,
            )

        return PipelineStartError(details)

    def _terminate_process(self, proc: subprocess.Popen | None, name: str) -> None:
        # Ưu tiên terminate sạch, chỉ kill cứng khi thật sự cần.
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._logger.warning("Force killing %s process", name)
                proc.kill()
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

    def _stop_unlocked(self) -> None:
        # Dừng theo thứ tự tiến trình phát -> nguồn camera -> server.
        self._terminate_process(self._ffmpeg_process, "ffmpeg")
        self._terminate_process(self._source_process, "source")
        self._terminate_process(self._mediamtx_process, "mediamtx")
        self._ffmpeg_process = None
        self._source_process = None
        self._mediamtx_process = None

    def _ensure_port_free(self) -> None:
        # Không tự đổi port để tránh lệch URL với server.
        listeners = get_port_listeners(self._port)
        if listeners:
            raise RuntimeError(
                f"RTSP port {self._port} is occupied (PID(s): {sorted(list(listeners))}). "
                "Resolve conflict manually; port is not changed automatically."
            )

    def _start_once(self, mode: PipelineMode) -> None:
        self._clear_stderr_tails()

        # 1) Khởi động MediaMTX trước để ffmpeg có nơi phát luồng.
        mediamtx_cmd = self._build_mediamtx_command()
        self._logger.info("Starting mediamtx: %s", " ".join(mediamtx_cmd))
        self._mediamtx_process = subprocess.Popen(
            mediamtx_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=self._mediamtx_env(),
        )
        self._attach_stderr_logger(self._mediamtx_process, "mediamtx")
        time.sleep(1.0)
        if self._mediamtx_process.poll() is not None:
            raise self._classify_start_failure(
                source_tag="mediamtx",
                base_message="mediamtx exited immediately after start",
                returncode=self._mediamtx_process.returncode,
            )

        # 2) Khởi động camera source đẩy TS/H264 qua đích UDP.
        source_cmd = self._build_source_command(mode)
        self._logger.info(
            "Starting camera source kind=%s mode=%s: %s",
            self._source_kind.value,
            mode.value,
            " ".join(source_cmd),
        )
        self._source_process = subprocess.Popen(
            source_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._attach_stderr_logger(self._source_process, "source")

        # 3) Với CSI cần ffmpeg publisher; với USB publish trực tiếp từ source.
        if self._source_kind == CameraSourceKind.RPI_CSI:
            ffmpeg_cmd = self._build_ffmpeg_command(mode)
            self._logger.info("Starting ffmpeg publisher (%s): %s", mode.value, " ".join(ffmpeg_cmd))
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._attach_stderr_logger(self._ffmpeg_process, "ffmpeg")
        else:
            self._ffmpeg_process = None

        # Kiểm tra nhanh để phát hiện lỗi vỡ đường ống ngay khi khởi động.
        time.sleep(0.7)
        if self._source_process.poll() is not None:
            raise self._classify_start_failure(
                source_tag="source",
                base_message="camera source exited immediately after start",
                returncode=self._source_process.returncode,
            )
        if self._ffmpeg_process is not None and self._ffmpeg_process.poll() is not None:
            raise self._classify_start_failure(
                source_tag="ffmpeg",
                base_message="ffmpeg publisher exited immediately after start",
                returncode=self._ffmpeg_process.returncode,
            )

    def start(self) -> None:
        with self._lock:
            # Đảm bảo gọi lặp an toàn: đang chạy thì không khởi động lại.
            if self.is_running():
                return

            # Kiểm tra xung đột cổng trước khi tạo tiến trình mới.
            self._ensure_port_free()

            failures: list[PipelineStartError] = []
            attempted_modes = self._preferred_mode_order()

            for idx, mode in enumerate(attempted_modes):
                try:
                    self._start_once(mode)
                    self._active_mode = mode
                    if idx > 0:
                        self._logger.warning(
                            "Pipeline recovered with fallback mode=%s after previous mode failed.",
                            mode.value,
                        )
                    return
                except PipelineStartError as exc:
                    failures.append(exc)
                    self._logger.warning(
                        "Pipeline mode=%s failed to start: %s",
                        mode.value,
                        exc,
                    )
                    self._stop_unlocked()
                    if idx < len(attempted_modes) - 1:
                        self._logger.warning("Trying next pipeline mode...")
                        time.sleep(0.2)
                        continue
                except Exception as exc:
                    failures.append(PipelineStartError(str(exc)))
                    self._stop_unlocked()
                    break

            if not failures:
                raise RuntimeError("Failed to start pipeline for unknown reason.")

            # Ưu tiên ném lỗi cuối cùng vì đó là mode gần với trạng thái mới nhất.
            raise failures[-1]

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def restart(self) -> None:
        # Khởi động lại theo thứ tự rõ ràng: dừng -> đợi ngắn -> khởi động.
        self.stop()
        time.sleep(0.5)
        self.start()

    def set_image_tuning_profile(self, profile: str) -> str:
        normalized = normalize_image_tuning_profile(profile)
        apply_live = False
        active_usb_device = ""
        previous = ""
        with self._lock:
            previous = self._image_tuning_profile
            self._image_tuning_profile = normalized
            apply_live = (
                self._source_kind == CameraSourceKind.USB_V4L2
                and self.is_running()
                and bool(self._active_usb_device)
            )
            active_usb_device = self._active_usb_device or ""

        self._logger.info(
            "Image tuning profile update: %s -> %s (running=%s source=%s)",
            previous,
            normalized,
            self.is_running(),
            self._source_kind.value,
        )

        if not self.is_running():
            self._logger.info("Pipeline is not running; new profile will be applied at next start.")
            return normalized

        if self._source_kind == CameraSourceKind.USB_V4L2 and not active_usb_device:
            self._logger.warning("USB pipeline is running but active USB device is unknown; skip live tuning apply.")
            return normalized

        if apply_live:
            try:
                self._apply_usb_image_tuning(active_usb_device)
                self._logger.info("Live USB tuning apply completed for profile=%s on %s", normalized, active_usb_device)
            except Exception as exc:
                self._logger.warning(
                    "Live USB tuning apply failed for profile=%s on %s: %s",
                    normalized,
                    active_usb_device,
                    exc,
                )
        return normalized

    def get_image_tuning_profile(self) -> str:
        with self._lock:
            return self._image_tuning_profile

    def is_running(self) -> bool:
        # CSI cần 3 tiến trình; USB cần MediaMTX + source (publisher đã tích hợp trong source).
        mediamtx_ok = self._mediamtx_process is not None and self._mediamtx_process.poll() is None
        source_ok = self._source_process is not None and self._source_process.poll() is None
        ffmpeg_ok = True
        if self._source_kind == CameraSourceKind.RPI_CSI:
            ffmpeg_ok = self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None
        return mediamtx_ok and source_ok and ffmpeg_ok

    def health(self) -> PipelineHealth:
        # Trả về tiến trình lỗi đầu tiên để dễ chẩn đoán.
        if self.is_running():
            return PipelineHealth(running=True, detail=None)
        if self._mediamtx_process and self._mediamtx_process.poll() is not None:
            detail = f"mediamtx exited code {self._mediamtx_process.returncode}"
            stderr = self._stderr_summary("mediamtx", max_lines=2)
            if stderr:
                detail = f"{detail}. stderr: {stderr}"
            return PipelineHealth(running=False, detail=detail)
        if self._source_process and self._source_process.poll() is not None:
            detail = f"camera source exited code {self._source_process.returncode}"
            stderr = self._stderr_summary("source", max_lines=2)
            if stderr:
                detail = f"{detail}. stderr: {stderr}"
            return PipelineHealth(running=False, detail=detail)
        if self._ffmpeg_process and self._ffmpeg_process.poll() is not None:
            detail = f"ffmpeg exited code {self._ffmpeg_process.returncode}"
            stderr = self._stderr_summary("ffmpeg", max_lines=2)
            if stderr:
                detail = f"{detail}. stderr: {stderr}"
            return PipelineHealth(running=False, detail=detail)
        if self._source_kind == CameraSourceKind.RPI_CSI and self._ffmpeg_process is None:
            return PipelineHealth(running=False, detail="ffmpeg publisher is not started")
        return PipelineHealth(running=False, detail="Pipeline is not started")
