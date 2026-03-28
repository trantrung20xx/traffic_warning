from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2


def normalize_video_source(source: str) -> str:
    """
    OpenCV VideoCapture accepts RTSP/HTTP URLs as-is.
    For local files on Windows, resolving to an absolute path avoids subtle path issues.
    """
    s = (source or "").strip()
    if not s:
        return s
    lower = s.lower()
    if lower.startswith("rtsp://") or lower.startswith("rtsps://"):
        return s
    if lower.startswith("http://") or lower.startswith("https://"):
        return s
    p = Path(s)
    try:
        if p.exists() and p.is_file():
            return str(p.resolve())
    except OSError:
        pass
    return s


@dataclass
class Frame:
    bgr: np.ndarray
    timestamp_utc_ms: int


class RtspFrameReader:
    """
    Minimal RTSP reader using OpenCV VideoCapture.
    Real deployment may require FFmpeg for robustness; skeleton keeps it simple.
    """

    def __init__(
        self,
        rtsp_url: str,
        *,
        reconnect_delay_s: float = 2.0,
        frame_width: Optional[int] = None,
        frame_height: Optional[int] = None,
    ):
        self.rtsp_url = normalize_video_source(rtsp_url)
        self.reconnect_delay_s = float(reconnect_delay_s)
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._cap: Optional[cv2.VideoCapture] = None
        self._open()

    def _open(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = cv2.VideoCapture(self.rtsp_url)

    def read(self) -> Optional[Frame]:
        if self._cap is None:
            self._open()
        assert self._cap is not None

        ok, frame = self._cap.read()
        if not ok or frame is None:
            time.sleep(self.reconnect_delay_s)
            self._open()
            ok, frame = self._cap.read()
            if not ok or frame is None:
                return None

        if self.frame_width and self.frame_height:
            frame = cv2.resize(frame, (self.frame_width, self.frame_height))

        ts_ms = int(time.time() * 1000)
        return Frame(bgr=frame, timestamp_utc_ms=ts_ms)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = None

