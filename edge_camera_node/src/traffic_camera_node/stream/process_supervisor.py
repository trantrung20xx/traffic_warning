from __future__ import annotations

import collections
import threading
import time
from logging import Logger

from ..config import AppConfig
from ..state import NodeState, NodeStatus
from .fps_probe import FpsProbe
from .rtsp_pipeline import PipelineStartError, RtspPipeline


class ProcessSupervisor:
    def __init__(
        self,
        config: AppConfig,
        state: NodeState,
        pipeline: RtspPipeline,
        fps_probe: FpsProbe,
        logger: Logger,
    ) -> None:
        # Lưu tham chiếu cấu hình và các thành phần phối hợp.
        self._config = config
        self._state = state
        self._pipeline = pipeline
        self._fps_probe = fps_probe
        self._logger = logger

        # Lịch sử khởi động lại dùng cho watchdog theo cửa sổ thời gian.
        self._restart_history: collections.deque[float] = collections.deque()
        # Cờ dừng để thoát monitor thread an toàn.
        self._stop_event = threading.Event()
        # Cờ khởi động lại thủ công được bật bởi nút/API.
        self._manual_restart_event = threading.Event()
        # Cờ bật stream thủ công từ API/UI.
        self._manual_start_event = threading.Event()
        # Cờ tắt stream thủ công từ API/UI.
        self._manual_stop_event = threading.Event()
        # Tham chiếu thread giám sát nền.
        self._thread: threading.Thread | None = None
        # Chốt watchdog chặn vòng lặp khởi động lại vô hạn khi lỗi lặp lại.
        self._watchdog_latched = False
        # Bật/tắt watchdog restart theo ý người vận hành.
        self._stream_enabled = True
        # Mốc thời gian retry tiếp theo để tránh spam restart khi lỗi phần cứng.
        self._next_retry_monotonic = 0.0

    def start(self) -> None:
        # Đặt lại cờ điều khiển trước khi bắt đầu giám sát.
        self._stop_event.clear()
        self._manual_start_event.clear()
        self._manual_stop_event.clear()
        self._watchdog_latched = False
        self._stream_enabled = True
        self._next_retry_monotonic = 0.0
        self._state.set_stream_enabled(True)
        try:
            # Thử khởi động pipeline ngay lần đầu.
            self._pipeline.start()
            self._state.set_stream_running(True)
            self._state.transition(NodeStatus.STREAMING)
        except PipelineStartError as exc:
            self._state.set_stream_running(False)
            self._state.set_error(str(exc))
            self._logger.exception("Initial RTSP pipeline start failed.")
            self._try_restart_with_limits(f"initial start failed: {exc}", error=exc)
        except Exception as exc:
            # Nếu khởi động lỗi thì ghi trạng thái và chuyển qua cơ chế khởi động lại có giới hạn.
            self._state.set_stream_running(False)
            self._state.set_error(str(exc))
            self._logger.exception("Initial RTSP pipeline start failed.")
            self._try_restart_with_limits(f"initial start failed: {exc}")

        # Luồng giám sát chạy nền để theo dõi tình trạng liên tục.
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # Báo dừng vòng lặp giám sát rồi chờ luồng thoát.
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        # Dừng pipeline thật và cập nhật trạng thái.
        self._pipeline.stop()
        self._state.set_stream_running(False)

    def request_restart(self, force: bool = False) -> bool:
        if not self._stream_enabled and not force:
            return False
        # Nếu watchdog đang chốt lỗi thì từ chối khởi động lại thông thường.
        if self._watchdog_latched and not force:
            return False
        # Hàng đợi khởi động lại để luồng giám sát xử lý an toàn.
        self._manual_restart_event.set()
        return True

    def set_stream_enabled(self, enabled: bool) -> bool:
        if enabled:
            if self._watchdog_latched:
                return False
            self._stream_enabled = True
            self._next_retry_monotonic = 0.0
            self._state.set_stream_enabled(True)
            self._manual_stop_event.clear()
            self._manual_start_event.set()
            return True

        self._stream_enabled = False
        self._state.set_stream_enabled(False)
        self._manual_restart_event.clear()
        self._manual_start_event.clear()
        self._manual_stop_event.set()
        return True

    def is_stream_enabled(self) -> bool:
        return self._stream_enabled

    def clear_watchdog_and_restart(self) -> None:
        # RESET_WATCHDOG: bỏ chốt watchdog và xóa lịch sử khởi động lại cũ.
        self._watchdog_latched = False
        self._restart_history.clear()
        self._next_retry_monotonic = 0.0
        self._state.set_restart_count(0)
        self._state.set_watchdog_latched(False)
        self._state.clear_error()
        # Kích hoạt một lần khởi động lại mới sau khi xóa lỗi.
        self._manual_restart_event.set()

    def _run(self) -> None:
        # Vòng lặp giám sát chính: nhận lệnh, theo dõi health, cập nhật trạng thái.
        while not self._stop_event.is_set():
            if self._manual_stop_event.is_set():
                self._manual_stop_event.clear()
                self._pipeline.stop()
                self._state.set_stream_running(False)
                self._state.transition(NodeStatus.ONLINE)
                self._state.set_fps_estimate(0.0)
                self._logger.warning("RTSP stream stopped by remote command.")

            if self._manual_start_event.is_set():
                self._manual_start_event.clear()
                try:
                    self._pipeline.start()
                    self._state.set_stream_running(True)
                    self._state.transition(NodeStatus.STREAMING)
                    self._logger.warning("RTSP stream started by remote command.")
                except PipelineStartError as exc:
                    self._state.set_stream_running(False)
                    self._state.set_warning(f"Start stream failed: {exc}")
                    self._next_retry_monotonic = time.monotonic() + exc.retry_after_s
                except Exception as exc:
                    self._state.set_stream_running(False)
                    self._state.set_warning(f"Start stream failed: {exc}")

            if self._manual_restart_event.is_set():
                # Tiêu thụ lệnh khởi động lại thủ công.
                self._manual_restart_event.clear()
                self._try_restart_with_limits("manual restart requested", ignore_retry_delay=True)

            health = self._pipeline.health()
            if not self._stream_enabled:
                self._state.set_stream_running(False)
                self._state.set_fps_estimate(0.0)
            elif not health.running and not self._watchdog_latched:
                # Pipeline chết sẽ được xử lý khởi động lại nội bộ trước khi trông chờ systemd.
                self._state.set_stream_running(False)
                detail = health.detail or "pipeline not running"
                self._state.set_warning(detail)
                self._try_restart_with_limits(detail)
            elif health.running:
                self._state.set_stream_running(True)
                rtsp_url = self._state.snapshot().primary_rtsp_url
                fps = self._fps_probe.estimate(rtsp_url, stream_running=True)
                self._state.set_fps_estimate(fps)
                if fps < self._config.watchdog.fps_warning_threshold:
                    # Cảnh báo FPS thấp không phải lỗi chết stream.
                    self._state.set_warning(
                        f"Low FPS warning: {fps:.1f} < {self._config.watchdog.fps_warning_threshold}"
                    )
                elif self._state.get_status() == NodeStatus.WARNING:
                    self._state.transition(NodeStatus.STREAMING)

            # Chu kỳ cố định giúp overhead thấp và hành vi ổn định.
            time.sleep(2)

    def _try_restart_with_limits(
        self,
        reason: str,
        error: Exception | None = None,
        ignore_retry_delay: bool = False,
    ) -> None:
        # Đánh giá tần suất khởi động lại trong cửa sổ thời gian.
        now = time.monotonic()
        if not ignore_retry_delay and now < self._next_retry_monotonic:
            return

        window = self._config.watchdog.restart_window_seconds
        max_restarts = self._config.watchdog.max_restarts_per_window
        count_toward_watchdog = True
        retry_after_s = 0.5
        if isinstance(error, PipelineStartError):
            count_toward_watchdog = error.count_toward_watchdog
            retry_after_s = error.retry_after_s

        # Cửa sổ trượt ngăn vòng lặp khởi động lại vô hạn gây áp lực CPU/SD.
        counted_this_attempt = False
        if count_toward_watchdog:
            while self._restart_history and now - self._restart_history[0] > window:
                self._restart_history.popleft()

            if len(self._restart_history) >= max_restarts:
                self._watchdog_latched = True
                self._state.set_watchdog_latched(True)
                self._state.set_error(
                    f"Watchdog latched: too many restarts in {window}s. Last reason: {reason}"
                )
                self._pipeline.stop()
                self._logger.error("Watchdog latched. Manual RESET_WATCHDOG required.")
                return

            # Nếu còn lượt khởi động lại thì ghi dấu thời gian và thử lại.
            self._restart_history.append(now)
            counted_this_attempt = True
            self._state.set_restart_count(len(self._restart_history))
        else:
            # Lỗi phần cứng/cấu hình camera: không latch watchdog, retry thưa để chờ môi trường hồi phục.
            self._logger.warning(
                "Skipping watchdog count for restart. next_retry_in=%.1fs reason=%s",
                retry_after_s,
                reason,
            )

        try:
            self._logger.warning("Restarting RTSP pipeline. reason=%s", reason)
            self._pipeline.restart()
            self._next_retry_monotonic = 0.0
            self._state.set_stream_running(True)
            self._state.transition(NodeStatus.STREAMING)
        except PipelineStartError as exc:
            if counted_this_attempt and not exc.count_toward_watchdog and self._restart_history:
                self._restart_history.pop()
                self._state.set_restart_count(len(self._restart_history))
            self._next_retry_monotonic = time.monotonic() + exc.retry_after_s
            self._state.set_stream_running(False)
            self._state.set_warning(f"Restart failed: {exc}")
        except Exception as exc:
            # Nếu khởi động lại lỗi thì giữ trạng thái cảnh báo để hiện trên màn hình chẩn đoán.
            self._next_retry_monotonic = time.monotonic() + retry_after_s
            self._state.set_stream_running(False)
            self._state.set_warning(f"Restart failed: {exc}")
