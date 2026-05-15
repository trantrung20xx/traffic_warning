from __future__ import annotations

from traffic_camera_node.stream.rtsp_pipeline import RtspPipeline


SAMPLE_V4L2_LISTING = """
ioctl: VIDIOC_ENUM_FMT
    Type: Video Capture

    [0]: 'MJPG' (Motion-JPEG, compressed)
        Size: Discrete 160x120
            Interval: Discrete 0.050s (20.000 fps)
            Interval: Discrete 0.067s (15.000 fps)
    [1]: 'YUYV' (YUYV 4:2:2)
        Size: Discrete 160x120
            Interval: Discrete 0.050s (20.000 fps)
            Interval: Discrete 0.067s (15.000 fps)
""".strip()


def test_parse_v4l2_modes_extracts_discrete_modes() -> None:
    modes = RtspPipeline._parse_v4l2_modes(SAMPLE_V4L2_LISTING)
    assert ("MJPG", 160, 120, 20.0) in modes
    assert ("MJPG", 160, 120, 15.0) in modes
    assert ("YUYV", 160, 120, 20.0) in modes
    assert ("YUYV", 160, 120, 15.0) in modes


def test_select_usb_mode_falls_back_to_best_supported_mode() -> None:
    modes = RtspPipeline._parse_v4l2_modes(SAMPLE_V4L2_LISTING)
    selected = RtspPipeline._select_usb_mode(
        modes=modes,
        requested_width=1920,
        requested_height=1080,
        requested_fps=30.0,
        preferred_formats=("MJPG", "YUYV"),
    )
    assert selected == ("MJPG", 160, 120, 20.0)
