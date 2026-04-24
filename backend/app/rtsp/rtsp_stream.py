from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


NETWORK_SOURCE_PREFIXES = ("rtsp://", "rtsps://", "http://", "https://")


@dataclass(frozen=True)
class VideoSourceDescriptor:
    normalized_source: str
    is_local_file: bool


def _describe_video_source(source: str) -> VideoSourceDescriptor:
    s = (source or "").strip()
    if not s:
        return VideoSourceDescriptor(normalized_source="", is_local_file=False)

    if s.lower().startswith(NETWORK_SOURCE_PREFIXES):
        return VideoSourceDescriptor(normalized_source=s, is_local_file=False)

    try:
        local_path = Path(s)
        if local_path.exists() and local_path.is_file():
            return VideoSourceDescriptor(normalized_source=str(local_path.resolve()), is_local_file=True)
    except OSError:
        pass

    return VideoSourceDescriptor(normalized_source=s, is_local_file=False)


def is_local_video_file(source: str) -> bool:
    """
    Xác định source là file video cục bộ để áp dụng nhịp đọc theo FPS gốc.
    """
    return _describe_video_source(source).is_local_file


@dataclass
class Frame:
    bgr: np.ndarray
    timestamp_utc_ms: int


class RtspFrameReader:
    """
    Bộ đọc RTSP tối giản dùng `cv2.VideoCapture`.
    Môi trường triển khai thực tế có thể cần FFmpeg để ổn định hơn, nhưng ở đây giữ giải pháp gọn nhẹ.
    """

    def __init__(
        self,
        rtsp_url: str,
        *,
        reconnect_delay_s: float = 2.0,
        frame_width: Optional[int] = None,
        frame_height: Optional[int] = None,
    ):
        source_descriptor = _describe_video_source(rtsp_url)
        self.rtsp_url = source_descriptor.normalized_source
        self._source_is_local_file = source_descriptor.is_local_file
        self.reconnect_delay_s = float(reconnect_delay_s)
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._cap_lock = threading.Lock()
        self._latest_lock = threading.Lock()
        self._cap: Optional[cv2.VideoCapture] = None
        self._latest_frame: Optional[Frame] = None
        self._latest_frame_seq: int = 0
        self._last_delivered_seq: int = 0
        self._file_frame_interval_s: Optional[float] = None
        self._file_next_deadline_s: Optional[float] = None
        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"rtsp-reader-{id(self)}",
            daemon=True,
        )
        self._reader_thread.start()

    def _open_locked(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = cv2.VideoCapture(self.rtsp_url)
        cap = self._cap
        if cap is not None:
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                try:
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1000)
                except Exception:
                    pass
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                try:
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)
                except Exception:
                    pass
            if self._source_is_local_file:
                self._configure_file_playback_pacing(cap)
            else:
                self._file_frame_interval_s = None
                self._file_next_deadline_s = None

    def _configure_file_playback_pacing(self, cap: cv2.VideoCapture) -> None:
        fps = 0.0
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            fps = 0.0
        if not (1.0 <= fps <= 240.0):
            fps = 25.0
        self._file_frame_interval_s = 1.0 / fps
        self._file_next_deadline_s = time.perf_counter()

    def _pace_file_playback_if_needed(self) -> bool:
        interval = self._file_frame_interval_s
        if not self._source_is_local_file or interval is None:
            return True

        now = time.perf_counter()
        deadline = self._file_next_deadline_s
        if deadline is None:
            deadline = now

        deadline += interval
        self._file_next_deadline_s = deadline

        sleep_s = deadline - now
        if sleep_s > 0:
            if self._stop_event.wait(sleep_s):
                return False
        elif sleep_s < -1.0:
            # Nếu backend bị trễ quá xa, reset deadline để tránh dồn sai số.
            self._file_next_deadline_s = now
        return True

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._cap_lock:
                if self._cap is None:
                    self._open_locked()
                cap = self._cap

            if cap is None:
                if self._stop_event.wait(self.reconnect_delay_s):
                    break
                continue

            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                if self._stop_event.wait(self.reconnect_delay_s):
                    break
                with self._cap_lock:
                    self._open_locked()
                continue

            if self.frame_width and self.frame_height:
                frame = cv2.resize(frame, (self.frame_width, self.frame_height))

            snapshot = Frame(bgr=frame, timestamp_utc_ms=int(time.time() * 1000))
            with self._latest_lock:
                self._latest_frame = snapshot
                self._latest_frame_seq += 1
            if not self._pace_file_playback_if_needed():
                break

    def read(self, *, only_new: bool = True) -> Optional[Frame]:
        """Đọc frame mới nhất đã được thread nền lấy về; không block vòng lặp async."""
        with self._latest_lock:
            latest = self._latest_frame
            latest_seq = self._latest_frame_seq
        if latest is None:
            return None
        if only_new and latest_seq == self._last_delivered_seq:
            return None
        self._last_delivered_seq = latest_seq
        return Frame(bgr=latest.bgr.copy(), timestamp_utc_ms=latest.timestamp_utc_ms)

    def close(self) -> None:
        self._stop_event.set()
        cap_to_release: Optional[cv2.VideoCapture] = None
        if self._cap_lock.acquire(timeout=0.1):
            try:
                cap_to_release = self._cap
                self._cap = None
            finally:
                self._cap_lock.release()

        if cap_to_release is not None:
            def _release_capture(capture: cv2.VideoCapture) -> None:
                try:
                    capture.release()
                except Exception:
                    pass

            release_thread = threading.Thread(
                target=_release_capture,
                args=(cap_to_release,),
                name=f"rtsp-release-{id(self)}",
                daemon=True,
            )
            release_thread.start()
            release_thread.join(timeout=0.2)

        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)

