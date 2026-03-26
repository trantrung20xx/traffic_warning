import {
  CameraInfo,
  LanesResponse,
  StatsRow,
  TrackMessage,
  ViolationEvent,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function apiUrl(path: string) {
  return `${API_BASE}${path}`;
}

function wsUrl(path: string) {
  const u = new URL(API_BASE);
  const proto = u.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${u.host}${path}`;
}

export async function fetchCameras(): Promise<CameraInfo[]> {
  const res = await fetch(apiUrl("/api/cameras"));
  if (!res.ok) throw new Error(`fetchCameras failed: ${res.status}`);
  const json = await res.json();
  return json.cameras as CameraInfo[];
}

export async function fetchLanes(cameraId: string): Promise<LanesResponse> {
  const res = await fetch(apiUrl(`/api/cameras/${cameraId}/lanes`));
  if (!res.ok) throw new Error(`fetchLanes failed: ${res.status}`);
  return (await res.json()) as LanesResponse;
}

export async function fetchStats(fromTs?: string, toTs?: string): Promise<StatsRow[]> {
  const qs = new URLSearchParams();
  if (fromTs) qs.set("from_ts", fromTs);
  if (toTs) qs.set("to_ts", toTs);
  const res = await fetch(apiUrl(`/api/stats?${qs.toString()}`));
  if (!res.ok) throw new Error(`fetchStats failed: ${res.status}`);
  const json = await res.json();
  return (json.rows || []) as StatsRow[];
}

export function connectTracks(cameraId: string | null, onMessage: (msg: TrackMessage) => void) {
  const url = wsUrl("/ws/tracks");
  const ws = new WebSocket(cameraId ? `${url}?camera_id=${encodeURIComponent(cameraId)}` : url);

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data) as TrackMessage;
    if (msg.type === "track") onMessage(msg);
  };
  return ws;
}

export function connectViolations(
  cameraId: string | null,
  onMessage: (ev: ViolationEvent) => void,
) {
  const url = wsUrl("/ws/violations");
  const ws = new WebSocket(cameraId ? `${url}?camera_id=${encodeURIComponent(cameraId)}` : url);

  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data) as { type: "violation"; event: ViolationEvent };
    if (msg.type === "violation") onMessage(msg.event);
  };
  return ws;
}

