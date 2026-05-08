from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from ..utils.shell import command_exists, run_command


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    temperature_c: float | None
    throttled_raw: str | None
    undervoltage: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "ram_percent": self.ram_percent,
            "disk_percent": self.disk_percent,
            "temperature_c": self.temperature_c,
            "throttled_raw": self.throttled_raw,
            "undervoltage": self.undervoltage,
        }


def _read_temperature() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
        for entries in temps.values():
            if entries:
                current = entries[0].current
                if current is not None:
                    return float(current)
    except Exception:
        pass

    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal_path.exists():
        try:
            raw = thermal_path.read_text(encoding="utf-8").strip()
            return float(raw) / 1000.0
        except Exception:
            return None
    return None


def _read_throttled_status() -> tuple[str | None, bool | None]:
    if not command_exists("vcgencmd"):
        return None, None
    result = run_command(["vcgencmd", "get_throttled"], timeout=3)
    if not result.ok:
        return None, None
    raw = result.stdout.strip()
    if "=" not in raw:
        return raw, None
    hex_value = raw.split("=", maxsplit=1)[-1]
    try:
        value = int(hex_value, 16)
    except ValueError:
        return raw, None
    undervoltage_now = bool(value & 0x1)
    undervoltage_happened = bool(value & 0x10000)
    return raw, undervoltage_now or undervoltage_happened


def collect_metrics() -> SystemMetrics:
    cpu = float(psutil.cpu_percent(interval=None))
    ram = float(psutil.virtual_memory().percent)
    disk = float(psutil.disk_usage("/").percent)
    temp = _read_temperature()
    throttled_raw, undervoltage = _read_throttled_status()
    return SystemMetrics(
        cpu_percent=cpu,
        ram_percent=ram,
        disk_percent=disk,
        temperature_c=temp,
        throttled_raw=throttled_raw,
        undervoltage=undervoltage,
    )
