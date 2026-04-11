const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function wsUrl(path) {
  const base = new URL(API_BASE);
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}${path}`;
}

function withQuery(path, params) {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, value);
    }
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

async function request(path, options) {
  const isFormData = options?.body instanceof FormData;
  const response = await fetch(apiUrl(path), {
    headers: isFormData
      ? options?.headers || {}
      : {
          "Content-Type": "application/json",
          ...(options?.headers || {}),
        },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = `${response.status}`;
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return await response.json();
}

export async function fetchCameras() {
  const data = await request("/api/cameras");
  return data.cameras || [];
}

export async function fetchCameraDetail(cameraId) {
  return await request(`/api/cameras/${cameraId}`);
}

export async function fetchDashboard({ cameraId, fromTs, toTs }) {
  return await request(
    withQuery("/api/analytics/dashboard", {
      camera_id: cameraId,
      from_ts: fromTs,
      to_ts: toTs,
    }),
  );
}

export async function fetchViolationHistory({ cameraId, fromTs, toTs, limit }) {
  const params = {
    camera_id: cameraId,
    from_ts: fromTs,
    to_ts: toTs,
  };
  if (limit !== undefined && limit !== null) {
    params.limit = limit;
  }
  return await request(withQuery("/api/violations/history", params));
}

export async function createCamera(payload) {
  return await request("/api/cameras", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateCamera(cameraId, payload) {
  return await request(`/api/cameras/${cameraId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteCamera(cameraId) {
  return await request(`/api/cameras/${cameraId}`, {
    method: "DELETE",
  });
}

export async function uploadBackgroundImage(cameraId, file) {
  const body = new FormData();
  body.set("file", file);
  return await request(`/api/camera/${cameraId}/background-image`, {
    method: "POST",
    body,
  });
}

export async function deleteBackgroundImage(cameraId) {
  return await request(`/api/camera/${cameraId}/background-image`, {
    method: "DELETE",
  });
}

export function getBackgroundImageUrl(cameraId, revision = "0") {
  return apiUrl(`/api/camera/${cameraId}/background-image?rev=${encodeURIComponent(revision)}`);
}

export function getViolationEvidenceUrl(imageUrlOrPath) {
  if (!imageUrlOrPath) return null;
  const value = String(imageUrlOrPath);
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  if (value.startsWith("/")) {
    return apiUrl(value);
  }
  return apiUrl(`/api/violations/evidence/${encodeURIComponent(value).replaceAll("%2F", "/")}`);
}

export function connectTracks(cameraId, onMessage) {
  const socket = new WebSocket(`${wsUrl("/ws/tracks")}?camera_id=${encodeURIComponent(cameraId)}`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "track") {
      onMessage(message);
    }
  };
  return socket;
}

export function connectViolations(cameraId, onMessage) {
  const path = cameraId ? `/ws/violations?camera_id=${encodeURIComponent(cameraId)}` : "/ws/violations";
  const socket = new WebSocket(wsUrl(path));
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "violation") {
      onMessage(message.event);
    }
  };
  return socket;
}

export function getCameraPreviewUrl(cameraId) {
  return apiUrl(`/api/cameras/${cameraId}/preview`);
}

