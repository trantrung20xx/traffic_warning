export const MANEUVERS = ["left", "straight", "right", "u_turn"];

export const MANEUVER_LABELS = {
  left: "Rẽ trái",
  straight: "Đi thẳng",
  right: "Rẽ phải",
  u_turn: "Quay đầu",
};

export const VEHICLE_TYPE_LABELS = {
  motorcycle: "Xe máy",
  car: "Ô tô",
  truck: "Xe tải",
  bus: "Xe buýt",
};

export const VIOLATION_LABELS = {
  wrong_lane: "Đi sai làn",
  turn_left_not_allowed: "Rẽ trái không đúng quy định",
  turn_right_not_allowed: "Rẽ phải không đúng quy định",
  turn_straight_not_allowed: "Đi thẳng không đúng quy định",
  turn_u_turn_not_allowed: "Quay đầu không đúng quy định",
};

export const CAMERA_TYPE_LABELS = {
  roadside: "Bên đường",
  overhead: "Trên cao",
  intersection: "Nút giao",
};

export function nowLocalInput() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

export function startOfDayLocalInput() {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

export function toIsoOrNull(value) {
  if (!value) return null;
  return new Date(value).toISOString();
}

export function formatTimestamp(value) {
  if (!value) return "-";
  const dt = new Date(value);
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(dt);
}

export function getManeuverLabel(value) {
  return MANEUVER_LABELS[value] || value;
}

export function getVehicleTypeLabel(value) {
  return VEHICLE_TYPE_LABELS[value] || value;
}

export function getViolationLabel(value) {
  return VIOLATION_LABELS[value] || value;
}

export function getCameraTypeLabel(value) {
  return CAMERA_TYPE_LABELS[value] || value;
}

export function createEmptyLane(laneId, frameWidth, frameHeight) {
  const left = 80 + (laneId - 1) * 40;
  const top = Math.max(120, Math.round(frameHeight * 0.55));
  return {
    lane_id: laneId,
    polygon: [
      [left, top],
      [Math.min(frameWidth - 80, left + 280), top],
      [Math.min(frameWidth - 40, left + 320), frameHeight - 40],
      [left + 40, frameHeight - 40],
    ],
    allowed_maneuvers: ["straight"],
    allowed_lane_changes: [laneId],
    turn_regions: {},
  };
}

export function createCameraDraft(cameraId = "") {
  return {
    camera: {
      camera_id: cameraId,
      rtsp_url: "",
      camera_type: "roadside",
      view_direction: "",
      frame_width: 1280,
      frame_height: 720,
      location: {
        road_name: "",
        intersection_name: "",
        gps_lat: "",
        gps_lng: "",
      },
      monitored_lanes: [1],
    },
    lane_config: {
      camera_id: cameraId,
      frame_width: 1280,
      frame_height: 720,
      lanes: [createEmptyLane(1, 1280, 720)],
    },
  };
}

export function normalizeCameraDetail(detail) {
  const draft = detail?.camera ? detail : createCameraDraft();
  const camera = {
    ...draft.camera,
    view_direction: draft.camera.view_direction || "",
    location: {
      road_name: draft.camera.location?.road_name || "",
      intersection_name: draft.camera.location?.intersection_name || "",
      gps_lat: draft.camera.location?.gps_lat ?? "",
      gps_lng: draft.camera.location?.gps_lng ?? "",
    },
  };
  const laneConfig = {
    ...draft.lane_config,
    frame_width: draft.lane_config?.frame_width || camera.frame_width || 1280,
    frame_height: draft.lane_config?.frame_height || camera.frame_height || 720,
    lanes: (draft.lane_config?.lanes || []).map((lane) => ({
      lane_id: lane.lane_id,
      polygon: lane.polygon || [],
      allowed_maneuvers: lane.allowed_maneuvers || [],
      allowed_lane_changes: lane.allowed_lane_changes || [lane.lane_id],
      turn_regions: lane.turn_regions || {},
    })),
  };
  return {
    camera,
    lane_config: laneConfig,
  };
}

export function buildPayload(draft) {
  const frameWidth = Number(draft.camera.frame_width) || 1280;
  const frameHeight = Number(draft.camera.frame_height) || 720;
  const lanes = draft.lane_config.lanes.map((lane) => ({
    lane_id: Number(lane.lane_id),
    polygon: (lane.polygon || []).map(([x, y]) => [Number(x), Number(y)]),
    allowed_maneuvers: lane.allowed_maneuvers || [],
    allowed_lane_changes: (lane.allowed_lane_changes || []).map((value) => Number(value)),
    turn_regions: Object.fromEntries(
      Object.entries(lane.turn_regions || {})
        .filter(([, points]) => Array.isArray(points) && points.length >= 3)
        .map(([key, points]) => [key, points.map(([x, y]) => [Number(x), Number(y)])]),
    ),
  }));
  return {
    camera: {
      camera_id: draft.camera.camera_id.trim(),
      rtsp_url: draft.camera.rtsp_url.trim(),
      camera_type: draft.camera.camera_type,
      view_direction: draft.camera.view_direction.trim() || null,
      frame_width: frameWidth,
      frame_height: frameHeight,
      monitored_lanes: lanes.map((lane) => lane.lane_id),
      location: {
        road_name: draft.camera.location.road_name.trim(),
        intersection_name: draft.camera.location.intersection_name.trim() || null,
        gps_lat: draft.camera.location.gps_lat === "" ? null : Number(draft.camera.location.gps_lat),
        gps_lng: draft.camera.location.gps_lng === "" ? null : Number(draft.camera.location.gps_lng),
      },
    },
    lane_config: {
      camera_id: draft.camera.camera_id.trim(),
      frame_width: frameWidth,
      frame_height: frameHeight,
      lanes,
    },
  };
}
