from __future__ import annotations

import re
import time
from logging import Logger

from ..utils.shell import command_exists, run_command


class FpsProbe:
    """Best-effort RTSP FPS estimator with low-frequency probing."""

    FRAME_RATE_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")

    def __init__(self, target_fps: int, logger: Logger) -> None:
        self._target_fps = float(target_fps)
        self._logger = logger
        self._last_probe_ts = 0.0
        self._last_value = self._target_fps
        self._ffprobe_available = command_exists("ffprobe")

    def estimate(self, rtsp_url: str, stream_running: bool) -> float:
        if not stream_running:
            self._last_value = 0.0
            return 0.0
        if not self._ffprobe_available:
            return self._target_fps

        now = time.monotonic()
        if now - self._last_probe_ts < 15:
            return self._last_value
        self._last_probe_ts = now

        result = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                rtsp_url,
            ],
            timeout=4,
        )
        if not result.ok:
            self._logger.debug("ffprobe failed for FPS estimation: %s", result.stderr)
            return self._last_value

        parsed = self._parse_rate(result.stdout)
        if parsed is not None:
            self._last_value = parsed
        return self._last_value

    def _parse_rate(self, raw: str) -> float | None:
        token = raw.strip().splitlines()[0] if raw.strip() else ""
        match = self.FRAME_RATE_PATTERN.match(token)
        if not match:
            return None
        numerator = float(match.group(1))
        denominator = float(match.group(2))
        if denominator == 0:
            return None
        return numerator / denominator
