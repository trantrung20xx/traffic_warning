import React, { useCallback, useEffect, useMemo, useState } from "react";
import AppIcon from "../components/AppIcon";
import {
  cycleEdgeCameraImageTuning,
  fetchEdgeCamera,
  fetchEdgeCameras,
  restartEdgeCameraStream,
  rescanEdgeCameras,
  startEdgeCameraStream,
  stopEdgeCameraStream,
} from "../api";

const IMAGE_TUNING_SHORT_LABELS = Object.freeze({
  normal: "Thường",
  low_light: "Tối",
  bright_scene: "Sáng",
  sharpness_safe: "Nét",
  disabled: "Tắt",
});

function formatLastSeen(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("vi-VN");
}

function getStatusBadgeClass(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "online") return "badge success";
  if (normalized === "offline") return "badge warning";
  return "badge subtle";
}

function normalizeStreamState(row) {
  if (row?.stream_running === true) return "running";
  if (row?.stream_enabled === true) return "starting";
  if (row?.stream_enabled === false) return "stopped";
  return "unknown";
}

function isStreamStarted(row) {
  return row?.stream_running === true;
}

function isEdgeOnline(row) {
  return String(row?.status || "").toLowerCase() === "online";
}

function getImageTuningButtonLabel(profile) {
  const key = String(profile || "").trim().toLowerCase();
  const shortLabel = IMAGE_TUNING_SHORT_LABELS[key] || "Mặc định";
  return `Ảnh: ${shortLabel}`;
}

export default function EdgeCamerasView() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState("");

  const loadRows = useCallback(async ({ silent = false } = {}) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await fetchEdgeCameras();
      setRows(Array.isArray(data) ? data : []);
    } catch (loadError) {
      if (!silent) {
        setError(loadError?.message || "Không thể tải danh sách edge camera.");
        setRows([]);
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      // Poll trạng thái ngắn chu kỳ để khi Pi tắt/mất kết nối, UI đổi offline sớm.
      loadRows({ silent: true });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadRows]);

  const hasRows = rows.length > 0;
  const sortedRows = useMemo(
    () => [...rows].sort((left, right) => String(left.camera_id || "").localeCompare(String(right.camera_id || ""), "vi")),
    [rows],
  );

  const handleRescan = useCallback(async () => {
    setBusyAction("rescan");
    setError("");
    try {
      await rescanEdgeCameras();
      await loadRows();
    } catch (rescanError) {
      setError(rescanError?.message || "Không thể quét lại edge camera.");
    } finally {
      setBusyAction("");
    }
  }, [loadRows]);

  const handleAction = useCallback(
    async (row, action) => {
      const cameraId = String(row?.camera_id || "");
      if (!cameraId) return;
      const key =
        action === "restart"
          ? `${cameraId}:restart`
          : action === "tuning"
            ? `${cameraId}:tuning`
            : `${cameraId}:toggle`;
      setBusyAction(key);
      setError("");
      try {
        let response = null;
        if (action === "start") response = await startEdgeCameraStream(cameraId);
        if (action === "stop") response = await stopEdgeCameraStream(cameraId);
        if (action === "restart") response = await restartEdgeCameraStream(cameraId);
        if (action === "tuning") response = await cycleEdgeCameraImageTuning(cameraId);

        const cameraPayload = response?.camera;
        if (cameraPayload && cameraPayload.camera_id) {
          setRows((prevRows) =>
            prevRows.map((item) => (String(item.camera_id || "") === cameraId ? { ...item, ...cameraPayload } : item)),
          );
        } else {
          try {
            const latest = await fetchEdgeCamera(cameraId);
            if (latest && latest.camera_id) {
              setRows((prevRows) =>
                prevRows.map((item) => (String(item.camera_id || "") === cameraId ? { ...item, ...latest } : item)),
              );
            }
          } catch {
            // Nếu sync từng camera lỗi thì vẫn fallback reload toàn bộ danh sách.
          }
        }

        await loadRows({ silent: true });
      } catch (actionError) {
        if (action === "tuning") {
          setError(actionError?.message || "Không thể đổi chế độ ảnh của edge camera.");
        } else {
          setError(actionError?.message || "Không thể gửi lệnh điều khiển stream.");
        }
      } finally {
        setBusyAction("");
      }
    },
    [loadRows],
  );

  return (
    <div className="edge-cameras-layout">
      <section className="panel edge-cameras-panel">
        <div className="panel-header">
          <div>
            <div className="panel-kicker">Edge Discovery</div>
            <div className="title-with-icon">
              <span className="panel-title-icon">
                <AppIcon name="server" size={20} />
              </span>
              <h2>Edge Cameras</h2>
            </div>
          </div>
          <button className="button secondary" onClick={handleRescan} disabled={loading || busyAction === "rescan"}>
            <AppIcon name="redo" />
            {busyAction === "rescan" ? "Đang quét..." : "Rescan"}
          </button>
        </div>

        {loading && !hasRows ? <div className="empty-state">Đang tải danh sách edge camera...</div> : null}
        {error ? <div className="message-bar warning">{error}</div> : null}

        {!loading && !hasRows ? (
          <div className="empty-state">
            Không tìm thấy edge camera. Hãy kiểm tra Raspberry Pi, avahi/mDNS, cùng mạng LAN, firewall, và service
            traffic_camera_node.
          </div>
        ) : null}

        {hasRows ? (
          <div className="edge-camera-table-wrap">
            <table className="edge-camera-table">
              <thead>
                <tr>
                  <th>camera_id</th>
                  <th>host</th>
                  <th>api_port</th>
                  <th>rtsp_url</th>
                  <th>status</th>
                  <th>stream</th>
                  <th>last_seen</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row) => (
                  <tr key={row.camera_id}>
                    <td>{row.camera_id || "-"}</td>
                    <td>{row.host || "-"}</td>
                    <td>{row.api_port ?? "-"}</td>
                    <td className="edge-camera-rtsp-cell">{row.rtsp_url || "-"}</td>
                    <td>
                      <span className={getStatusBadgeClass(row.status)}>{row.status || "unknown"}</span>
                      {row.node_status ? <div className="edge-camera-node-status">{String(row.node_status)}</div> : null}
                    </td>
                    <td>{normalizeStreamState(row)}</td>
                    <td>{formatLastSeen(row.last_seen)}</td>
                    <td className="edge-camera-actions">
                      <button
                        className="button secondary compact-button edge-profile-button"
                        onClick={() => handleAction(row, "tuning")}
                        disabled={!isEdgeOnline(row) || busyAction.startsWith(`${row.camera_id}:`)}>
                        {busyAction === `${row.camera_id}:tuning`
                          ? "Đang đổi"
                          : getImageTuningButtonLabel(row.image_tuning_profile)}
                      </button>
                      <button
                        className={`button compact-button ${isStreamStarted(row) ? "warning-action" : "secondary"}`}
                        onClick={() => handleAction(row, isStreamStarted(row) ? "stop" : "start")}
                        disabled={!isEdgeOnline(row) || busyAction.startsWith(`${row.camera_id}:`)}>
                        {busyAction === `${row.camera_id}:toggle`
                          ? "Đang gửi..."
                          : isStreamStarted(row)
                            ? "Stop Stream"
                            : "Start Stream"}
                      </button>
                      <button
                        className="button danger compact-button"
                        onClick={() => handleAction(row, "restart")}
                        disabled={!isEdgeOnline(row) || busyAction.startsWith(`${row.camera_id}:`)}>
                        Restart Stream
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
