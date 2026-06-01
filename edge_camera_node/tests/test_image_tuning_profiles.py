from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import traffic_camera_node.stream.rtsp_pipeline as rtsp_pipeline_module
from traffic_camera_node.stream.rtsp_pipeline import (
    CameraSourceKind,
    PipelineMode,
    RtspPipeline,
    UsbCaptureMode,
    V4L2Control,
    _image_tuning_args,
    _v4l2_image_tuning_controls,
)


CSI_PROFILE_EXPECTED_ARGS = {
    "normal": [
        "--ev",
        "0.00",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.00",
        "--contrast",
        "1.03",
        "--sharpness",
        "1.08",
        "--saturation",
        "1.00",
    ],
    "low_light": [
        "--ev",
        "0.30",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.04",
        "--contrast",
        "1.08",
        "--sharpness",
        "1.03",
        "--saturation",
        "0.94",
    ],
    "bright_scene": [
        "--ev",
        "-0.45",
        "--metering",
        "average",
        "--exposure",
        "sport",
        "--awb",
        "daylight",
        "--brightness",
        "-0.06",
        "--contrast",
        "0.98",
        "--sharpness",
        "1.02",
        "--saturation",
        "0.94",
    ],
    "sharpness_safe": [
        "--ev",
        "0.00",
        "--metering",
        "centre",
        "--exposure",
        "sport",
        "--awb",
        "auto",
        "--brightness",
        "0.00",
        "--contrast",
        "1.05",
        "--sharpness",
        "1.14",
        "--saturation",
        "0.98",
    ],
    "disabled": [],
}


USB_PROFILE_EXPECTED_CONTROLS = {
    "normal": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 50,
        "contrast": 52,
        "saturation": 50,
        "sharpness": 53,
        "gain": 9,
        "backlight_compensation": 0,
    },
    "low_light": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 56,
        "contrast": 56,
        "saturation": 47,
        "sharpness": 51,
        "gain": 15,
        "backlight_compensation": 1,
    },
    "bright_scene": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 44,
        "contrast": 48,
        "saturation": 46,
        "sharpness": 48,
        "gain": 4,
        "backlight_compensation": 0,
    },
    "sharpness_safe": {
        "power_line_frequency": "50hz",
        "exposure_auto_priority": 0,
        "white_balance_temperature_auto": 1,
        "brightness": 50,
        "contrast": 54,
        "saturation": 49,
        "sharpness": 63,
        "gain": 10,
        "backlight_compensation": 0,
    },
    "disabled": {},
}


@pytest.mark.parametrize("profile", ["normal", "low_light", "bright_scene", "sharpness_safe", "disabled"])
def test_csi_image_tuning_helper_all_profiles(profile: str) -> None:
    assert _image_tuning_args(profile) == CSI_PROFILE_EXPECTED_ARGS[profile]


def test_csi_image_tuning_helper_unknown_profile_falls_back_to_normal() -> None:
    assert _image_tuning_args("unknown_profile") == CSI_PROFILE_EXPECTED_ARGS["normal"]


@pytest.mark.parametrize("profile", ["normal", "low_light", "bright_scene", "sharpness_safe", "disabled"])
def test_usb_image_tuning_helper_all_profiles(profile: str) -> None:
    assert _v4l2_image_tuning_controls(profile) == USB_PROFILE_EXPECTED_CONTROLS[profile]


def test_usb_image_tuning_helper_unknown_profile_falls_back_to_normal() -> None:
    assert _v4l2_image_tuning_controls("unsupported") == USB_PROFILE_EXPECTED_CONTROLS["normal"]


def test_v4l2_percent_target_clamps_to_control_range() -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    control = V4L2Control(name="brightness", min_value=10, max_value=20, control_type="int")
    high_value, high_error = pipeline._resolve_v4l2_target_value("brightness", 200, control)
    low_value, low_error = pipeline._resolve_v4l2_target_value("brightness", -100, control)

    assert high_error is None
    assert low_error is None
    assert high_value == 20
    assert low_value == 10


def test_v4l2_bool_target_without_min_max_still_maps_to_binary_value() -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    control = V4L2Control(name="white_balance_automatic", control_type="bool")
    resolved, error = pipeline._resolve_v4l2_target_value("white_balance_temperature_auto", 1, control)
    assert error is None
    assert resolved == 1


def test_apply_usb_tuning_skips_unsupported_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    pipeline._logger = MagicMock()
    pipeline._image_tuning_profile = "normal"

    supported = {
        "brightness": V4L2Control(name="brightness", min_value=0, max_value=100, control_type="int"),
    }
    monkeypatch.setattr(pipeline, "_query_v4l2_controls", lambda _device: supported)

    applied: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pipeline,
        "_set_v4l2_control",
        lambda _device, control_name, value: applied.append((control_name, value)) or True,
    )

    pipeline._apply_usb_image_tuning("/dev/video0")
    assert applied == [("brightness", 50)]


def test_apply_usb_tuning_uses_control_alias_when_primary_name_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    pipeline._logger = MagicMock()
    pipeline._image_tuning_profile = "normal"

    supported = {
        "white_balance_automatic": V4L2Control(name="white_balance_automatic", control_type="bool"),
    }
    monkeypatch.setattr(pipeline, "_query_v4l2_controls", lambda _device: supported)

    applied: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pipeline,
        "_set_v4l2_control",
        lambda _device, control_name, value: applied.append((control_name, value)) or True,
    )

    pipeline._apply_usb_image_tuning("/dev/video0")
    assert ("white_balance_automatic", 1) in applied


def test_build_usb_source_command_applies_tuning_before_capture_mode_resolution() -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    pipeline._config = SimpleNamespace(stream=SimpleNamespace(bitrate=2_000_000))
    pipeline._identity = SimpleNamespace(stream_path="/cam0")
    pipeline._ffmpeg_binary = "ffmpeg"
    pipeline._port = 8554

    call_order: list[str] = []

    def _resolve_usb_device_path() -> str:
        call_order.append("resolve_device")
        return "/dev/video0"

    def _apply_usb_image_tuning(usb_device: str) -> None:
        call_order.append(f"apply_tuning:{usb_device}")

    def _resolve_usb_capture_mode(usb_device: str) -> UsbCaptureMode:
        call_order.append(f"resolve_capture:{usb_device}")
        return UsbCaptureMode(width=1280, height=720, fps=30.0, input_format="mjpeg")

    pipeline._resolve_usb_device_path = _resolve_usb_device_path  # type: ignore[method-assign]
    pipeline._apply_usb_image_tuning = _apply_usb_image_tuning  # type: ignore[method-assign]
    pipeline._resolve_usb_capture_mode = _resolve_usb_capture_mode  # type: ignore[method-assign]

    command = pipeline._build_usb_source_command(PipelineMode.LIBAV_MPEGTS)
    assert command[0] == "ffmpeg"
    assert "-fflags" in command
    assert "nobuffer" in command
    assert "-flags" in command
    assert "low_delay" in command
    assert "-muxdelay" in command
    assert "-muxpreload" in command
    assert "-flush_packets" in command
    assert call_order == [
        "resolve_device",
        "apply_tuning:/dev/video0",
        "resolve_capture:/dev/video0",
    ]


def test_query_v4l2_controls_missing_binary_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = RtspPipeline.__new__(RtspPipeline)
    pipeline._logger = MagicMock()
    monkeypatch.setattr(rtsp_pipeline_module.shutil, "which", lambda _name: None)

    controls = pipeline._query_v4l2_controls("/dev/video0")
    assert controls == {}
