from __future__ import annotations

import pytest

from traffic_camera_node.identity import allocate_rtsp_port


def test_port_allocator_is_deterministic() -> None:
    checker = lambda _port: False
    p1 = allocate_rtsp_port(
        node_id="node-fixed",
        port_start=8554,
        port_end=8654,
        fixed_rtsp_port=None,
        port_checker=checker,
    )
    p2 = allocate_rtsp_port(
        node_id="node-fixed",
        port_start=8554,
        port_end=8654,
        fixed_rtsp_port=None,
        port_checker=checker,
    )
    assert p1 == p2


def test_port_allocator_skips_occupied_port() -> None:
    occupied = {8600, 8601}

    def checker(port: int) -> bool:
        return port in occupied

    result = allocate_rtsp_port(
        node_id="node-fixed",
        port_start=8600,
        port_end=8603,
        fixed_rtsp_port=None,
        port_checker=checker,
    )
    assert result in {8602, 8603}


def test_fixed_port_must_be_free() -> None:
    with pytest.raises(RuntimeError):
        allocate_rtsp_port(
            node_id="node-fixed",
            port_start=8554,
            port_end=8654,
            fixed_rtsp_port=8554,
            port_checker=lambda _port: True,
        )
