const DEFAULT_API_PORT = Number.parseInt(import.meta.env.VITE_API_PORT || "8000", 10);

function normalizeBaseUrl(value) {
  // Loại dấu "/" cuối để tránh tạo URL dạng "//api//path".
  return String(value || "").trim().replace(/\/+$/, "");
}

function resolveApiBase() {
  // Ưu tiên biến môi trường; nếu không có thì suy ra cùng host với frontend.
  const explicitBase = normalizeBaseUrl(import.meta.env.VITE_API_BASE);
  if (explicitBase) {
    return explicitBase;
  }

  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    if ((protocol === "http:" || protocol === "https:") && hostname) {
      const apiPort = Number.isFinite(DEFAULT_API_PORT) && DEFAULT_API_PORT > 0 ? DEFAULT_API_PORT : 8000;
      return `${protocol}//${hostname}:${apiPort}`;
    }
  }

  return "http://localhost:8000";
}

const API_BASE = resolveApiBase();

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function wsUrl(path) {
  const base = new URL(API_BASE);
  // Đồng bộ scheme ws/wss theo http/https của API base.
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}${path}`;
}

function withQuery(path, params) {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      // Chỉ đưa tham số có giá trị thực để URL gọn và backend dễ parse.
      search.set(key, value);
    }
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

async function request(path, options) {
  const requestOptions = options || {};
  const { timeoutMs: timeoutRaw, timeoutMessage, ...fetchOptions } = requestOptions;
  const isFormData = fetchOptions?.body instanceof FormData;
  const timeoutMs = Number.isFinite(Number(timeoutRaw)) ? Number(timeoutRaw) : 0;
  const controller = new AbortController();
  const abortTimer =
    timeoutMs > 0
      ? window.setTimeout(() => {
          controller.abort();
        }, timeoutMs)
      : null;
  // FormData để trình duyệt tự set boundary multipart.
  let response;
  try {
    response = await fetch(apiUrl(path), {
      headers: isFormData
        ? fetchOptions?.headers || {}
        : {
            "Content-Type": "application/json",
            ...(fetchOptions?.headers || {}),
          },
      ...fetchOptions,
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(timeoutMessage || "Request timeout");
    }
    throw error;
  } finally {
    if (abortTimer !== null) {
      window.clearTimeout(abortTimer);
    }
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      detail = `${response.status}`;
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return await response.json();
}

function getDownloadFilename(contentDisposition, fallback) {
  if (!contentDisposition) return fallback;
  const encodedMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1]);
    } catch {
      return fallback;
    }
  }
  const simpleMatch = contentDisposition.match(/filename="?([^"]+)"?/i);
  return simpleMatch?.[1] || fallback;
}

async function download(path, { fallbackFilename } = {}) {
  const response = await fetch(apiUrl(path));
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      try {
        detail = await response.text();
      } catch {
        detail = `${response.status}`;
      }
    }
    throw new Error(detail);
  }

  const blob = await response.blob();
  const filename = getDownloadFilename(response.headers.get("Content-Disposition"), fallbackFilename || "download");
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  return { filename };
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

export async function fetchViolationHistory({ cameraId, licensePlate, fromTs, toTs, limit }) {
  const params = {
    camera_id: cameraId,
    license_plate: licensePlate,
    from_ts: fromTs,
    to_ts: toTs,
  };
  if (limit !== undefined && limit !== null) {
    params.limit = limit;
  }
  return await request(withQuery("/api/violations/history", params));
}

export async function fetchViolationDetail(violationId) {
  return await request(`/api/violations/detail/${encodeURIComponent(String(violationId))}`);
}

export async function exportViolationHistory({ format, cameraId, licensePlate, fromTs, toTs }) {
  const extension = format === "xlsx" ? "xlsx" : "csv";
  const params = {
    format: extension,
    camera_id: cameraId,
    license_plate: licensePlate,
    from_ts: fromTs,
    to_ts: toTs,
  };
  const fromDate = fromTs ? String(fromTs).slice(0, 10) : "start";
  const toDate = toTs ? String(toTs).slice(0, 10) : "end";
  return await download(withQuery("/api/violations/export", params), {
    fallbackFilename: `violation_history_${fromDate}_${toDate}.${extension}`,
  });
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
    // URL tuyệt đối từ backend/storage ngoài thì dùng trực tiếp.
    return value;
  }
  if (value.startsWith("/")) {
    // Path tuyệt đối nội bộ API thì gắn base URL.
    return apiUrl(value);
  }
  // Path tương đối evidence lưu trong DB.
  return apiUrl(`/api/violations/evidence/${encodeURIComponent(value).replaceAll("%2F", "/")}`);
}

export function connectTracks(cameraId, onMessage, callbacks = {}) {
  return connectTracksWithCallbacks(cameraId, onMessage, callbacks);
}

export function connectViolations(cameraId, onMessage, callbacks = {}) {
  return connectViolationsWithCallbacks(cameraId, onMessage, callbacks);
}

function parseSocketJson(payload, onInvalidMessage, streamName) {
  try {
    return JSON.parse(payload);
  } catch (error) {
    const parseError = error instanceof Error ? error : new Error(String(error));
    if (onInvalidMessage) {
      onInvalidMessage(parseError, payload);
    } else {
      // Không nuốt lỗi parse âm thầm để vẫn điều tra được nguồn dữ liệu hỏng.
      console.error(`[${streamName}] Invalid WebSocket payload`, parseError);
    }
    return null;
  }
}

function bindSocketLifecycle(socket, streamName, callbacks = {}) {
  const { onOpen, onClose, onError } = callbacks;
  socket.onopen = () => {
    onOpen?.();
  };
  socket.onclose = (event) => {
    onClose?.(event);
  };
  socket.onerror = (event) => {
    if (onError) {
      onError(event);
      return;
    }
    console.error(`[${streamName}] WebSocket connection error`, event);
  };
}

function connectTracksWithCallbacks(cameraId, onMessage, callbacks = {}) {
  // Luồng track đẩy liên tục: payload nguyên bản từ backend TrackMessage.
  const socket = new WebSocket(`${wsUrl("/ws/tracks")}?camera_id=${encodeURIComponent(cameraId)}`);
  bindSocketLifecycle(socket, "tracks", callbacks);
  socket.onmessage = (event) => {
    const message = parseSocketJson(event.data, callbacks.onInvalidMessage, "tracks");
    if (message?.type === "track") {
      onMessage(message);
    }
  };
  return socket;
}

function connectViolationsWithCallbacks(cameraId, onMessage, callbacks = {}) {
  // Luồng violation mang envelope {type, event} để hỗ trợ mở rộng message type.
  const path = cameraId ? `/ws/violations?camera_id=${encodeURIComponent(cameraId)}` : "/ws/violations";
  const socket = new WebSocket(wsUrl(path));
  bindSocketLifecycle(socket, "violations", callbacks);
  socket.onmessage = (event) => {
    const message = parseSocketJson(event.data, callbacks.onInvalidMessage, "violations");
    if (message?.type === "violation") {
      onMessage(message.event);
    }
  };
  return socket;
}

export function getCameraPreviewUrl(cameraId, sessionToken = null) {
  const query = sessionToken ? `?session=${encodeURIComponent(String(sessionToken))}` : "";
  return apiUrl(`/api/cameras/${cameraId}/preview${query}`);
}

export async function fetchEdgeCameras() {
  return await request("/api/edge-cameras", {
    timeoutMs: 6000,
    timeoutMessage: "Quá thời gian tải danh sách edge camera.",
  });
}

export async function fetchEdgeCamera(cameraId) {
  return await request(`/api/edge-cameras/${cameraId}`, {
    timeoutMs: 5000,
    timeoutMessage: "Quá thời gian đồng bộ trạng thái edge camera.",
  });
}

export async function rescanEdgeCameras() {
  return await request("/api/edge-cameras/rescan", {
    method: "POST",
    timeoutMs: 10000,
    timeoutMessage: "Quá thời gian quét lại edge camera.",
  });
}

export async function startEdgeCameraStream(cameraId) {
  return await request(`/api/edge-cameras/${cameraId}/stream/start`, {
    method: "POST",
    timeoutMs: 6000,
  });
}

export async function stopEdgeCameraStream(cameraId) {
  return await request(`/api/edge-cameras/${cameraId}/stream/stop`, {
    method: "POST",
    timeoutMs: 6000,
  });
}

export async function restartEdgeCameraStream(cameraId) {
  return await request(`/api/edge-cameras/${cameraId}/stream/restart`, {
    method: "POST",
    timeoutMs: 7000,
  });
}

export async function cycleEdgeCameraImageTuning(cameraId) {
  return await request(`/api/edge-cameras/${cameraId}/image-tuning/cycle`, {
    method: "POST",
    timeoutMs: 7000,
  });
}
