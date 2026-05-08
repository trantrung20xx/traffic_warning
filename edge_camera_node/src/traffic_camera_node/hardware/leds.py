from __future__ import annotations

from logging import Logger

from ..state import NodeStatus
from .gpio_pins import LedPins


class LedController:
    def __init__(self, enabled: bool, pins: LedPins, logger: Logger) -> None:
        self._enabled = enabled
        self._pins = pins
        self._logger = logger
        self._online = None
        self._warning = None
        self._error = None
        self._streaming = None
        self._initialized = False

    def start(self) -> None:
        if not self._enabled:
            self._logger.info("GPIO LEDs disabled by configuration.")
            return
        try:
            from gpiozero import LED
        except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
            self._logger.warning("gpiozero unavailable; LED controller mock mode: %s", exc)
            return

        try:
            self._online = LED(self._pins.online)
            self._warning = LED(self._pins.warning)
            self._error = LED(self._pins.error)
            self._streaming = LED(self._pins.streaming)
            self._initialized = True
        except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
            self._logger.warning("Failed to initialize LEDs. Falling back to mock mode: %s", exc)
            self.stop()
            return
        self._logger.info(
            "GPIO LEDs initialized (online=%s warning=%s error=%s streaming=%s).",
            self._pins.online,
            self._pins.warning,
            self._pins.error,
            self._pins.streaming,
        )

    def apply(self, status: NodeStatus, stream_running: bool) -> None:
        if not self._initialized:
            return
        try:
            if status == NodeStatus.SHUTTING_DOWN:
                self._all_off()
                return

            self._online.on()
            if stream_running:
                self._streaming.on()
            else:
                self._streaming.off()

            if status == NodeStatus.WARNING:
                self._warning.blink(on_time=0.5, off_time=0.5)
            else:
                self._warning.off()

            if status == NodeStatus.ERROR:
                self._error.blink(on_time=0.2, off_time=0.2)
            else:
                self._error.off()
        except Exception:  # pragma: no cover - phụ thuộc phần cứng
            self._logger.exception("LED update failed.")

    def set_booting(self) -> None:
        if not self._initialized:
            return
        try:
            self._online.blink(on_time=0.2, off_time=0.8)
            self._warning.off()
            self._error.off()
            self._streaming.off()
        except Exception:  # pragma: no cover
            self._logger.exception("Failed to set BOOTING LED pattern.")

    def _all_off(self) -> None:
        for led in (self._online, self._warning, self._error, self._streaming):
            if led is not None:
                led.off()

    def stop(self) -> None:
        self._all_off()
        for led in (self._online, self._warning, self._error, self._streaming):
            try:
                if led is not None:
                    led.close()
            except Exception:
                pass
        self._online = None
        self._warning = None
        self._error = None
        self._streaming = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized
