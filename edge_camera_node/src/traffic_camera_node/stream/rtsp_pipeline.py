from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from logging import Logger

from ..config import AppConfig
from ..identity import RuntimeIdentity, get_port_listeners


@dataclass(frozen=True)
class PipelineHealth:
    # Giá trị True khi toàn bộ tiến trình của đường ống còn sống.
    running: bool
    # Mô tả lỗi ngắn gọn khi đường ống không ổn định.
    detail: str | None = None


def _image_tuning_args(profile: str) -> list[str]:
    # Các mức tinh chỉnh nhẹ để tránh làm lệch dữ liệu ảnh phía server.
    if profile == "low_light":
        return ["--brightness", "0.10", "--contrast", "1.05", "--sharpness", "1.00"]
    if profile == "bright_scene":
        return ["--brightness", "-0.05", "--contrast", "1.10", "--sharpness", "1.00"]
    if profile == "sharpness_safe":
        return ["--brightness", "0.00", "--contrast", "1.05", "--sharpness", "1.15"]
    if profile == "disabled":
        return []
    return ["--brightness", "0.00", "--contrast", "1.00", "--sharpness", "1.00"]


class RtspPipeline:
    def __init__(self, config: AppConfig, identity: RuntimeIdentity, logger: Logger) -> None:
        # Lưu cấu hình và định danh dùng xuyên suốt vòng đời đường ống.
        self._config = config
        self._identity = identity
        self._logger = logger

        # Ba tiến trình lõi: MediaMTX server, rpicam nguồn, ffmpeg phát luồng.
        self._mediamtx_process: subprocess.Popen | None = None
        self._rpicam_process: subprocess.Popen | None = None
        self._ffmpeg_process: subprocess.Popen | None = None

        # Khóa để tuần tự hóa thao tác khởi động/dừng, tránh tranh chấp luồng.
        self._lock = threading.Lock()

        # Port RTSP cố định lấy từ runtime identity đã lưu.
        self._port = identity.rtsp_port

        # Tìm tệp thực thi ngay lúc khởi tạo để báo lỗi sớm nếu thiếu phụ thuộc.
        self._mediamtx_binary = self._resolve_binary(self._config.stream.mediamtx_binary)
        self._camera_binary = self._resolve_camera_binary()
        self._ffmpeg_binary = self._resolve_binary(self._config.stream.ffmpeg_binary)

    def _resolve_binary(self, configured_binary: str) -> str:
        found = shutil.which(configured_binary)
        if not found:
            raise RuntimeError(f"Required binary not found: {configured_binary}")
        return found

    def _resolve_camera_binary(self) -> str:
        # Ưu tiên rpicam-vid, dùng libcamera-vid dự phòng nếu hệ thống dùng tên cũ.
        preferred = self._config.stream.rpicam_vid_binary
        found = shutil.which(preferred)
        if found:
            return found
        fallback = shutil.which("libcamera-vid")
        if fallback:
            self._logger.warning("%s not found, falling back to libcamera-vid.", preferred)
            return fallback
        raise RuntimeError("Neither rpicam-vid nor libcamera-vid is available.")

    def _build_mediamtx_command(self) -> list[str]:
        # Chạy MediaMTX bằng cấu hình mặc định và biến môi trường ghi đè.
        return [self._mediamtx_binary]

    def _mediamtx_env(self) -> dict[str, str]:
        # Cấu hình MediaMTX bằng biến môi trường để không cần file cấu hình động.
        env = dict(os.environ)
        env["MTX_RTSPADDRESS"] = f":{self._port}"
        env["MTX_RTMP"] = "false"
        env["MTX_HLS"] = "false"
        env["MTX_WEBRTC"] = "false"
        env["MTX_SRT"] = "false"
        return env

    def _build_rpicam_command(self) -> list[str]:
        # rpicam mã hóa H264/MPEG-TS và đẩy ra đích UDP nội bộ.
        camera = self._config.camera
        stream = self._config.stream
        cmd = [
            self._camera_binary,
            "-n",
            "-t",
            "0",
            "--width",
            str(camera.width),
            "--height",
            str(camera.height),
            "--framerate",
            str(camera.fps),
            "--codec",
            "libav",
            "--libav-format",
            "mpegts",
            "--bitrate",
            str(stream.bitrate),
            "--inline",
            "--low-latency",
            "-o",
            stream.udp_sink,
        ]
        cmd.extend(_image_tuning_args(self._config.image_tuning.profile))
        return cmd

    def _build_ffmpeg_command(self) -> list[str]:
        # ffmpeg đọc từ đích UDP và phát vào đường dẫn RTSP cố định.
        stream_name = self._identity.stream_path.lstrip("/")
        target_rtsp = f"rtsp://127.0.0.1:{self._port}/{stream_name}"
        return [
            self._ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "+genpts+nobuffer+igndts+discardcorrupt",
            "-flags",
            "low_delay",
            "-f",
            "mpegts",
            "-i",
            self._config.stream.udp_sink,
            "-an",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            target_rtsp,
        ]

    def _read_stderr(self, proc: subprocess.Popen, tag: str) -> None:
        # Đọc stderr liên tục để tránh đầy bộ đệm và mất log chẩn đoán.
        if proc.stderr is None:
            return
        for raw_line in proc.stderr:
            if isinstance(raw_line, bytes):
                text = raw_line.decode("utf-8", errors="replace").strip()
            else:
                text = raw_line.strip()
            if text:
                self._logger.debug("%s: %s", tag, text)

    def _attach_stderr_logger(self, proc: subprocess.Popen, tag: str) -> None:
        # Mỗi tiến trình có một luồng riêng để theo dõi stderr.
        threading.Thread(
            target=self._read_stderr,
            args=(proc, tag),
            daemon=True,
        ).start()

    def _terminate_process(self, proc: subprocess.Popen | None, name: str) -> None:
        # Ưu tiên terminate sạch, chỉ kill cứng khi thật sự cần.
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._logger.warning("Force killing %s process", name)
                proc.kill()
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

    def _ensure_port_free(self) -> None:
        # Không tự đổi port để tránh lệch URL với server.
        listeners = get_port_listeners(self._port)
        if listeners:
            raise RuntimeError(
                f"RTSP port {self._port} is occupied (PID(s): {sorted(list(listeners))}). "
                "Resolve conflict manually; port is not changed automatically."
            )

    def start(self) -> None:
        with self._lock:
            # Đảm bảo gọi lặp an toàn: đang chạy thì không khởi động lại.
            if self.is_running():
                return

            # Kiểm tra xung đột cổng trước khi tạo tiến trình mới.
            self._ensure_port_free()

            # 1) Khởi động MediaMTX trước để ffmpeg có nơi phát luồng.
            mediamtx_cmd = self._build_mediamtx_command()
            self._logger.info("Starting mediamtx: %s", " ".join(mediamtx_cmd))
            self._mediamtx_process = subprocess.Popen(
                mediamtx_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=self._mediamtx_env(),
            )
            self._attach_stderr_logger(self._mediamtx_process, "mediamtx")
            time.sleep(1.0)
            if self._mediamtx_process.poll() is not None:
                raise RuntimeError("mediamtx exited immediately after start.")

            # 2) Khởi động bộ mã hóa camera đẩy TS qua đích UDP.
            rpicam_cmd = self._build_rpicam_command()
            self._logger.info("Starting camera source: %s", " ".join(rpicam_cmd))
            self._rpicam_process = subprocess.Popen(
                rpicam_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._attach_stderr_logger(self._rpicam_process, "rpicam")

            # 3) Khởi động ffmpeg lấy từ đích UDP và phát vào RTSP.
            ffmpeg_cmd = self._build_ffmpeg_command()
            self._logger.info("Starting ffmpeg publisher: %s", " ".join(ffmpeg_cmd))
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._attach_stderr_logger(self._ffmpeg_process, "ffmpeg")

            # Kiểm tra nhanh để phát hiện lỗi vỡ đường ống ngay khi khởi động.
            time.sleep(0.7)
            if self._rpicam_process.poll() is not None:
                raise RuntimeError("rpicam-vid exited immediately after start.")
            if self._ffmpeg_process.poll() is not None:
                raise RuntimeError("ffmpeg publisher exited immediately after start.")

    def stop(self) -> None:
        with self._lock:
            # Dừng theo thứ tự tiến trình phát -> nguồn camera -> server.
            self._terminate_process(self._ffmpeg_process, "ffmpeg")
            self._terminate_process(self._rpicam_process, "rpicam")
            self._terminate_process(self._mediamtx_process, "mediamtx")
            self._ffmpeg_process = None
            self._rpicam_process = None
            self._mediamtx_process = None

    def restart(self) -> None:
        # Khởi động lại theo thứ tự rõ ràng: dừng -> đợi ngắn -> khởi động.
        self.stop()
        time.sleep(0.5)
        self.start()

    def is_running(self) -> bool:
        # Đường ống khỏe khi cả 3 tiến trình đều còn sống.
        mediamtx_ok = self._mediamtx_process is not None and self._mediamtx_process.poll() is None
        rpicam_ok = self._rpicam_process is not None and self._rpicam_process.poll() is None
        ffmpeg_ok = self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None
        return mediamtx_ok and rpicam_ok and ffmpeg_ok

    def health(self) -> PipelineHealth:
        # Trả về tiến trình lỗi đầu tiên để dễ chẩn đoán.
        if self.is_running():
            return PipelineHealth(running=True, detail=None)
        if self._mediamtx_process and self._mediamtx_process.poll() is not None:
            return PipelineHealth(
                running=False,
                detail=f"mediamtx exited code {self._mediamtx_process.returncode}",
            )
        if self._rpicam_process and self._rpicam_process.poll() is not None:
            return PipelineHealth(
                running=False,
                detail=f"rpicam exited code {self._rpicam_process.returncode}",
            )
        if self._ffmpeg_process and self._ffmpeg_process.poll() is not None:
            return PipelineHealth(
                running=False,
                detail=f"ffmpeg exited code {self._ffmpeg_process.returncode}",
            )
        return PipelineHealth(running=False, detail="Pipeline is not started")
