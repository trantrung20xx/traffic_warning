const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function wsUrl(path) {
  const u = new URL(API_BASE);
  const proto = u.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${u.host}${path}`;
}

export async function fetchCameras() {
  const res = await fetch(apiUrl("/api/cameras"));
  if (!res.ok) throw new Error(`fetchCameras failed: ${res.status}`);
  const json = await res.json();
  return json.cameras || [];
}

export async function fetchLanes(cameraId) {
  const res = await fetch(apiUrl(`/api/cameras/${cameraId}/lanes`));
  if (!res.ok) throw new Error(`fetchLanes failed: ${res.status}`);
  return await res.json();
}

export async function fetchStats(fromTs, toTs) {
  const qs = new URLSearchParams();
  if (fromTs) qs.set("from_ts", fromTs);
  if (toTs) qs.set("to_ts", toTs);
  const res = await fetch(apiUrl(`/api/stats?${qs.toString()}`));
  if (!res.ok) throw new Error(`fetchStats failed: ${res.status}`);
  const json = await res.json();
  return json.rows || [];
}

export function connectTracks(cameraId, onMessage) {
  const url = wsUrl("/ws/tracks");
  const ws = new WebSocket(cameraId ? `${url}?camera_id=${encodeURIComponent(cameraId)}` : url);

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "track") onMessage(msg);
  };
  return ws;
}

export function connectViolations(cameraId, onMessage) {
  const url = wsUrl("/ws/violations");
  const ws = new WebSocket(cameraId ? `${url}?camera_id=${encodeURIComponent(cameraId)}` : url);

  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "violation") onMessage(msg.event);
  };
  return ws;
}

/** MJPEG stream URL for <img src="..."> (preview only; AI runs on backend pipeline). */
export function getCameraPreviewUrl(cameraId) {
  return apiUrl(`/api/cameras/${cameraId}/preview`);
}

