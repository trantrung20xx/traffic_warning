from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from typing import Callable

from ..utils.debounce import Debouncer
from .gpio_pins import ButtonPins


@dataclass
class ButtonCallbacks:
    on_mode: Callable[[], None]
    on_restart_stream: Callable[[], None]
    on_safe_shutdown: Callable[[], None]
    on_reset_watchdog: Callable[[], None]


class ButtonController:
    def __init__(
        self,
        enabled: bool,
        pins: ButtonPins,
        callbacks: ButtonCallbacks,
        logger: Logger,
    ) -> None:
        self._enabled = enabled
        self._pins = pins
        self._callbacks = callbacks
        self._logger = logger

        self._mode_button = None
        self._restart_button = None
        self._shutdown_button = None
        self._reset_button = None
        self._debouncers = {
            "mode": Debouncer(0.2),
            "restart": Debouncer(0.2),
            "reset": Debouncer(0.2),
        }
        self._initialized = False

    def start(self) -> None:
        if not self._enabled:
            self._logger.info("GPIO buttons disabled by configuration.")
            return
        try:
            from gpiozero import Button
        except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
            self._logger.warning("gpiozero unavailable; button controller mock mode: %s", exc)
            return

        try:
            self._mode_button = Button(self._pins.mode, pull_up=True, bounce_time=0.05)
            self._restart_button = Button(
                self._pins.restart_stream, pull_up=True, bounce_time=0.05
            )
            self._shutdown_button = Button(
                self._pins.safe_shutdown, pull_up=True, bounce_time=0.05, hold_time=3.0
            )
            self._reset_button = Button(
                self._pins.reset_watchdog, pull_up=True, bounce_time=0.05
            )
        except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
            self._logger.warning("Failed to initialize GPIO buttons. Falling back to mock mode: %s", exc)
            self.stop()
            return

        self._mode_button.when_pressed = self._handle_mode
        self._restart_button.when_pressed = self._handle_restart
        self._shutdown_button.when_held = self._handle_shutdown_hold
        self._reset_button.when_pressed = self._handle_reset
        self._initialized = True
        self._logger.info(
            "GPIO buttons initialized (mode=%s restart=%s shutdown=%s reset=%s).",
            self._pins.mode,
            self._pins.restart_stream,
            self._pins.safe_shutdown,
            self._pins.reset_watchdog,
        )

    def _handle_mode(self) -> None:
        if self._debouncers["mode"].should_accept():
            self._callbacks.on_mode()

    def _handle_restart(self) -> None:
        if self._debouncers["restart"].should_accept():
            self._callbacks.on_restart_stream()

    def _handle_shutdown_hold(self) -> None:
        self._callbacks.on_safe_shutdown()

    def _handle_reset(self) -> None:
        if self._debouncers["reset"].should_accept():
            self._callbacks.on_reset_watchdog()

    def stop(self) -> None:
        for button in (
            self._mode_button,
            self._restart_button,
            self._shutdown_button,
            self._reset_button,
        ):
            try:
                if button is not None:
                    button.close()
            except Exception:
                pass
        self._mode_button = None
        self._restart_button = None
        self._shutdown_button = None
        self._reset_button = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized
