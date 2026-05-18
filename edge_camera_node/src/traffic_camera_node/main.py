from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from . import __version__
from .config import (
    AppConfig,
    load_config,
    next_image_tuning_profile,
    persist_image_tuning_profile,
)
from .hardware.buttons import ButtonCallbacks, ButtonController
from .hardware.gpio_pins import HardwarePins
from .hardware.leds import LedController
from .hardware.system_metrics import collect_metrics
from .hardware.tft_display import DisplayContext, DisplayRenderConfig, TFTDisplayManager
from .health_api import HealthAPIServer
from .identity import (
    RuntimeIdentity,
    load_or_create_identity,
    persist_fallback_ip_if_missing,
)
from .logging_setup import setup_logging
from .network import (
    MdnsPublisher,
    MdnsServiceMetadata,
    build_rtsp_urls,
    detect_ipv4,
    probe_mdns,
)
from .state import NodeState, NodeStatus
from .stream.fps_probe import FpsProbe
from .stream.process_supervisor import ProcessSupervisor
from .stream.rtsp_pipeline import RtspPipeline
from .utils.shell import request_safe_shutdown


class CameraNodeApp:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._logger = setup_logging(config)
        self._stop_event = threading.Event()
        self._image_tuning_lock = threading.Lock()

        self._identity: RuntimeIdentity = load_or_create_identity(config)
        self._state = NodeState(
            identity=self._identity,
            image_tuning_profile=config.image_tuning.profile,
            service_version=__version__,
        )

        self._pins = HardwarePins.from_config(config)
        self._leds = LedController(config.gpio.enabled, self._pins.leds, self._logger)
        self._display = TFTDisplayManager(
            enabled=config.display.enabled,
            update_hz=config.display.update_hz,
            pins=self._pins.display,
            render_config=DisplayRenderConfig(
                width=config.display.width,
                height=config.display.height,
                madctl=config.display.madctl,
                spi_max_speed_hz=config.display.spi_max_speed_hz,
                font_size=config.display.font_size,
            ),
            context=DisplayContext(
                camera_width=config.camera.width,
                camera_height=config.camera.height,
                camera_fps=config.camera.fps,
                image_tuning_profile=config.image_tuning.profile,
            ),
            logger=self._logger,
        )
        self._mdns = MdnsPublisher(self._logger)

        self._pipeline = RtspPipeline(config, self._identity, self._logger)
        self._fps_probe = FpsProbe(config.camera.fps, self._logger)
        self._supervisor = ProcessSupervisor(
            config=config,
            state=self._state,
            pipeline=self._pipeline,
            fps_probe=self._fps_probe,
            logger=self._logger,
        )
        self._health_api = HealthAPIServer(
            config=config,
            state=self._state,
            logger=self._logger,
            set_stream_enabled_callback=lambda enabled: self._supervisor.set_stream_enabled(enabled),
            restart_stream_callback=lambda: self._supervisor.request_restart(),
            cycle_image_tuning_callback=self._cycle_image_tuning_profile,
        )

        callbacks = ButtonCallbacks(
            on_mode=self._on_mode_button,
            on_restart_stream=self._on_restart_button,
            on_safe_shutdown=self._on_shutdown_button,
            on_reset_watchdog=self._on_reset_watchdog_button,
        )
        self._buttons = ButtonController(
            enabled=config.gpio.enabled,
            pins=self._pins.buttons,
            callbacks=callbacks,
            logger=self._logger,
        )

    def run(self) -> int:
        self._logger.info("Traffic camera node starting. version=%s", __version__)
        self._logger.info("Loaded identity: %s", self._identity.to_dict())

        self._install_signal_handlers()
        self._leds.start()
        self._leds.set_booting()
        self._display.start()
        self._buttons.start()
        self._health_api.start()

        self._update_network_state()
        self._state.transition(NodeStatus.ONLINE)
        self._supervisor.start()

        try:
            while not self._stop_event.is_set():
                self._tick()
                time.sleep(self._display.update_interval_s)
        except Exception:
            self._logger.exception("Unhandled exception in main loop.")
            self._state.set_error("Unhandled exception in main loop.")
            return 1
        finally:
            self._shutdown()
        return 0

    def _tick(self) -> None:
        self._update_network_state()
        self._update_metrics()
        snapshot = self._state.snapshot()
        self._display.render_snapshot(snapshot)
        self._leds.apply(NodeStatus(snapshot.status), snapshot.stream_running)

    def _update_network_state(self) -> None:
        net = detect_ipv4(self._config.identity.preferred_interfaces)

        # Chỉ lưu IP dự phòng một lần để URL dự phòng ổn định qua các lần khởi động lại.
        self._identity = persist_fallback_ip_if_missing(
            config=self._config,
            identity=self._identity,
            detected_ip=net.ip_address,
        )
        self._state.update_identity(self._identity)

        # mDNS luôn là URL chính; IP dự phòng chỉ dùng để gỡ lỗi hoặc khi cần khôi phục.
        urls = build_rtsp_urls(identity=self._identity, current_ip=net.ip_address)
        self._state.set_urls(
            primary_rtsp_url=urls.primary_rtsp_url,
            ip_address=net.ip_address,
            ip_fallback_rtsp_url=urls.ip_fallback_rtsp_url,
            interface=net.interface,
        )

        mdns_status, mdns_detail = self._mdns.publish(
            hostname=self._identity.mdns_hostname,
            ip_address=net.ip_address,
            api_port=self._config.health_api.port,
            service_metadata=MdnsServiceMetadata(
                camera_id=self._identity.camera_id,
                node_id=self._identity.node_id,
                mac_address=self._identity.mac_address,
                rtsp_port=self._identity.rtsp_port,
                rtsp_path=self._identity.stream_path,
                ip_address=net.ip_address,
            ),
        )
        if mdns_status == "OK":
            probed_status, probed_detail = probe_mdns(self._identity.mdns_hostname)
            mdns_status = probed_status
            mdns_detail = probed_detail
        self._state.set_mdns_status(mdns_status, mdns_detail)

    def _update_metrics(self) -> None:
        metrics = collect_metrics()
        self._state.set_metrics(
            temperature_c=metrics.temperature_c,
            cpu_percent=metrics.cpu_percent,
            ram_percent=metrics.ram_percent,
            disk_percent=metrics.disk_percent,
            throttled_raw=metrics.throttled_raw,
            undervoltage=metrics.undervoltage,
        )

        if metrics.temperature_c is not None:
            if metrics.temperature_c >= self._config.watchdog.temperature_error_c:
                self._state.set_error(
                    f"Overheat: {metrics.temperature_c:.1f}C >= {self._config.watchdog.temperature_error_c:.1f}C"
                )
            elif metrics.temperature_c >= self._config.watchdog.temperature_warning_c:
                self._state.set_warning(
                    f"High temperature: {metrics.temperature_c:.1f}C >= {self._config.watchdog.temperature_warning_c:.1f}C"
                )

    def _on_mode_button(self) -> None:
        self._display.next_screen()

    def _on_restart_button(self) -> None:
        accepted = self._supervisor.request_restart()
        if not accepted:
            self._logger.warning("Restart request ignored (watchdog latched).")

    def _on_shutdown_button(self) -> None:
        self._logger.warning("SAFE_SHUTDOWN held for 3s. Requesting clean shutdown.")
        self._state.transition(NodeStatus.SHUTTING_DOWN)
        self._stop_event.set()
        result = request_safe_shutdown()
        if not result.ok:
            self._logger.error("Safe shutdown command failed: %s", result.stderr)

    def _on_reset_watchdog_button(self) -> None:
        self._logger.info("RESET_WATCHDOG button pressed.")
        self._supervisor.clear_watchdog_and_restart()

    def _cycle_image_tuning_profile(self) -> dict[str, object]:
        with self._image_tuning_lock:
            previous_profile = self._pipeline.get_image_tuning_profile()
            next_profile = next_image_tuning_profile(previous_profile)
            applied_profile = self._pipeline.set_image_tuning_profile(next_profile)
            persisted_profile = persist_image_tuning_profile(self._config.config_path, applied_profile)
            self._state.set_image_tuning_profile(persisted_profile)
            self._display.set_image_tuning_profile(persisted_profile)

        stream_enabled = self._supervisor.is_stream_enabled()
        restart_requested = False
        if stream_enabled:
            restart_requested = self._supervisor.request_restart()
            if restart_requested:
                self._logger.info(
                    "Image tuning profile changed: %s -> %s (stream restart requested).",
                    previous_profile,
                    persisted_profile,
                )
            else:
                self._logger.warning(
                    "Image tuning profile changed: %s -> %s (restart request rejected).",
                    previous_profile,
                    persisted_profile,
                )
        else:
            self._logger.info(
                "Image tuning profile changed: %s -> %s (stream currently disabled).",
                previous_profile,
                persisted_profile,
            )

        snapshot = self._state.snapshot()
        return {
            "status": "accepted",
            "previous_image_tuning_profile": previous_profile,
            "image_tuning_profile": persisted_profile,
            "stream_enabled": snapshot.stream_enabled,
            "stream_running": snapshot.stream_running,
            "stream_restart_requested": restart_requested,
            "fps_estimate": snapshot.fps_estimate,
        }

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: object) -> None:
            self._logger.info("Received signal %s; stopping service.", signum)
            self._state.transition(NodeStatus.SHUTTING_DOWN)
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _shutdown(self) -> None:
        self._logger.info("Stopping camera node components.")
        self._state.transition(NodeStatus.SHUTTING_DOWN)
        self._health_api.stop()
        self._buttons.stop()
        self._supervisor.stop()
        self._mdns.stop()
        self._display.stop()
        self._leds.stop()
        self._logger.info("Camera node stopped cleanly.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Traffic Camera Node")
    parser.add_argument(
        "--config",
        default="config/settings.json",
        help="Path to camera node JSON config",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    app = CameraNodeApp(config)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
