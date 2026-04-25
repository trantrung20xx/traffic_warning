export const MANEUVERS = ["left", "straight", "right", "u_turn"];
export const LANE_EDIT_TARGETS = ["lane_polygon", "approach_zone", "commit_gate", "commit_line"];
export const MANEUVER_EDIT_TARGETS = ["movement_path", "exit_line", "exit_zone"];
export const CORRIDOR_PRESET_OPTIONS = ["narrow", "normal", "wide"];
const POLYGON_GEOMETRY_TARGETS = ["lane_polygon", "approach_zone", "commit_gate", "exit_zone", "turn_corridor"];
const LINE_GEOMETRY_TARGETS = ["commit_line", "exit_line"];
const POLYLINE_GEOMETRY_TARGETS = ["movement_path"];

export const MANEUVER_LABELS = {
  left: "Rẽ trái",
  straight: "Đi thẳng",
  right: "Rẽ phải",
  u_turn: "Quay đầu",
};

export const LANE_TARGET_LABELS = {
  lane_polygon: "Biên làn xe",
  approach_zone: "Vùng chuẩn bị rẽ",
  commit_gate: "Vùng bắt đầu rẽ",
  commit_line: "Vạch bắt đầu rẽ",
};

export const MANEUVER_TARGET_LABELS = {
  movement_path: "Đường đi",
  exit_line: "Vạch xác nhận đầu ra",
  exit_zone: "Vùng xác nhận đầu ra",
};

export const CORRIDOR_PRESET_LABELS = {
  narrow: "Hẹp",
  normal: "Tiêu chuẩn",
  wide: "Rộng",
};

export const VEHICLE_TYPES = ["motorcycle", "car", "truck", "bus"];

export const VEHICLE_TYPE_LABELS = {
  motorcycle: "Xe máy",
  car: "Ô tô",
  truck: "Xe tải",
  bus: "Xe buýt",
};

export const VIOLATION_LABELS = {
  wrong_lane: "Đi sai làn",
  vehicle_type_not_allowed: "Loại phương tiện không đúng quy định",
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

export const VIETNAM_TIMEZONE = "Asia/Ho_Chi_Minh";
export const DEFAULT_ANALYTICS_CHART_CONFIG = {
  minute_granularity_max_range_hours: 24,
  hour_granularity_max_range_days: 14,
  day_granularity_max_range_days: 120,
  week_granularity_max_range_days: 365,
  minute_axis_label_interval_minutes: 60,
  minute_axis_max_ticks: 8,
  hour_axis_max_ticks: 8,
  overview_axis_max_ticks: 7,
  point_markers_max_points: 240,
};

const VIETNAM_UTC_OFFSET_HOURS = 7;

const VIETNAM_DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: VIETNAM_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const VIETNAM_TIMESTAMP_FORMATTER = new Intl.DateTimeFormat("vi-VN", {
  timeZone: VIETNAM_TIMEZONE,
  dateStyle: "short",
  timeStyle: "medium",
});

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatDateTimePartsInVietnam(date) {
  const parts = Object.fromEntries(
    VIETNAM_DATE_TIME_FORMATTER
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour,
    minute: parts.minute,
    second: parts.second,
  };
}

function parseVietnamLocalInput(value) {
  if (!value) return null;
  const match = String(value)
    .trim()
    .match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) return null;

  const [, year, month, day, hour, minute, second = "00"] = match;
  const utcTime = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour) - VIETNAM_UTC_OFFSET_HOURS,
    Number(minute),
    Number(second),
    0,
  );
  return new Date(utcTime);
}

function toVietnamLocalDate(value) {
  const dt = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(dt.getTime())) return null;
  const parts = formatDateTimePartsInVietnam(dt);
  return new Date(
    Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour),
      Number(parts.minute),
      Number(parts.second),
      0,
    ),
  );
}

function fromVietnamLocalDate(localDate) {
  return `${localDate.getUTCFullYear()}-${pad2(localDate.getUTCMonth() + 1)}-${pad2(localDate.getUTCDate())}T${pad2(localDate.getUTCHours())}:${pad2(localDate.getUTCMinutes())}:${pad2(localDate.getUTCSeconds())}+07:00`;
}

function addUtcHours(localDate, hours) {
  const next = new Date(localDate.getTime());
  next.setUTCHours(next.getUTCHours() + hours);
  return next;
}

function addUtcDays(localDate, days) {
  const next = new Date(localDate.getTime());
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

function addUtcMinutes(localDate, minutes) {
  const next = new Date(localDate.getTime());
  next.setUTCMinutes(next.getUTCMinutes() + minutes);
  return next;
}

function addUtcMonths(localDate, months) {
  const next = new Date(localDate.getTime());
  next.setUTCMonth(next.getUTCMonth() + months);
  return next;
}

function formatLocalDateTimeParts(localDate) {
  if (!localDate) return "-";
  return `${pad2(localDate.getUTCDate())}/${pad2(localDate.getUTCMonth() + 1)}/${localDate.getUTCFullYear()} ${pad2(localDate.getUTCHours())}:${pad2(localDate.getUTCMinutes())}`;
}

function formatLocalDayMonthParts(localDate) {
  if (!localDate) return "";
  return `${pad2(localDate.getUTCDate())}/${pad2(localDate.getUTCMonth() + 1)}`;
}

function formatVietnamLocalDateTime(value) {
  const localDate = toVietnamLocalDate(value);
  return formatLocalDateTimeParts(localDate);
}

function formatVietnamLocalDayMonth(value) {
  const localDate = toVietnamLocalDate(value);
  return formatLocalDayMonthParts(localDate);
}

function formatVietnamLocalHourMinute(value) {
  const localDate = toVietnamLocalDate(value);
  if (!localDate) return "";
  return `${pad2(localDate.getUTCHours())}:${pad2(localDate.getUTCMinutes())}`;
}

function formatVietnamLocalMonthYear(value) {
  const localDate = toVietnamLocalDate(value);
  if (!localDate) return "";
  return `${pad2(localDate.getUTCMonth() + 1)}/${localDate.getUTCFullYear()}`;
}

function getTimeBucketRange(value, granularity) {
  const localDate = toVietnamLocalDate(value);
  if (!localDate) return null;

  if (granularity === "minute") {
    localDate.setUTCSeconds(0, 0);
    return { start: localDate, end: addUtcMinutes(localDate, 1) };
  }

  if (granularity === "hour") {
    localDate.setUTCMinutes(0, 0, 0);
    return { start: localDate, end: addUtcHours(localDate, 1) };
  }

  if (granularity === "day") {
    localDate.setUTCHours(0, 0, 0, 0);
    return { start: localDate, end: addUtcDays(localDate, 1) };
  }

  if (granularity === "week") {
    localDate.setUTCHours(0, 0, 0, 0);
    const weekday = localDate.getUTCDay() === 0 ? 7 : localDate.getUTCDay();
    localDate.setUTCDate(localDate.getUTCDate() - weekday + 1);
    return { start: localDate, end: addUtcDays(localDate, 7) };
  }

  localDate.setUTCHours(0, 0, 0, 0);
  localDate.setUTCDate(1);
  return { start: localDate, end: addUtcMonths(localDate, 1) };
}

function mergeBreakdown(target, source) {
  Object.entries(source || {}).forEach(([key, value]) => {
    target[key] = (target[key] || 0) + Number(value || 0);
  });
}

function normalizeTimeSeriesPoint(point, granularity) {
  const range = getTimeBucketRange(point.bucket, granularity);
  if (!range) return null;
  return {
    bucket: fromVietnamLocalDate(range.start),
    bucket_end: fromVietnamLocalDate(range.end),
    total: Number(point.total || 0),
    camera_breakdown: { ...(point.camera_breakdown || {}) },
    vehicle_breakdown: { ...(point.vehicle_breakdown || {}) },
    violation_breakdown: { ...(point.violation_breakdown || {}) },
  };
}

export function normalizeAnalyticsChartConfig(config) {
  return {
    ...DEFAULT_ANALYTICS_CHART_CONFIG,
    ...(config || {}),
  };
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
  const maneuvers = Object.fromEntries(
    Object.entries(lane.maneuvers || {}).map(([maneuver, cfg]) => [
      maneuver,
      {
        ...cfg,
        movement_path: denormalizePoints(cfg.movement_path || [], frameWidth, frameHeight),
        turn_corridor: denormalizePoints(cfg.turn_corridor || [], frameWidth, frameHeight),
        exit_line: denormalizePoints(cfg.exit_line || [], frameWidth, frameHeight),
        exit_zone: denormalizePoints(cfg.exit_zone || [], frameWidth, frameHeight),
      },
    ]),
  );
  return {
    ...lane,
    polygon: denormalizePoints(lane.polygon || [], frameWidth, frameHeight),
    approach_zone: denormalizePoints(lane.approach_zone || [], frameWidth, frameHeight),
    commit_gate: denormalizePoints(lane.commit_gate || [], frameWidth, frameHeight),
    commit_line: denormalizePoints(lane.commit_line || [], frameWidth, frameHeight),
    maneuvers,
  };
}

export function nowLocalInput() {
  const now = formatDateTimePartsInVietnam(new Date());
  return `${now.year}-${now.month}-${now.day}T${now.hour}:${now.minute}`;
}

export function startOfDayLocalInput() {
  const now = formatDateTimePartsInVietnam(new Date());
  return `${now.year}-${now.month}-${now.day}T00:00`;
}

export function toIsoOrNull(value) {
  if (!value) return null;
  const vietnamDate = parseVietnamLocalInput(value);
  if (vietnamDate) {
    return vietnamDate.toISOString();
  }
  const fallback = new Date(value);
  return Number.isNaN(fallback.getTime()) ? null : fallback.toISOString();
}

export function formatTimestamp(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "-";
  return VIETNAM_TIMESTAMP_FORMATTER.format(dt);
}

export function determineTimeSeriesGranularity({ fromTs, toTs, pointCount = 0, chartConfig } = {}) {
  const normalizedChartConfig = normalizeAnalyticsChartConfig(chartConfig);
  const fromMs = fromTs ? new Date(fromTs).getTime() : Number.NaN;
  const toMs = toTs ? new Date(toTs).getTime() : Number.NaN;

  if (!Number.isNaN(fromMs) && !Number.isNaN(toMs) && toMs > fromMs) {
    const durationMs = toMs - fromMs;
    const dayMs = 24 * 60 * 60 * 1000;

    if (durationMs <= normalizedChartConfig.minute_granularity_max_range_hours * 60 * 60 * 1000) return "minute";
    if (durationMs <= normalizedChartConfig.hour_granularity_max_range_days * dayMs) return "hour";
    if (durationMs <= normalizedChartConfig.day_granularity_max_range_days * dayMs) return "day";
    if (durationMs <= normalizedChartConfig.week_granularity_max_range_days * dayMs) return "week";
    return "month";
  }

  if (pointCount <= normalizedChartConfig.minute_granularity_max_range_hours * 60) return "minute";
  if (pointCount <= normalizedChartConfig.hour_granularity_max_range_days * 24) return "hour";
  if (pointCount <= normalizedChartConfig.day_granularity_max_range_days) return "day";
  if (pointCount <= normalizedChartConfig.week_granularity_max_range_days) return "week";
  return "month";
}

export function getTimeSeriesGranularityLabel(granularity) {
  const labels = {
    minute: "phút",
    hour: "giờ",
    day: "ngày",
    week: "tuần",
    month: "tháng",
  };
  return labels[granularity] || "thời gian";
}

export function aggregateTimeSeries(points, granularity) {
  const source = Array.isArray(points) ? points : [];
  if (granularity === "minute" || granularity === "hour") {
    return source
      .map((point) => normalizeTimeSeriesPoint(point, granularity))
      .filter(Boolean)
      .sort((left, right) => new Date(left.bucket).getTime() - new Date(right.bucket).getTime());
  }

  const buckets = new Map();
  source.forEach((point) => {
    const range = getTimeBucketRange(point.bucket, granularity);
    if (!range) return;
    const key = fromVietnamLocalDate(range.start);
    const entry =
      buckets.get(key) ||
      {
        bucket: key,
        bucket_end: fromVietnamLocalDate(range.end),
        total: 0,
        camera_breakdown: {},
        vehicle_breakdown: {},
        violation_breakdown: {},
      };

    entry.total += Number(point.total || 0);
    mergeBreakdown(entry.camera_breakdown, point.camera_breakdown || {});
    mergeBreakdown(entry.vehicle_breakdown, point.vehicle_breakdown || {});
    mergeBreakdown(entry.violation_breakdown, point.violation_breakdown || {});
    buckets.set(key, entry);
  });

  return Array.from(buckets.values()).sort(
    (left, right) => new Date(left.bucket).getTime() - new Date(right.bucket).getTime(),
  );
}

export function formatTimeSeriesAxisLabel(point, granularity) {
  if (!point?.bucket) return { primary: "", secondary: "" };

  if (granularity === "minute") {
    return {
      primary: formatVietnamLocalHourMinute(point.bucket),
      secondary: formatVietnamLocalDayMonth(point.bucket),
    };
  }

  if (granularity === "hour") {
    return {
      primary: formatVietnamLocalHourMinute(point.bucket),
      secondary: formatVietnamLocalDayMonth(point.bucket),
    };
  }

  if (granularity === "day") {
    return {
      primary: formatVietnamLocalDayMonth(point.bucket),
      secondary: "",
    };
  }

  if (granularity === "week") {
    const endLocal = point.bucket_end ? toVietnamLocalDate(point.bucket_end) : null;
    const endDisplay = endLocal ? addUtcMinutes(endLocal, -1) : null;
    return {
      primary: formatVietnamLocalDayMonth(point.bucket),
      secondary: formatLocalDayMonthParts(endDisplay),
    };
  }

  return {
    primary: formatVietnamLocalMonthYear(point.bucket),
    secondary: "",
  };
}

export function formatTimeSeriesTooltip(point, granularity) {
  const startLabel = formatVietnamLocalDateTime(point?.bucket);
  const endLocal = point?.bucket_end ? toVietnamLocalDate(point.bucket_end) : null;
  const endDisplay = endLocal ? addUtcMinutes(endLocal, -1) : null;
  const endLabel = endDisplay ? formatLocalDateTimeParts(endDisplay) : startLabel;

  if (granularity === "minute") {
    return {
      title: startLabel,
      total: `Tổng số vi phạm: ${point?.total ?? 0}`,
    };
  }

  if (granularity === "hour") {
    return {
      title: `Từ ${startLabel} đến ${endLabel}`,
      total: `Tổng số vi phạm: ${point?.total ?? 0}`,
    };
  }

  return {
    title: `Từ ${startLabel} đến ${endLabel}`,
    total: `Tổng số vi phạm: ${point?.total ?? 0}`,
  };
}

export function getManeuverLabel(value) {
  return MANEUVER_LABELS[value] || value;
}

export function getVehicleTypeLabel(value) {
  return VEHICLE_TYPE_LABELS[value] || value;
}

export function getEditTargetGeometryType(target) {
  if (LINE_GEOMETRY_TARGETS.includes(target)) return "line";
  if (POLYLINE_GEOMETRY_TARGETS.includes(target)) return "polyline";
  if (POLYGON_GEOMETRY_TARGETS.includes(target)) return "polygon";
  return "polygon";
}

export function isLineEditTarget(target) {
  return getEditTargetGeometryType(target) === "line";
}

export function isPolylineEditTarget(target) {
  return getEditTargetGeometryType(target) === "polyline";
}

export function isPolygonEditTarget(target) {
  return getEditTargetGeometryType(target) === "polygon";
}

export function getEditTargetGeometryNoun(target) {
  const geometryType = getEditTargetGeometryType(target);
  if (geometryType === "line") return "đường";
  if (geometryType === "polyline") return "đường đi";
  return "vùng";
}

export function parseEditTarget(target) {
  if (LANE_EDIT_TARGETS.includes(target)) {
    return { scope: "lane", key: target, maneuver: null };
  }
  if (MANEUVER_EDIT_TARGETS.includes(target)) {
    return { scope: "lane_maneuver", key: target, maneuver: null };
  }
  return { scope: "lane", key: "lane_polygon", maneuver: null };
}

export function getEditTargetLabel(target, laneId = null, selectedManeuver = null) {
  const parsed = parseEditTarget(target);
  if (parsed.scope === "lane") {
    const base = LANE_TARGET_LABELS[parsed.key] || parsed.key;
    return laneId != null ? `${base} của làn ${laneId}` : base;
  }
  if (parsed.scope === "lane_maneuver") {
    const base = MANEUVER_TARGET_LABELS[parsed.key] || parsed.key;
    const maneuverLabel = getManeuverLabel(selectedManeuver || parsed.maneuver || "straight");
    if (laneId != null) {
      return `${base} · ${maneuverLabel} · làn ${laneId}`;
    }
    return `${base} · ${maneuverLabel}`;
  }
  return LANE_TARGET_LABELS.lane_polygon;
}

export function getEditTargetMinimumPoints(target) {
  if (target === "movement_path") return 2;
  return isLineEditTarget(target) ? 2 : 3;
}

export function getTargetPoints({ lane, laneConfig, editTarget, selectedManeuver = null }) {
  const parsed = parseEditTarget(editTarget);
  if (parsed.scope === "lane") {
    if (!lane) return [];
    const laneField = parsed.key === "lane_polygon" ? "polygon" : parsed.key;
    return lane[laneField] || [];
  }
  if (parsed.scope === "lane_maneuver") {
    if (!lane) return [];
    const maneuverKey = selectedManeuver || "straight";
    const maneuverCfg = lane.maneuvers?.[maneuverKey] || {};
    return maneuverCfg[parsed.key] || [];
  }
  return [];
}

export function getViolationLabel(value) {
  return VIOLATION_LABELS[value] || value;
}

export function getCameraTypeLabel(value) {
  return CAMERA_TYPE_LABELS[value] || value;
}

function createDefaultManeuverConfig(maneuver, { allowed = false } = {}) {
  const preset = maneuver === "u_turn" ? "wide" : "normal";
  return {
    enabled: true,
    allowed,
    movement_path: [],
    corridor_preset: preset,
    corridor_width_px: null,
    turn_corridor: [],
    exit_line: [],
    exit_zone: [],
  };
}

export function createDefaultLaneManeuvers({ allowedManeuvers = [] } = {}) {
  const allowedSet = new Set(allowedManeuvers || []);
  return Object.fromEntries(
    MANEUVERS.map((maneuver) => [maneuver, createDefaultManeuverConfig(maneuver, { allowed: allowedSet.has(maneuver) })]),
  );
}

export function normalizeLaneManeuvers(lane = {}, laneConfig = {}) {
  const source = lane.maneuvers || {};
  const allowedSet = new Set(Array.isArray(lane.allowed_maneuvers) ? lane.allowed_maneuvers : []);

  const maneuvers = {};
  MANEUVERS.forEach((maneuver) => {
    const raw = source[maneuver] || {};
    const base = createDefaultManeuverConfig(maneuver, { allowed: false });
    const enabled = raw.enabled ?? base.enabled;
    maneuvers[maneuver] = {
      ...base,
      enabled,
      allowed: enabled ? raw.allowed ?? allowedSet.has(maneuver) : false,
      movement_path: raw.movement_path || [],
      corridor_preset: raw.corridor_preset || base.corridor_preset,
      corridor_width_px: raw.corridor_width_px ?? null,
      turn_corridor: raw.turn_corridor || [],
      exit_line: raw.exit_line || [],
      exit_zone: raw.exit_zone || [],
    };
  });
  return maneuvers;
}

export function createEmptyLane(laneId, frameWidth, frameHeight) {
  const left = 80 + (laneId - 1) * 40;
  const top = Math.max(120, Math.round(frameHeight * 0.55));
  const right = Math.min(frameWidth - 80, left + 280);
  return {
    lane_id: laneId,
    polygon: normalizePoints(
      [
      [left, top],
      [right, top],
      [Math.min(frameWidth - 40, left + 320), frameHeight - 40],
      [left + 40, frameHeight - 40],
      ],
      frameWidth,
      frameHeight,
    ),
    allowed_maneuvers: ["straight"],
    allowed_lane_changes: [laneId],
    allowed_vehicle_types: [...VEHICLE_TYPES],
    approach_zone: [],
    commit_gate: [],
    commit_line: [],
    maneuvers: createDefaultLaneManeuvers({ allowedManeuvers: ["straight"] }),
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
    lanes: (draft.lane_config?.lanes || []).map((lane) => {
      const maneuvers = normalizeLaneManeuvers(lane, draft.lane_config || {});
      const derivedAllowedManeuvers = MANEUVERS.filter((maneuver) => {
        const cfg = maneuvers[maneuver];
        return Boolean(cfg?.enabled && cfg?.allowed);
      });
      return {
        lane_id: lane.lane_id,
        polygon: lane.polygon || [],
        approach_zone: lane.approach_zone || [],
        commit_gate: lane.commit_gate || [],
        commit_line: lane.commit_line || [],
        allowed_maneuvers: derivedAllowedManeuvers,
        allowed_lane_changes: lane.allowed_lane_changes || [lane.lane_id],
        allowed_vehicle_types: lane.allowed_vehicle_types || [...VEHICLE_TYPES],
        maneuvers,
      };
    }),
  };
  return {
    camera,
    lane_config: laneConfig,
  };
}

export function buildPayload(draft) {
  const frameWidth = Number(draft.camera.frame_width) || 1280;
  const frameHeight = Number(draft.camera.frame_height) || 720;
  const normalizeOptionalGeometry = (points, minimumPoints) => {
    const normalized = (points || []).map(([x, y]) => [Number(x), Number(y)]);
    return normalized.length >= minimumPoints ? normalized : null;
  };
  const lanes = draft.lane_config.lanes.map((lane) => ({
    maneuvers: Object.fromEntries(
      MANEUVERS.map((maneuver) => {
        const cfg = lane.maneuvers?.[maneuver] || {};
        const enabled = Boolean(cfg.enabled ?? true);
        const normalizeManeuverGeometry = (points, minimumPoints) => {
          const normalized = (points || []).map(([x, y]) => [Number(x), Number(y)]);
          return normalized.length >= minimumPoints ? normalized : null;
        };
        return [
          maneuver,
          {
            enabled,
            allowed: enabled && Boolean(cfg.allowed ?? false),
            movement_path: normalizeManeuverGeometry(cfg.movement_path, 2),
            corridor_preset: cfg.corridor_preset || (maneuver === "u_turn" ? "wide" : "normal"),
            corridor_width_px: cfg.corridor_width_px == null ? null : Number(cfg.corridor_width_px),
            turn_corridor: normalizeManeuverGeometry(cfg.turn_corridor, 3),
            exit_line: normalizeManeuverGeometry(cfg.exit_line, 2),
            exit_zone: normalizeManeuverGeometry(cfg.exit_zone, 3),
          },
        ];
      }),
    ),
    lane_id: Number(lane.lane_id),
    // Giữ polygon ở dạng chuẩn hóa ngay trong state và payload để cấu hình không phụ thuộc độ phân giải.
    // Khi vẽ lên canvas mới đổi ngược về pixel.
    polygon: (lane.polygon || []).map(([x, y]) => [Number(x), Number(y)]),
    approach_zone: normalizeOptionalGeometry(lane.approach_zone, 3),
    commit_gate: normalizeOptionalGeometry(lane.commit_gate, 3),
    commit_line: normalizeOptionalGeometry(lane.commit_line, 2),
    allowed_maneuvers: MANEUVERS.filter((maneuver) => {
      const cfg = lane.maneuvers?.[maneuver];
      if (!cfg) return (lane.allowed_maneuvers || []).includes(maneuver);
      return Boolean(cfg.enabled && cfg.allowed);
    }),
    allowed_lane_changes: (lane.allowed_lane_changes || []).map((value) => Number(value)),
    allowed_vehicle_types: lane.allowed_vehicle_types || [...VEHICLE_TYPES],
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

      // Bỏ qua các cạnh kề nhau vì chúng luôn chạm nhau ở đỉnh chung, không phải tự cắt.
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

    if (!Array.isArray(lane.allowed_maneuvers) || lane.allowed_maneuvers.length === 0) {
      errors.push(`Làn ${lane.lane_id} phải có ít nhất một hướng di chuyển được phép.`);
    }

    if (!Array.isArray(lane.allowed_vehicle_types) || lane.allowed_vehicle_types.length === 0) {
      errors.push(`Làn ${lane.lane_id} phải có ít nhất một loại phương tiện được phép.`);
    }

    const approachZone = lane.approach_zone || [];
    if (approachZone.length > 0 && approachZone.length < 3) {
      errors.push(`Vùng tiếp cận của làn ${lane.lane_id} phải có ít nhất 3 điểm.`);
    } else if (approachZone.length >= 3 && polygonSelfIntersects(approachZone)) {
      warnings.push(`Vùng tiếp cận của làn ${lane.lane_id} đang tự cắt nhau.`);
    }

    const commitGate = lane.commit_gate || [];
    const commitLine = lane.commit_line || [];
    if (commitGate.length > 0 && commitGate.length < 3) {
      errors.push(`Vùng bắt đầu rẽ của làn ${lane.lane_id} phải có ít nhất 3 điểm.`);
    } else if (commitGate.length >= 3 && polygonSelfIntersects(commitGate)) {
      warnings.push(`Vùng commit của làn ${lane.lane_id} đang tự cắt nhau.`);
    }
    if (commitLine.length > 0 && commitLine.length !== 2) {
      errors.push(`Vạch bắt đầu rẽ của làn ${lane.lane_id} phải có đúng 2 điểm.`);
    }

    MANEUVERS.forEach((maneuver) => {
      const cfg = lane.maneuvers?.[maneuver] || {};
      const movementPath = cfg.movement_path || [];
      const exitZone = cfg.exit_zone || [];
      const exitLine = cfg.exit_line || [];
      const turnCorridor = cfg.turn_corridor || [];
      const isEnabled = Boolean(cfg.enabled ?? true);
      const isAllowed = isEnabled && Boolean(cfg.allowed ?? false);

      if (movementPath.length > 0 && movementPath.length < 2) {
        errors.push(`Đường đi "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} phải có ít nhất 2 điểm.`);
      }
      if (exitZone.length > 0 && exitZone.length < 3) {
        errors.push(`Vùng xác nhận đầu ra "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} phải có ít nhất 3 điểm.`);
      }
      if (exitLine.length > 0 && exitLine.length !== 2) {
        errors.push(`Vạch xác nhận đầu ra "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} phải có đúng 2 điểm.`);
      }
      if (turnCorridor.length > 0 && turnCorridor.length < 3) {
        errors.push(`Hành lang rẽ "${getManeuverLabel(maneuver)}" của làn ${lane.lane_id} phải có ít nhất 3 điểm.`);
      }
      if (isAllowed && !isEnabled) {
        warnings.push(`Làn ${lane.lane_id}: ${getManeuverLabel(maneuver)} đang cho phép nhưng trạng thái đang tắt.`);
      }
      if (isEnabled && !movementPath.length && !turnCorridor.length && !exitZone.length && !exitLine.length) {
        warnings.push(`Làn ${lane.lane_id}: ${getManeuverLabel(maneuver)} chưa có hình học xác nhận (đường đi/exit).`);
      }
      if (!isEnabled && (movementPath.length || turnCorridor.length || exitZone.length || exitLine.length)) {
        warnings.push(`Làn ${lane.lane_id}: ${getManeuverLabel(maneuver)} đang tắt nhưng vẫn có geometry cấu hình.`);
      }
    });
  });

  return { errors, warnings };
}
