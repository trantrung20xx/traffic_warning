import React, { useCallback, useEffect, useMemo, useState } from "react";
import AppIcon from "../components/AppIcon";
import {
  fetchEdgeCameras,
  restartEdgeCameraStream,
  rescanEdgeCameras,
  startEdgeCameraStream,
  stopEdgeCameraStream,
} from "../api";

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

export default function EdgeCamerasView() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState("");

  const loadRows = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchEdgeCameras();
      setRows(Array.isArray(data) ? data : []);
    } catch (loadError) {
      setError(loadError?.message || "Không thể tải danh sách edge camera.");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRows();
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
    async (cameraId, action) => {
      const key = `${cameraId}:${action}`;
      setBusyAction(key);
      setError("");
      try {
        if (action === "start") await startEdgeCameraStream(cameraId);
        if (action === "stop") await stopEdgeCameraStream(cameraId);
        if (action === "restart") await restartEdgeCameraStream(cameraId);
        await loadRows();
      } catch (actionError) {
        setError(actionError?.message || "Không thể gửi lệnh điều khiển stream.");
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
                    </td>
                    <td>{formatLastSeen(row.last_seen)}</td>
                    <td className="edge-camera-actions">
                      <button
                        className="button secondary compact-button"
                        onClick={() => handleAction(row.camera_id, "start")}
                        disabled={busyAction === `${row.camera_id}:start`}>
                        Start Stream
                      </button>
                      <button
                        className="button warning-action compact-button"
                        onClick={() => handleAction(row.camera_id, "stop")}
                        disabled={busyAction === `${row.camera_id}:stop`}>
                        Stop Stream
                      </button>
                      <button
                        className="button danger compact-button"
                        onClick={() => handleAction(row.camera_id, "restart")}
                        disabled={busyAction === `${row.camera_id}:restart`}>
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

