import { formatTimestamp, getVehicleTypeLabel, getViolationLabel } from "./utils";

export const VIOLATION_FALLBACK = "-";

function hasValue(value) {
  return value !== null && value !== undefined && value !== "";
}

function formatValue(value, fallback = VIOLATION_FALLBACK) {
  return hasValue(value) ? String(value) : fallback;
}

export function getViolationLocationText(location) {
  const parts = [location?.road_name, location?.intersection].filter(hasValue);
  return parts.length > 0 ? parts.join(" · ") : VIOLATION_FALLBACK;
}

export function hasViolationCoreDetails(violation) {
  return Boolean(
    violation &&
      hasValue(violation.camera_id) &&
      hasValue(violation.vehicle_id) &&
      hasValue(violation.vehicle_type) &&
      hasValue(violation.violation) &&
      hasValue(violation.timestamp) &&
      hasValue(violation.location?.road_name),
  );
}

export function buildViolationSections(violation) {
  const location = violation?.location || {};
  const gpsText =
    hasValue(location.gps_lat) && hasValue(location.gps_lng) ? `${location.gps_lat}, ${location.gps_lng}` : null;

  return [
    {
      id: "summary",
      kicker: "Tóm tắt lỗi vi phạm",
      items: [
        {
          label: "Lỗi vi phạm",
          value: hasValue(violation?.violation) ? getViolationLabel(violation.violation) : VIOLATION_FALLBACK,
        },
        {
          label: "Địa điểm vi phạm",
          value: getViolationLocationText(location),
        },
        {
          label: "Thời gian vi phạm",
          value: violation?.timestamp ? formatTimestamp(violation.timestamp) : VIOLATION_FALLBACK,
        },
        {
          label: "Camera phát hiện",
          value: formatValue(violation?.camera_id),
        },
      ],
    },
    {
      id: "vehicle",
      kicker: "Thông tin phương tiện",
      items: [
        {
          label: "ID xe",
          value: hasValue(violation?.vehicle_id) ? `#${violation.vehicle_id}` : VIOLATION_FALLBACK,
        },
        {
          label: "Loại xe",
          value: hasValue(violation?.vehicle_type) ? getVehicleTypeLabel(violation.vehicle_type) : VIOLATION_FALLBACK,
        },
        {
          label: "Làn phát hiện",
          value: formatValue(violation?.lane_id),
        },
      ],
    },
    {
      id: "extra",
      kicker: "Dữ liệu bổ sung",
      items: [
        {
          label: "ID bản ghi",
          value: formatValue(violation?.id),
          hidden: !hasValue(violation?.id),
        },
        {
          label: "GPS",
          value: gpsText,
          hidden: !gpsText,
        },
      ],
    },
  ].filter((section) => section.items.some((item) => !item.hidden));
}
