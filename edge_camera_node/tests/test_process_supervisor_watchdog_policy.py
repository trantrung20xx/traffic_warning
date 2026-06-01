from __future__ import annotations

import logging

from traffic_camera_node.config import AppConfig, WatchdogConfig
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


class _PipelineStub:
    def __init__(self) -> None:
        self.restart_calls = 0
        self.stop_calls = 0

    def restart(self) -> None:
        self.restart_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


def _supervisor(max_restarts_per_window: int) -> tuple[ProcessSupervisor, NodeState, _PipelineStub]:
    config = AppConfig(
        watchdog=WatchdogConfig(
            max_restarts_per_window=max_restarts_per_window,
            restart_window_seconds=300,
        )
    )
    state = NodeState(_identity(), image_tuning_profile="normal", service_version="0.1.0")
    pipeline = _PipelineStub()
    supervisor = ProcessSupervisor(
        config=config,
        state=state,
        pipeline=pipeline,  # type: ignore[arg-type]
        fps_probe=object(),  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )
    return supervisor, state, pipeline


def test_profile_change_restarts_do_not_count_toward_watchdog() -> None:
    supervisor, state, pipeline = _supervisor(max_restarts_per_window=2)

    for _ in range(6):
        supervisor._try_restart_with_limits(
            "image tuning profile changed",
            ignore_retry_delay=True,
            force_watchdog_count=False,
        )

    snapshot = state.snapshot()
    assert pipeline.restart_calls == 6
    assert pipeline.stop_calls == 0
    assert snapshot.restart_count == 0
    assert snapshot.watchdog_latched is False


def test_regular_manual_restart_still_latches_watchdog() -> None:
    supervisor, state, pipeline = _supervisor(max_restarts_per_window=2)

    supervisor._try_restart_with_limits(
        "manual restart requested",
        ignore_retry_delay=True,
        force_watchdog_count=True,
    )
    supervisor._try_restart_with_limits(
        "manual restart requested",
        ignore_retry_delay=True,
        force_watchdog_count=True,
    )
    supervisor._try_restart_with_limits(
        "manual restart requested",
        ignore_retry_delay=True,
        force_watchdog_count=True,
    )

    snapshot = state.snapshot()
    assert pipeline.restart_calls == 2
    assert pipeline.stop_calls == 1
    assert snapshot.restart_count == 2
    assert snapshot.watchdog_latched is True
    assert snapshot.last_error is not None
    assert "Watchdog latched" in snapshot.last_error
