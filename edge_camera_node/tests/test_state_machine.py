from __future__ import annotations

from traffic_camera_node.identity import RuntimeIdentity
from traffic_camera_node.network import build_rtsp_urls
from traffic_camera_node.state import NodeState, NodeStatus


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        camera_id="cam_dca632112233",
        node_id="nodeid001122",
        mac_address="dca632112233",
        interface="eth0",
        mdns_hostname="cam-dca632112233.local",
        rtsp_port=8554,
        stream_path="/cam_dca632112233",
        fallback_ip="192.168.1.50",
        created_at="2026-05-09T10:00:00+07:00",
    )


def test_state_transitions() -> None:
    state = NodeState(_identity(), image_tuning_profile="normal", service_version="0.1.0")
    assert state.get_status() == NodeStatus.BOOTING
    assert state.transition(NodeStatus.ONLINE)
    assert state.transition(NodeStatus.STREAMING)
    assert state.transition(NodeStatus.WARNING)
    assert state.transition(NodeStatus.ERROR)
    assert state.transition(NodeStatus.ONLINE)


def test_invalid_transition_is_rejected() -> None:
    state = NodeState(_identity(), image_tuning_profile="normal", service_version="0.1.0")
    assert not state.transition(NodeStatus.STREAMING)
    assert state.get_status() == NodeStatus.BOOTING


def test_url_builder_prefers_mdns_and_keeps_ip_fallback() -> None:
    urls = build_rtsp_urls(_identity(), current_ip="192.168.1.77")
    assert (
        urls.primary_rtsp_url
        == "rtsp://cam-dca632112233.local:8554/cam_dca632112233"
    )
    assert (
        urls.ip_fallback_rtsp_url
        == "rtsp://192.168.1.50:8554/cam_dca632112233"
    )
