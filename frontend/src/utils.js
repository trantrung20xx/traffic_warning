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

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function normalizePoint([x, y], frameWidth, frameHeight) {
  return [clamp(Number(x) / Math.max(frameWidth, 1), 0, 1), clamp(Number(y) / Math.max(frameHeight, 1), 0, 1)];
}

export function denormalizePoint([x, y], frameWidth, frameHeight) {
  return [Number(x) * frameWidth, Number(y) * frameHeight];
}

export function normalizePoints(points, frameWidth, frameHeight) {
  return (points || []).map((point) => normalizePoint(point, frameWidth, frameHeight));
}

export function denormalizePoints(points, frameWidth, frameHeight) {
  return (points || []).map((point) => denormalizePoint(point, frameWidth, frameHeight));
}

export function denormalizeLane(lane, frameWidth, frameHeight) {
  return {
    ...lane,
    polygon: denormalizePoints(lane.polygon || [], frameWidth, frameHeight),
    turn_regions: Object.fromEntries(
      Object.entries(lane.turn_regions || {}).map(([maneuver, points]) => [
        maneuver,
        denormalizePoints(points, frameWidth, frameHeight),
      ]),
    ),
  };
}

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
    polygon: normalizePoints(
      [
      [left, top],
      [Math.min(frameWidth - 80, left + 280), top],
      [Math.min(frameWidth - 40, left + 320), frameHeight - 40],
      [left + 40, frameHeight - 40],
      ],
      frameWidth,
      frameHeight,
    ),
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
    // Polygons stay normalized in state and payload so the manual config remains
    // resolution-independent. Rendering converts them back to canvas pixels.
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

function orientation(a, b, c) {
  const value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1]);
  if (Math.abs(value) < 1e-9) return 0;
  return value > 0 ? 1 : 2;
}

function onSegment(a, b, c) {
  return (
    Math.min(a[0], c[0]) <= b[0] &&
    b[0] <= Math.max(a[0], c[0]) &&
    Math.min(a[1], c[1]) <= b[1] &&
    b[1] <= Math.max(a[1], c[1])
  );
}

function segmentsIntersect(a1, a2, b1, b2) {
  const o1 = orientation(a1, a2, b1);
  const o2 = orientation(a1, a2, b2);
  const o3 = orientation(b1, b2, a1);
  const o4 = orientation(b1, b2, a2);

  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(a1, b1, a2)) return true;
  if (o2 === 0 && onSegment(a1, b2, a2)) return true;
  if (o3 === 0 && onSegment(b1, a1, b2)) return true;
  if (o4 === 0 && onSegment(b1, a2, b2)) return true;
  return false;
}

export function polygonSelfIntersects(points) {
  if (!Array.isArray(points) || points.length < 4) return false;

  for (let i = 0; i < points.length; i += 1) {
    const a1 = points[i];
    const a2 = points[(i + 1) % points.length];

    for (let j = i + 1; j < points.length; j += 1) {
      const b1 = points[j];
      const b2 = points[(j + 1) % points.length];

      const sharesVertex =
        i === j ||
        (i + 1) % points.length === j ||
        i === (j + 1) % points.length;

      if (sharesVertex) continue;
      if (segmentsIntersect(a1, a2, b1, b2)) return true;
    }
  }

  return false;
}

export function validatePolygonDraft(draft) {
  const errors = [];
  const warnings = [];

  (draft?.lane_config?.lanes || []).forEach((lane) => {
    const lanePoints = lane.polygon || [];
    if (lanePoints.length < 3) {
      errors.push(`Làn ${lane.lane_id} phải có polygon ít nhất 3 điểm.`);
    } else if (polygonSelfIntersects(lanePoints)) {
      warnings.push(`Polygon của làn ${lane.lane_id} đang tự cắt nhau.`);
    }

    Object.entries(lane.turn_regions || {}).forEach(([maneuver, points]) => {
      if (!Array.isArray(points) || points.length === 0) return;
      if (points.length < 3) {
        errors.push(`Vùng rẽ "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} phải có ít nhất 3 điểm.`);
      } else if (polygonSelfIntersects(points)) {
        warnings.push(`Vùng rẽ "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} đang tự cắt nhau.`);
      }
    });
  });

  return { errors, warnings };
}
