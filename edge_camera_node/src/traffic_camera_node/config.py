from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_IMAGE_TUNING_PROFILES = {
    "normal",
    "low_light",
    "bright_scene",
    "sharpness_safe",
    "disabled",
}


@dataclass(frozen=True)
class CameraConfig:
    width: int = 2560
    height: int = 1440
    fps: int = 25


@dataclass(frozen=True)
class ImageTuningConfig:
    profile: str = "normal"


@dataclass(frozen=True)
class IdentityConfig:
    fixed_camera_id: str | None = None
    fixed_mdns_hostname: str | None = None
    fixed_rtsp_port: int | None = None
    port_range_start: int = 8554
    port_range_end: int = 8654
    persist_file: str = "config/runtime_identity.json"
    mdns_domain: str = "local"
    preferred_interfaces: tuple[str, ...] = ("eth0", "wlan0")


@dataclass(frozen=True)
class StreamConfig:
    bitrate: int = 6_000_000
    mediamtx_binary: str = "mediamtx"
    rpicam_vid_binary: str = "rpicam-vid"
    ffmpeg_binary: str = "ffmpeg"
    udp_sink: str = "udp://127.0.0.1:1234?pkt_size=1316"


@dataclass(frozen=True)
class WatchdogConfig:
    max_restarts_per_window: int = 5
    restart_window_seconds: int = 300
    fps_warning_threshold: int = 15
    temperature_warning_c: float = 75.0
    temperature_error_c: float = 82.0


@dataclass(frozen=True)
class HealthApiConfig:
    host: str = "0.0.0.0"
    port: int = 8088
    allow_restart_endpoint: bool = True
    token: str | None = None


@dataclass(frozen=True)
class ButtonPinsConfig:
    mode: int = 5
    restart_stream: int = 6
    safe_shutdown: int = 13
    reset_watchdog: int = 19


@dataclass(frozen=True)
class LedPinsConfig:
    online: int = 17
    warning: int = 27
    error: int = 22
    streaming: int = 23


@dataclass(frozen=True)
class GpioConfig:
    enabled: bool = True
    buttons: ButtonPinsConfig = field(default_factory=ButtonPinsConfig)
    leds: LedPinsConfig = field(default_factory=LedPinsConfig)


@dataclass(frozen=True)
class DisplayConfig:
    enabled: bool = True
    update_hz: int = 1
    spi_bus: int = 0
    spi_device: int = 0
    dc_pin: int = 25
    reset_pin: int = 24
    backlight_pin: int | None = None


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    log_dir: str = "logs"
    max_bytes: int = 1_048_576
    backup_count: int = 3


@dataclass(frozen=True)
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    image_tuning: ImageTuningConfig = field(default_factory=ImageTuningConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    health_api: HealthApiConfig = field(default_factory=HealthApiConfig)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    config_path: Path = Path("config/settings.json")
    root_dir: Path = Path(".")

    @property
    def persist_identity_path(self) -> Path:
        return (self.root_dir / self.identity.persist_file).resolve()

    @property
    def log_dir_path(self) -> Path:
        return (self.root_dir / self.logging.log_dir).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data


def _as_optional_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    return int(raw)


def _as_interfaces(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ("eth0", "wlan0")
    values = [str(item).strip() for item in raw if str(item).strip()]
    return tuple(values) if values else ("eth0", "wlan0")


def load_config(config_path: Path) -> AppConfig:
    root_dir = config_path.resolve().parent.parent
    raw = _load_json(config_path)

    raw_camera = raw.get("camera", {}) if isinstance(raw.get("camera"), dict) else {}
    raw_tuning = raw.get("image_tuning", {}) if isinstance(raw.get("image_tuning"), dict) else {}
    raw_identity = raw.get("identity", {}) if isinstance(raw.get("identity"), dict) else {}
    raw_stream = raw.get("stream", {}) if isinstance(raw.get("stream"), dict) else {}
    raw_watchdog = raw.get("watchdog", {}) if isinstance(raw.get("watchdog"), dict) else {}
    raw_health = raw.get("health_api", {}) if isinstance(raw.get("health_api"), dict) else {}
    raw_gpio = raw.get("gpio", {}) if isinstance(raw.get("gpio"), dict) else {}
    raw_display = raw.get("display", {}) if isinstance(raw.get("display"), dict) else {}
    raw_logging = raw.get("logging", {}) if isinstance(raw.get("logging"), dict) else {}

    camera = CameraConfig(
        width=int(raw_camera.get("width", 2560)),
        height=int(raw_camera.get("height", 1440)),
        fps=int(raw_camera.get("fps", 25)),
    )

    profile = str(raw_tuning.get("profile", "normal")).strip().lower()
    if profile not in ALLOWED_IMAGE_TUNING_PROFILES:
        raise ValueError(f"Unsupported image_tuning.profile: {profile}")
    image_tuning = ImageTuningConfig(profile=profile)

    identity = IdentityConfig(
        fixed_camera_id=raw_identity.get("fixed_camera_id"),
        fixed_mdns_hostname=raw_identity.get("fixed_mdns_hostname"),
        fixed_rtsp_port=_as_optional_int(raw_identity.get("fixed_rtsp_port")),
        port_range_start=int(raw_identity.get("port_range_start", 8554)),
        port_range_end=int(raw_identity.get("port_range_end", 8654)),
        persist_file=str(raw_identity.get("persist_file", "config/runtime_identity.json")),
        mdns_domain=str(raw_identity.get("mdns_domain", "local")),
        preferred_interfaces=_as_interfaces(raw_identity.get("preferred_interfaces")),
    )

    stream = StreamConfig(
        bitrate=int(raw_stream.get("bitrate", 6_000_000)),
        mediamtx_binary=str(raw_stream.get("mediamtx_binary", "mediamtx")),
        rpicam_vid_binary=str(raw_stream.get("rpicam_vid_binary", "rpicam-vid")),
        ffmpeg_binary=str(raw_stream.get("ffmpeg_binary", "ffmpeg")),
        udp_sink=str(raw_stream.get("udp_sink", "udp://127.0.0.1:1234?pkt_size=1316")),
    )

    watchdog = WatchdogConfig(
        max_restarts_per_window=int(raw_watchdog.get("max_restarts_per_window", 5)),
        restart_window_seconds=int(raw_watchdog.get("restart_window_seconds", 300)),
        fps_warning_threshold=int(raw_watchdog.get("fps_warning_threshold", 15)),
        temperature_warning_c=float(raw_watchdog.get("temperature_warning_c", 75)),
        temperature_error_c=float(raw_watchdog.get("temperature_error_c", 82)),
    )

    health = HealthApiConfig(
        host=str(raw_health.get("host", "0.0.0.0")),
        port=int(raw_health.get("port", 8088)),
        allow_restart_endpoint=bool(raw_health.get("allow_restart_endpoint", True)),
        token=raw_health.get("token"),
    )

    raw_buttons = raw_gpio.get("buttons", {}) if isinstance(raw_gpio.get("buttons"), dict) else {}
    raw_leds = raw_gpio.get("leds", {}) if isinstance(raw_gpio.get("leds"), dict) else {}
    gpio = GpioConfig(
        enabled=bool(raw_gpio.get("enabled", True)),
        buttons=ButtonPinsConfig(
            mode=int(raw_buttons.get("mode", 5)),
            restart_stream=int(raw_buttons.get("restart_stream", 6)),
            safe_shutdown=int(raw_buttons.get("safe_shutdown", 13)),
            reset_watchdog=int(raw_buttons.get("reset_watchdog", 19)),
        ),
        leds=LedPinsConfig(
            online=int(raw_leds.get("online", 17)),
            warning=int(raw_leds.get("warning", 27)),
            error=int(raw_leds.get("error", 22)),
            streaming=int(raw_leds.get("streaming", 23)),
        ),
    )

    display = DisplayConfig(
        enabled=bool(raw_display.get("enabled", True)),
        update_hz=max(1, int(raw_display.get("update_hz", 1))),
        spi_bus=int(raw_display.get("spi_bus", 0)),
        spi_device=int(raw_display.get("spi_device", 0)),
        dc_pin=int(raw_display.get("dc_pin", 25)),
        reset_pin=int(raw_display.get("reset_pin", 24)),
        backlight_pin=_as_optional_int(raw_display.get("backlight_pin")),
    )

    logging = LoggingConfig(
        level=str(raw_logging.get("level", "INFO")).upper(),
        log_dir=str(raw_logging.get("log_dir", "logs")),
        max_bytes=int(raw_logging.get("max_bytes", 1_048_576)),
        backup_count=int(raw_logging.get("backup_count", 3)),
    )

    _validate(camera, identity, watchdog, health, display)
    return AppConfig(
        camera=camera,
        image_tuning=image_tuning,
        identity=identity,
        stream=stream,
        watchdog=watchdog,
        health_api=health,
        gpio=gpio,
        display=display,
        logging=logging,
        config_path=config_path.resolve(),
        root_dir=root_dir,
    )


def _validate(
    camera: CameraConfig,
    identity: IdentityConfig,
    watchdog: WatchdogConfig,
    health: HealthApiConfig,
    display: DisplayConfig,
) -> None:
    if camera.width <= 0 or camera.height <= 0 or camera.fps <= 0:
        raise ValueError("camera width/height/fps must be positive.")
    if identity.port_range_start > identity.port_range_end:
        raise ValueError("identity.port_range_start must be <= identity.port_range_end.")
    if identity.fixed_rtsp_port is not None and identity.fixed_rtsp_port <= 0:
        raise ValueError("identity.fixed_rtsp_port must be positive.")
    if watchdog.max_restarts_per_window < 1 or watchdog.restart_window_seconds < 1:
        raise ValueError("watchdog restart limits must be positive.")
    if health.port <= 0:
        raise ValueError("health_api.port must be positive.")
    if display.update_hz <= 0:
        raise ValueError("display.update_hz must be positive.")
