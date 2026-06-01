from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rtsp.rtsp_stream import Frame, RtspFrameReader, is_local_video_file


def test_rtsp_reader_close_returns_quickly_when_source_unavailable() -> None:
    reader = RtspFrameReader(
        "rtsp://127.0.0.1:1/non-existent",
        reconnect_delay_s=0.05,
        frame_width=320,
        frame_height=180,
    )
    time.sleep(0.05)

    started = time.perf_counter()
    reader.close()
    elapsed_s = time.perf_counter() - started

    # Shutdown phải phản hồi nhanh để Ctrl+C không bị treo.
    assert elapsed_s < 2.0


def test_is_local_video_file_detects_local_path(tmp_path) -> None:
    sample = tmp_path / "sample.mp4"
    sample.write_bytes(b"not-a-real-video")

    assert is_local_video_file(str(sample))
    assert not is_local_video_file("rtsp://127.0.0.1/live")
    assert not is_local_video_file("https://example.com/video.mp4")


def test_reader_only_new_frame_mode_avoids_duplicate_processing() -> None:
    reader = RtspFrameReader(
        "rtsp://127.0.0.1:1/non-existent",
        reconnect_delay_s=0.05,
        frame_width=320,
        frame_height=180,
    )
    try:
        with reader._latest_lock:
            reader._latest_frame = Frame(
                bgr=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_utc_ms=1,
            )
            reader._latest_frame_seq += 1

        first = reader.read(only_new=True)
        second = reader.read(only_new=True)
        replay = reader.read(only_new=False)

        assert first is not None
        assert second is None
        assert replay is not None
    finally:
        reader.close()


def test_reader_peek_latest_does_not_consume_only_new_sequence() -> None:
    reader = RtspFrameReader(
        "rtsp://127.0.0.1:1/non-existent",
        reconnect_delay_s=0.05,
        frame_width=320,
        frame_height=180,
    )
    try:
        with reader._latest_lock:
            reader._latest_frame = Frame(
                bgr=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_utc_ms=123,
            )
            reader._latest_frame_seq += 1

        peeked = reader.peek_latest()
        consumed = reader.read(only_new=True)
        consumed_again = reader.read(only_new=True)

        assert peeked is not None
        assert peeked.timestamp_utc_ms == 123
        assert consumed is not None
        assert consumed_again is None
    finally:
        reader.close()


def test_reader_source_fps_estimation_from_samples(monkeypatch) -> None:
    reader = RtspFrameReader(
        "rtsp://127.0.0.1:1/non-existent",
        reconnect_delay_s=0.05,
        frame_width=320,
        frame_height=180,
    )
    try:
        samples = iter([1.0, 1.1, 1.2, 1.3, 1.4])
        monkeypatch.setattr("app.rtsp.rtsp_stream.time.perf_counter", lambda: next(samples))
        reader._record_source_frame()
        reader._record_source_frame()
        reader._record_source_frame()
        reader._record_source_frame()
        reader._record_source_frame()
        fps = reader.get_source_fps()
        assert fps is not None
        assert fps > 8.0
        assert fps < 12.0
    finally:
        reader.close()


def test_reader_ffmpeg_pipe_mode_emits_frames(monkeypatch) -> None:
    class _FakeProc:
        def poll(self):
            return None

    def _fake_open_locked(self):
        self._cap = None
        self._ffmpeg_proc = _FakeProc()

    def _fake_read_ffmpeg_frame(self):
        if getattr(self, "_test_frame_emitted", False):
            return None
        self._test_frame_emitted = True
        # Dừng reader sau khi đã phát một frame để test kết thúc nhanh.
        self._stop_event.set()
        return np.zeros((int(self.frame_height), int(self.frame_width), 3), dtype=np.uint8)

    monkeypatch.setattr(RtspFrameReader, "_open_locked", _fake_open_locked)
    monkeypatch.setattr(RtspFrameReader, "_read_ffmpeg_frame", _fake_read_ffmpeg_frame)

    reader = RtspFrameReader(
        "rtsp://127.0.0.1:8554/live",
        reconnect_delay_s=0.05,
        frame_width=320,
        frame_height=180,
    )
    try:
        deadline = time.perf_counter() + 1.0
        frame = None
        while time.perf_counter() < deadline:
            frame = reader.read(only_new=False)
            if frame is not None:
                break
            time.sleep(0.01)
        assert frame is not None
        assert frame.bgr.shape == (180, 320, 3)
    finally:
        reader.close()


def test_reader_ffmpeg_pipe_uses_stable_low_latency_rtsp_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProc:
        stdout = None

        def poll(self):
            return None

    def _fake_popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = dict(kwargs)
        return _FakeProc()

    monkeypatch.setattr("app.rtsp.rtsp_stream.subprocess.Popen", _fake_popen)

    reader = RtspFrameReader.__new__(RtspFrameReader)
    reader.rtsp_url = "rtsp://127.0.0.1:8554/live"
    reader.frame_width = 640
    reader.frame_height = 360
    reader._ffmpeg_frame_size = int(reader.frame_width) * int(reader.frame_height) * 3

    proc = RtspFrameReader._start_ffmpeg_pipe(reader)

    assert proc is not None
    command = captured["command"]
    assert isinstance(command, list)
    assert "-fflags" in command
    assert "nobuffer" in command
    assert "-flags" in command
    assert "low_delay" in command
    assert "-analyzeduration" in command
    assert "100000" in command
    assert "-probesize" in command
    assert "131072" in command
    assert "-max_delay" in command
    assert "250000" in command
