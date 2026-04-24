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
