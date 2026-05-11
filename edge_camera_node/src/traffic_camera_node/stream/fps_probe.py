from __future__ import annotations

import re
import time
from logging import Logger

from ..utils.shell import command_exists, run_command


class FpsProbe:
    """Best-effort RTSP FPS estimator with low-frequency probing."""

    FRAME_RATE_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")
    SAMPLE_COUNT_PATTERN = re.compile(r"^\s*(\d+)\s*$")

    def __init__(self, target_fps: int, logger: Logger) -> None:
        self._target_fps = float(target_fps)
        self._logger = logger
        self._last_probe_ts = 0.0
        self._last_value = 0.0
        self._probe_interval_s = 5.0
        self._sample_duration_s = 1.5
        self._ffprobe_available = command_exists("ffprobe")

    def estimate(self, rtsp_url: str, stream_running: bool) -> float:
        if not stream_running:
            self._last_value = 0.0
            self._last_probe_ts = 0.0
            return 0.0
        if not self._ffprobe_available:
            return self._target_fps

        now = time.monotonic()
        if now - self._last_probe_ts < self._probe_interval_s:
            return self._last_value
        self._last_probe_ts = now

        result = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-read_intervals",
                f"%+{self._sample_duration_s}",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                rtsp_url,
            ],
            timeout=int(self._sample_duration_s + 4),
        )
        if not result.ok:
            self._logger.debug("ffprobe packet-count failed for FPS estimation: %s", result.stderr)
            return self._last_value

        parsed = self._parse_sample_count(result.stdout)
        if parsed is not None:
            self._last_value = parsed
        return self._last_value

    def _parse_sample_count(self, raw: str) -> float | None:
        for token in raw.strip().splitlines():
            match = self.SAMPLE_COUNT_PATTERN.match(token)
            if not match:
                continue
            sample_count = float(match.group(1))
            return sample_count / max(self._sample_duration_s, 0.1)
        return self._parse_rate(raw)

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
