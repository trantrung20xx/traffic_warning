from __future__ import annotations

import logging

from traffic_camera_node.identity import RuntimeIdentity
from traffic_camera_node.state import NodeState
from traffic_camera_node.stream.process_supervisor import ProcessSupervisor


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        camera_id="cam_2ccf6788f9e5",
        node_id="36be62afb543",
        mac_address="2ccf6788f9e5",
        interface="eth0",
        mdns_hostname="cam-2ccf6788f9e5.local",
        rtsp_port=8593,
        stream_path="/cam_2ccf6788f9e5",
        fallback_ip="172.20.10.2",
        created_at="2026-05-10T16:57:58.276547+07:00",
    )


def test_watchdog_probe_uses_local_rtsp_url() -> None:
    state = NodeState(_identity(), image_tuning_profile="normal", service_version="0.1.0")
    supervisor = ProcessSupervisor(
        config=object(),  # type: ignore[arg-type]
        state=state,
        pipeline=object(),  # type: ignore[arg-type]
        fps_probe=object(),  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )

    assert supervisor._local_probe_rtsp_url() == "rtsp://127.0.0.1:8593/cam_2ccf6788f9e5"
