from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig


@dataclass(frozen=True)
class ButtonPins:
    mode: int
    restart_stream: int
    safe_shutdown: int
    reset_watchdog: int


@dataclass(frozen=True)
class LedPins:
    online: int
    warning: int
    error: int
    streaming: int


@dataclass(frozen=True)
class DisplayPins:
    spi_bus: int
    spi_device: int
    dc_pin: int
    reset_pin: int
    backlight_pin: int | None


@dataclass(frozen=True)
class HardwarePins:
    buttons: ButtonPins
    leds: LedPins
    display: DisplayPins

    @staticmethod
    def from_config(config: AppConfig) -> "HardwarePins":
        gpio = config.gpio
        display = config.display
        return HardwarePins(
            buttons=ButtonPins(
                mode=gpio.buttons.mode,
                restart_stream=gpio.buttons.restart_stream,
                safe_shutdown=gpio.buttons.safe_shutdown,
                reset_watchdog=gpio.buttons.reset_watchdog,
            ),
            leds=LedPins(
                online=gpio.leds.online,
                warning=gpio.leds.warning,
                error=gpio.leds.error,
                streaming=gpio.leds.streaming,
            ),
            display=DisplayPins(
                spi_bus=display.spi_bus,
                spi_device=display.spi_device,
                dc_pin=display.dc_pin,
                reset_pin=display.reset_pin,
                backlight_pin=display.backlight_pin,
            ),
        )
