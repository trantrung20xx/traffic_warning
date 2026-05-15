from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


NETWORK_SOURCE_PREFIXES = ("rtsp://", "rtsps://", "http://", "https://")
RTSP_SOURCE_PREFIXES = ("rtsp://", "rtsps://")


@dataclass(frozen=True)
class VideoSourceDescriptor:
    # Chuỗi source đã normalize để dùng trực tiếp cho OpenCV.
    normalized_source: str
    # Cờ phân biệt file cục bộ và stream mạng.
    is_local_file: bool


def _describe_video_source(source: str) -> VideoSourceDescriptor:
    s = (source or "").strip()
    if not s:
        return VideoSourceDescriptor(normalized_source="", is_local_file=False)

    if s.lower().startswith(NETWORK_SOURCE_PREFIXES):
        # URL mạng giữ nguyên, không resolve sang path local.
        return VideoSourceDescriptor(normalized_source=s, is_local_file=False)

    try:
        local_path = Path(s)
        if local_path.exists() and local_path.is_file():
            # File local được resolve tuyệt đối để tránh phụ thuộc cwd runtime.
            return VideoSourceDescriptor(normalized_source=str(local_path.resolve()), is_local_file=True)
    except OSError:
        pass

    # Fallback: coi như source không-local để lớp trên tự xử lý lỗi mở stream.
    return VideoSourceDescriptor(normalized_source=s, is_local_file=False)


def is_local_video_file(source: str) -> bool:
    """
    Xác định source là file video cục bộ để áp dụng nhịp đọc theo FPS gốc.
    """
    return _describe_video_source(source).is_local_file


@dataclass
class Frame:
    # Frame BGR raw lấy từ OpenCV.
    bgr: np.ndarray
    # Timestamp epoch ms tại lúc thread reader nhận frame.
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
        # URL/path đã normalize cho VideoCapture.
        self.rtsp_url = source_descriptor.normalized_source
        self._source_is_local_file = source_descriptor.is_local_file
        self.reconnect_delay_s = float(reconnect_delay_s)
        self.frame_width = frame_width
        self.frame_height = frame_height
        # Lock cho resource capture và buffer frame dùng chung giữa thread.
        self._cap_lock = threading.Lock()
        self._latest_lock = threading.Lock()
        self._cap: Optional[cv2.VideoCapture] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._latest_frame: Optional[Frame] = None
        # Seq tăng mỗi frame để read(only_new=True) biết frame đã đổi chưa.
        self._latest_frame_seq: int = 0
        self._last_delivered_seq: int = 0
        # FPS đọc thực tế của nguồn stream/video để UI phân biệt với AI FPS.
        self._source_fps_lock = threading.Lock()
        self._source_frame_times_s: deque[float] = deque()
        self._source_fps: Optional[float] = None
        # Nhịp pacing chỉ áp dụng cho source là file video local.
        self._file_frame_interval_s: Optional[float] = None
        self._file_next_deadline_s: Optional[float] = None
        self._source_is_rtsp = self.rtsp_url.lower().startswith(RTSP_SOURCE_PREFIXES)
        self._use_ffmpeg_pipe = self._source_is_rtsp and bool(self.frame_width and self.frame_height)
        self._ffmpeg_frame_size = int(self.frame_width or 0) * int(self.frame_height or 0) * 3
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
            self._cap = None
        if self._ffmpeg_proc is not None:
            self._terminate_ffmpeg(self._ffmpeg_proc)
            self._ffmpeg_proc = None
        if self._use_ffmpeg_pipe:
            self._ffmpeg_proc = self._start_ffmpeg_pipe()
            return
        # Mở lại VideoCapture mỗi lần reconnect.
        self._cap = cv2.VideoCapture(self.rtsp_url)
        cap = self._cap
        if cap is not None:
            try:
                # Buffer nhỏ để giảm độ trễ realtime.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                try:
                    # Timeout mở stream để không treo quá lâu.
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1000)
                except Exception:
                    pass
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                try:
                    # Timeout đọc frame để vòng reconnect phản ứng nhanh.
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)
                except Exception:
                    pass
            if self._source_is_local_file:
                # File local cần pacing để phát theo FPS gốc.
                self._configure_file_playback_pacing(cap)
            else:
                self._file_frame_interval_s = None
                self._file_next_deadline_s = None

    def _start_ffmpeg_pipe(self) -> subprocess.Popen:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-rw_timeout",
            "2000000",
            "-i",
            self.rtsp_url,
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"scale={int(self.frame_width)}:{int(self.frame_height)}",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._ffmpeg_frame_size,
        )

    def _read_ffmpeg_frame(self) -> Optional[np.ndarray]:
        proc = self._ffmpeg_proc
        if proc is None or proc.stdout is None or proc.poll() is not None:
            return None
        raw = proc.stdout.read(self._ffmpeg_frame_size)
        if len(raw) != self._ffmpeg_frame_size:
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape(
            (int(self.frame_height), int(self.frame_width), 3)
        ).copy()

    def _terminate_ffmpeg(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    def _configure_file_playback_pacing(self, cap: cv2.VideoCapture) -> None:
        fps = 0.0
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            fps = 0.0
        if not (1.0 <= fps <= 240.0):
            # FPS bất thường thì fallback giá trị an toàn.
            fps = 25.0
        self._file_frame_interval_s = 1.0 / fps
        # Deadline khởi tạo theo thời điểm hiện tại.
        self._file_next_deadline_s = time.perf_counter()

    def _pace_file_playback_if_needed(self) -> bool:
        interval = self._file_frame_interval_s
        if not self._source_is_local_file or interval is None:
            return True

        now = time.perf_counter()
        deadline = self._file_next_deadline_s
        if deadline is None:
            deadline = now

        # Cập nhật deadline frame kế tiếp theo nhịp cố định.
        deadline += interval
        self._file_next_deadline_s = deadline

        sleep_s = deadline - now
        if sleep_s > 0:
            # Sleep có thể bị ngắt bởi stop_event để shutdown nhanh.
            if self._stop_event.wait(sleep_s):
                return False
        elif sleep_s < -1.0:
            # Nếu backend bị trễ quá xa, reset deadline để tránh dồn sai số.
            self._file_next_deadline_s = now
        return True

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._cap_lock:
                if self._use_ffmpeg_pipe:
                    if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None:
                        self._open_locked()
                elif self._cap is None:
                    self._open_locked()
                cap = self._cap

            if self._use_ffmpeg_pipe:
                frame = self._read_ffmpeg_frame()
                if frame is None:
                    # Mất frame/ffmpeg dừng -> chờ ngắn rồi reopen ffmpeg pipeline.
                    if self._stop_event.wait(self.reconnect_delay_s):
                        break
                    with self._cap_lock:
                        self._open_locked()
                    continue

                snapshot = Frame(bgr=frame, timestamp_utc_ms=int(time.time() * 1000))
                with self._latest_lock:
                    # Luôn ghi đè frame mới nhất, không tích backlog.
                    self._latest_frame = snapshot
                    self._latest_frame_seq += 1
                self._record_source_frame()
                continue

            if cap is None:
                # Chưa mở được capture: chờ rồi thử lại.
                if self._stop_event.wait(self.reconnect_delay_s):
                    break
                continue

            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                # Mất frame -> chờ ngắn rồi reopen capture.
                if self._stop_event.wait(self.reconnect_delay_s):
                    break
                with self._cap_lock:
                    self._open_locked()
                continue

            if self.frame_width and self.frame_height:
                # Resize về kích thước chuẩn để đồng bộ với lane polygon runtime.
                frame = cv2.resize(frame, (self.frame_width, self.frame_height))

            snapshot = Frame(bgr=frame, timestamp_utc_ms=int(time.time() * 1000))
            with self._latest_lock:
                # Luôn ghi đè frame mới nhất, không tích backlog.
                self._latest_frame = snapshot
                self._latest_frame_seq += 1
            self._record_source_frame()
            if not self._pace_file_playback_if_needed():
                break

    def _record_source_frame(self) -> None:
        now_s = time.perf_counter()
        with self._source_fps_lock:
            self._source_frame_times_s.append(now_s)
            cutoff_s = now_s - 1.5
            while self._source_frame_times_s and self._source_frame_times_s[0] < cutoff_s:
                self._source_frame_times_s.popleft()

            if len(self._source_frame_times_s) >= 2:
                duration_s = self._source_frame_times_s[-1] - self._source_frame_times_s[0]
                self._source_fps = (
                    (len(self._source_frame_times_s) - 1) / duration_s if duration_s > 0 else None
                )
            else:
                self._source_fps = None

    def get_source_fps(self) -> Optional[float]:
        with self._source_fps_lock:
            if self._source_fps is None:
                return None
            return float(self._source_fps)

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

    def peek_latest(self) -> Optional[Frame]:
        """
        Trả snapshot frame mới nhất nhưng không động vào cờ `only_new`.
        Dùng cho luồng preview để không ảnh hưởng nhịp đọc của pipeline AI.
        """
        with self._latest_lock:
            latest = self._latest_frame
        if latest is None:
            return None
        return Frame(bgr=latest.bgr.copy(), timestamp_utc_ms=latest.timestamp_utc_ms)

    def close(self) -> None:
        # Signal dừng cho reader loop và các wait() đang chờ.
        self._stop_event.set()
        cap_to_release: Optional[cv2.VideoCapture] = None
        ffmpeg_to_terminate: Optional[subprocess.Popen] = None
        if self._cap_lock.acquire(timeout=0.1):
            try:
                # Tách cap ra biến cục bộ để release ngoài lock.
                cap_to_release = self._cap
                self._cap = None
                ffmpeg_to_terminate = self._ffmpeg_proc
                self._ffmpeg_proc = None
            finally:
                self._cap_lock.release()

        if ffmpeg_to_terminate is not None:
            try:
                self._terminate_ffmpeg(ffmpeg_to_terminate)
            except Exception:
                pass

        if cap_to_release is not None:
            def _release_capture(capture: cv2.VideoCapture) -> None:
                try:
                    capture.release()
                except Exception:
                    pass

            # Release trong thread riêng để tránh close() bị treo dài bất thường.
            release_thread = threading.Thread(
                target=_release_capture,
                args=(cap_to_release,),
                name=f"rtsp-release-{id(self)}",
                daemon=True,
            )
            release_thread.start()
            release_thread.join(timeout=0.2)

        if self._reader_thread.is_alive():
            # Join ngắn để shutdown mềm, không chặn server quá lâu.
            self._reader_thread.join(timeout=0.5)

