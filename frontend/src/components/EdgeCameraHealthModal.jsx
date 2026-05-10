import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import AppIcon from "./AppIcon";

function ValueRow({ label, value }) {
  return (
    <div className="edge-health-row">
      <span>{label}</span>
      <strong>{value == null || value === "" ? "-" : String(value)}</strong>
    </div>
  );
}

export default function EdgeCameraHealthModal({
  open,
  camera,
  healthBaseUrl,
  hostOverride,
  health,
  identity,
  streamEnabled,
  loading,
  error,
  actionBusy,
  onClose,
  onRefresh,
  onHostOverrideChange,
  onToggleStream,
  onRestartService,
}) {
  useEffect(() => {
    if (!open) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event) => {
      if (event.key === "Escape" && !actionBusy) onClose?.();
    };
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [actionBusy, onClose, open]);

  if (!open || !camera || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !actionBusy) onClose?.();
      }}
    >
      <div className="edge-health-modal" role="dialog" aria-modal="true" aria-labelledby="edge-health-modal-title">
        <div className="edge-health-header">
          <div>
            <div className="panel-kicker">Edge Camera Node</div>
            <div className="title-with-icon">
              <span className="panel-title-icon">
                <AppIcon name="server" size={18} />
              </span>
              <h3 id="edge-health-modal-title">{camera.camera_id}</h3>
            </div>
          </div>
          <button className="button ghost compact-button" type="button" disabled={actionBusy} onClick={() => onClose?.()}>
            <AppIcon name="x" />
            Đóng
          </button>
        </div>

        <div className="edge-health-badges">
          <span className={error ? "badge danger" : "badge success"}>
            <AppIcon name={error ? "alert" : "check-circle"} />
            {error ? "Kết nối lỗi" : "Kết nối OK"}
          </span>
          <span className="badge subtle">
            <AppIcon name="radio-tower" />
            {healthBaseUrl || "-"}
          </span>
        </div>

        <label className="field edge-health-host-override">
          <span>Host edge override (tùy chọn)</span>
          <input
            type="text"
            value={hostOverride || ""}
            onChange={(event) => onHostOverrideChange?.(event.target.value)}
            placeholder="Ví dụ: 172.20.10.2 hoặc cam-xxx.local"
            disabled={actionBusy}
            autoComplete="off"
          />
          <small className="edge-health-host-override-note">
            Dùng khi trình duyệt không truy cập được host từ RTSP URL mặc định.
          </small>
        </label>

        {loading ? <div className="message-bar">Đang tải health/identity...</div> : null}
        {error ? <div className="message-bar warning">{error}</div> : null}

        <div className="edge-health-grid">
          <section className="edge-health-card">
            <div className="panel-kicker">Health</div>
            <ValueRow label="mDNS hostname" value={health?.mdns_hostname} />
            <ValueRow label="RTSP chính" value={health?.primary_rtsp_url} />
            <ValueRow label="IP fallback" value={health?.ip_fallback_rtsp_url} />
            <ValueRow label="mDNS status" value={health?.mdns_status} />
            <ValueRow label="Stream enabled" value={health?.stream_enabled} />
            <ValueRow label="Stream running" value={health?.stream_running} />
            <ValueRow label="Profile" value={health?.image_tuning_profile} />
            <ValueRow label="Nhiệt độ" value={health?.temperature_c != null ? `${health.temperature_c} C` : "-"} />
            <ValueRow label="Uptime" value={health?.uptime_s != null ? `${health.uptime_s}s` : "-"} />
          </section>

          <section className="edge-health-card">
            <div className="panel-kicker">Identity</div>
            <ValueRow label="camera_id" value={identity?.camera_id} />
            <ValueRow label="node_id" value={identity?.node_id} />
            <ValueRow label="mdns_hostname" value={identity?.mdns_hostname} />
            <ValueRow label="rtsp_port" value={identity?.rtsp_port} />
            <ValueRow label="stream_path" value={identity?.stream_path} />
            <ValueRow label="created_at" value={identity?.created_at} />
          </section>
        </div>

        <div className="edge-health-actions">
          <button className="button secondary" type="button" disabled={loading || actionBusy} onClick={() => onRefresh?.()}>
            <AppIcon name="redo" />
            Làm mới
          </button>
          <button
            className={streamEnabled ? "button warning-action" : "button secondary"}
            type="button"
            disabled={loading || actionBusy}
            onClick={() => onToggleStream?.()}>
            <AppIcon name={streamEnabled ? "camera-off" : "video"} />
            {streamEnabled ? "Tắt stream" : "Bật stream"}
          </button>
          <button className="button danger" type="button" disabled={loading || actionBusy} onClick={() => onRestartService?.()}>
            <AppIcon name="settings" />
            Restart edge node
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
