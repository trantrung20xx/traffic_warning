import React, { useEffect, useMemo, useState } from "react";
import CameraCanvas from "../components/CameraCanvas";
import {
  createCamera,
  deleteBackgroundImage,
  deleteCamera,
  fetchCameraDetail,
  getBackgroundImageUrl,
  uploadBackgroundImage,
  updateCamera,
} from "../api";
import {
  CORRIDOR_PRESET_LABELS,
  CORRIDOR_PRESET_OPTIONS,
  MANEUVERS,
  VEHICLE_TYPES,
  buildPayload,
  createCameraDraft,
  createEmptyLane,
  getEditTargetGeometryNoun,
  getEditTargetLabel,
  getEditTargetMinimumPoints,
  getManeuverLabel,
  getTargetPoints,
  getVehicleTypeLabel,
  isLineEditTarget,
  isPolygonEditTarget,
  normalizeCameraDetail,
  polygonSelfIntersects,
  validatePolygonDraft,
} from "../utils";

function ActionIcon({ type }) {
  const commonProps = {
    viewBox: "0 0 24 24",
    width: 16,
    height: 16,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };

  if (type === "lock") {
    return (
      <svg {...commonProps}>
        <path d="M7 10V8a5 5 0 0 1 10 0v2" />
        <rect x="5" y="10" width="14" height="10" rx="2" />
        <path d="M12 14v2" />
      </svg>
    );
  }

  if (type === "unlock") {
    return (
      <svg {...commonProps}>
        <path d="M7 10V8a5 5 0 0 1 9-3" />
        <rect x="5" y="10" width="14" height="10" rx="2" />
        <path d="M12 14v2" />
      </svg>
    );
  }

  if (type === "undo") {
    return (
      <svg {...commonProps}>
        <path d="M9 7 5 11l4 4" />
        <path d="M19 17a6 6 0 0 0-6-6H5" />
      </svg>
    );
  }

  if (type === "vertex-delete") {
    return (
      <svg {...commonProps}>
        <circle cx="8" cy="8" r="2" />
        <circle cx="16" cy="8" r="2" />
        <circle cx="12" cy="16" r="2" />
        <path d="M9.8 9.2 10.9 14" />
        <path d="M14.2 9.2 13.1 14" />
        <path d="M17.5 17.5 21 21" />
        <path d="M21 17.5 17.5 21" />
      </svg>
    );
  }

  if (type === "polygon-delete") {
    return (
      <svg {...commonProps}>
        <path d="M6 7h12" />
        <path d="M9 7V5h6v2" />
        <path d="M8 7l1 12h6l1-12" />
        <path d="M10 11v5" />
        <path d="M14 11v5" />
      </svg>
    );
  }

  if (type === "image-upload") {
    return (
      <svg {...commonProps}>
        <path d="M12 16V8" />
        <path d="M9 11l3-3 3 3" />
        <rect x="4" y="16" width="16" height="4" rx="1.5" />
        <path d="M6 16v-6a2 2 0 0 1 2-2h1" />
        <path d="M15 8h1a2 2 0 0 1 2 2v6" />
      </svg>
    );
  }

  if (type === "image-delete") {
    return (
      <svg {...commonProps}>
        <rect x="4" y="5" width="12" height="12" rx="2" />
        <path d="m8 11 2 2 2-3 2 3" />
        <circle cx="9" cy="9" r="1" />
        <path d="M17.5 17.5 21 21" />
        <path d="M21 17.5 17.5 21" />
      </svg>
    );
  }

  if (type === "lane-add") {
    return (
      <svg {...commonProps}>
        <path d="M7 5v14" />
        <path d="M13 5v14" />
        <path d="M17 8h4" />
        <path d="M19 6v4" />
      </svg>
    );
  }

  if (type === "lane-delete") {
    return (
      <svg {...commonProps}>
        <path d="M7 5v14" />
        <path d="M13 5v14" />
        <path d="M16 8h5" />
      </svg>
    );
  }

  if (type === "chevron-down") {
    return (
      <svg {...commonProps}>
        <path d="m6 9 6 6 6-6" />
      </svg>
    );
  }

  return null;
}

function MultiSelectDropdown({ summary, placeholder, options, getKey, getLabel, isChecked, onToggle, getNote, isDisabled }) {
  return (
    <details className="lane-change-dropdown">
      <summary className="lane-change-summary">
        <span className="lane-change-summary-text">{summary || placeholder}</span>
        <span className="lane-change-summary-icon">
          <ActionIcon type="chevron-down" />
        </span>
      </summary>
      <div className="lane-change-menu">
        {options.map((option) => {
          const key = getKey(option);
          const checked = isChecked(option);
          const disabled = isDisabled ? isDisabled(option) : false;
          const note = getNote ? getNote(option) : null;
          return (
            <label key={key} className={disabled ? "lane-change-option lane-change-option-disabled" : "lane-change-option"}>
              <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onToggle(option, event.target.checked)} />
              <span className="lane-change-option-label">{getLabel(option)}</span>
              {note ? <span className="lane-change-option-note">{note}</span> : null}
            </label>
          );
        })}
      </div>
    </details>
  );
}

const VALIDATION_LEVELS = ["error", "warning", "info"];
const VALIDATION_LEVEL_ORDER = { error: 0, warning: 1, info: 2 };
const VALIDATION_LEVEL_ICONS = { error: "❌", warning: "⚠️", info: "ℹ️" };

const VALIDATION_MESSAGE_BY_CODE = {
  LANE_POLYGON_INVALID: "Biên làn chưa tạo được vùng hợp lệ.",
  LANE_POLYGON_SELF_INTERSECT: "Biên làn đang bị chéo nhau.",
  COMMIT_LINE_INVALID: "Vạch bắt đầu rẽ chưa hợp lệ.",
  COMMIT_LINE_OUTSIDE_LANE: "Vạch bắt đầu rẽ chưa nằm đúng trong làn.",
  APPROACH_ZONE_INVALID: "Vùng chuẩn bị rẽ quá nhỏ hoặc chưa hợp lệ.",
  APPROACH_ZONE_MISALIGNED: "Vùng chuẩn bị rẽ chưa phủ đúng làn.",
  COMMIT_GATE_INVALID: "Vùng bắt đầu rẽ quá nhỏ hoặc chưa hợp lệ.",
  COMMIT_GATE_MISALIGNED: "Vùng bắt đầu rẽ đang lệch khỏi làn.",
  LANE_OVERLAP_DANGEROUS: "Các làn đang chồng lên nhau nhiều, hệ thống có thể nhận nhầm xe thuộc làn nào.",
  ALLOWED_MANEUVERS_MISMATCH: "Danh sách hướng được phép chưa khớp với các công tắc của từng hướng.",
  MANEUVER_ENABLED_BUT_MISSING_GEOMETRY: "Đã bật nhận diện hướng này nhưng chưa vẽ đủ đường xe đi hoặc vùng/vạch xác nhận đầu ra.",
  MANEUVER_DISABLED_WITH_GEOMETRY: "Hướng này đang tắt nhưng vẫn còn hình đã vẽ.",
  MANEUVER_ALLOWED_BUT_DISABLED: "Hướng này đang được cho phép nhưng nhận diện đang tắt.",
  MOVEMENT_PATH_TOO_SHORT: "Đường xe đi quá ngắn, hệ thống khó hiểu xe đang đi hướng nào.",
  MOVEMENT_PATH_START_FAR_FROM_LANE: "Điểm bắt đầu đường xe đi đang xa làn nguồn.",
  TURN_CORRIDOR_INVALID: "Vùng theo dõi hướng rẽ quá nhỏ hoặc chưa hợp lệ.",
  TURN_CORRIDOR_FAR_FROM_LANE: "Vùng theo dõi hướng rẽ đang xa làn nguồn.",
  EXIT_ZONE_INVALID: "Vùng xác nhận đầu ra quá nhỏ hoặc chưa hợp lệ.",
  EXIT_ZONE_FAR_FROM_PATH: "Vùng xác nhận đầu ra đang xa đường xe đi.",
  EXIT_LINE_INVALID: "Vạch xác nhận đầu ra chưa hợp lệ.",
  EXIT_LINE_FAR_FROM_PATH: "Vạch xác nhận đầu ra đang xa đường xe đi.",
  MISSING_EXIT_CONFIRM: "Chưa có vạch hoặc vùng xác nhận đầu ra.",
  UTURN_PATH_NOT_OPPOSITE: "Đường quay đầu chưa thể hiện rõ hướng quay ngược lại.",
  UTURN_MISSING_EXIT_CONFIRM: "Quay đầu chưa có vạch/vùng xác nhận đầu ra riêng.",
  PATH_OVERLAP_AMBIGUOUS: "Đường đi của nhiều hướng trong cùng làn đang chồng lên nhau nhiều, dễ bị nhận nhầm.",
  UTURN_OVERLAP_HIGH: "Đường quay đầu đang chồng nhiều với hướng khác.",
};

const VALIDATION_SUGGESTION_BY_CODE = {
  LANE_POLYGON_INVALID: "Vẽ lại biên làn bằng ít nhất 3 điểm và tạo thành vùng rõ ràng.",
  LANE_POLYGON_SELF_INTERSECT: "Chỉnh lại thứ tự điểm để vùng làn không tự cắt.",
  COMMIT_LINE_INVALID: "Vẽ vạch bằng đúng 2 điểm cách nhau rõ ràng.",
  COMMIT_LINE_OUTSIDE_LANE: "Đặt vạch cắt qua làn tại nơi xe bắt đầu chọn hướng.",
  APPROACH_ZONE_INVALID: "Mở rộng vùng này ở đoạn xe đi vào trước khi rẽ.",
  APPROACH_ZONE_MISALIGNED: "Kéo vùng này phủ lên phần làn xe đi vào.",
  COMMIT_GATE_INVALID: "Vẽ vùng đủ rộng để xe đi qua ổn định.",
  COMMIT_GATE_MISALIGNED: "Đặt vùng này gần nơi xe bắt đầu bẻ lái.",
  LANE_OVERLAP_DANGEROUS: "Tách rõ biên các làn ở phần đang chồng lên nhau.",
  ALLOWED_MANEUVERS_MISMATCH: "Kiểm tra lại các công tắc bật nhận diện và cho phép trong từng hướng.",
  MANEUVER_ENABLED_BUT_MISSING_GEOMETRY: "Vẽ đường xe đi và thêm ít nhất một vạch hoặc vùng xác nhận đầu ra.",
  MANEUVER_DISABLED_WITH_GEOMETRY: "Bật lại hướng này nếu còn dùng, hoặc xóa các hình đã vẽ nếu không dùng nữa.",
  MANEUVER_ALLOWED_BUT_DISABLED: "Bật nhận diện cho hướng này để hệ thống theo dõi đúng.",
  MOVEMENT_PATH_TOO_SHORT: "Kéo dài đường từ trước điểm bắt đầu rẽ đến nhánh xe đi ra.",
  MOVEMENT_PATH_START_FAR_FROM_LANE: "Kéo điểm đầu đường xe đi về gần làn xe đi vào.",
  TURN_CORRIDOR_INVALID: "Kéo dài đường xe đi hoặc tăng độ rộng vùng theo dõi.",
  TURN_CORRIDOR_FAR_FROM_LANE: "Dịch đường xe đi gần làn nguồn và nhánh rẽ thực tế.",
  EXIT_ZONE_INVALID: "Mở rộng vùng tại nơi xe đã đi ra và ổn định hướng.",
  EXIT_ZONE_FAR_FROM_PATH: "Đặt vùng này gần cuối đường xe đi.",
  EXIT_LINE_INVALID: "Vẽ đúng 2 điểm trên nhánh xe đi ra.",
  EXIT_LINE_FAR_FROM_PATH: "Đặt vạch gần nơi xe rời nhánh và ổn định hướng.",
  MISSING_EXIT_CONFIRM: "Thêm một vạch hoặc vùng xác nhận để hệ thống chắc chắn hơn.",
  UTURN_PATH_NOT_OPPOSITE: "Kéo điểm cuối đường quay đầu về hướng đối diện hướng đi vào.",
  UTURN_MISSING_EXIT_CONFIRM: "Thêm vạch hoặc vùng xác nhận riêng cho quay đầu.",
  PATH_OVERLAP_AMBIGUOUS: "Tách đường đi của từng hướng hoặc thêm vạch xác nhận riêng.",
  UTURN_OVERLAP_HIGH: "Tách rõ đường quay đầu khỏi các hướng rẽ khác.",
};

function cleanValidationText(text) {
  return String(text || "")
    .replaceAll("lane polygon", "biên làn")
    .replaceAll("polygon", "vùng")
    .replaceAll("movement path", "đường xe đi")
    .replaceAll("turn corridor", "vùng theo dõi hướng rẽ")
    .replaceAll("corridor/path", "đường xe đi")
    .replaceAll("corridor", "vùng theo dõi")
    .replaceAll("exit geometry", "vùng/vạch xác nhận đầu ra")
    .replaceAll("exit line/zone", "vạch/vùng xác nhận đầu ra")
    .replaceAll("exit line", "vạch xác nhận đầu ra")
    .replaceAll("exit zone", "vùng xác nhận đầu ra")
    .replaceAll("commit line", "vạch bắt đầu rẽ")
    .replaceAll("commit gate", "vùng bắt đầu rẽ")
    .replaceAll("approach zone", "vùng chuẩn bị rẽ")
    .replaceAll("enabled=true", "đang bật nhận diện")
    .replaceAll("enabled=false", "nhận diện đang tắt")
    .replaceAll("allowed=true", "đang được cho phép");
}

function normalizeValidationLevel(level) {
  const normalized = String(level || "info").toLowerCase();
  return VALIDATION_LEVELS.includes(normalized) ? normalized : "info";
}

function extractLaneLabel(issue) {
  if (issue?.lane_id != null) {
    return `Làn ${issue.lane_id}`;
  }
  const message = String(issue?.message || "");
  const pairMatch = message.match(/Làn\s+(\d+)\s+và\s+(\d+)/i);
  if (pairMatch) {
    return `Làn ${pairMatch[1]} và ${pairMatch[2]}`;
  }
  const singleMatch = message.match(/Làn\s+(\d+)/i);
  return singleMatch ? `Làn ${singleMatch[1]}` : "Cấu hình chung";
}

function validationGroupSortValue(label) {
  const match = String(label).match(/\d+/);
  return match ? Number(match[0]) : Number.MAX_SAFE_INTEGER;
}

function extractManeuverSubject(issue) {
  if (issue?.maneuver) {
    return getManeuverLabel(issue.maneuver);
  }
  const message = String(issue?.message || "");
  const pairMatch = message.match(/'([^']+)'\s+và\s+'([^']+)'/);
  if (pairMatch) {
    return `${getManeuverLabel(pairMatch[1])} và ${getManeuverLabel(pairMatch[2])}`;
  }
  const dashMatch = message.match(/-\s*([a-z_]+):/i);
  return dashMatch ? getManeuverLabel(dashMatch[1]) : "";
}

function extractManeuverKey(issue) {
  const rawManeuver = String(issue?.maneuver || "").trim();
  if (MANEUVERS.includes(rawManeuver)) {
    return rawManeuver;
  }

  const message = String(issue?.message || "");
  const dashMatch = message.match(/-\s*([a-z_]+):/i);
  if (!dashMatch) return null;
  const parsed = String(dashMatch[1] || "").trim();
  return MANEUVERS.includes(parsed) ? parsed : null;
}

function normalizeValidationIssue(issue, index) {
  const source = typeof issue === "string" ? { message: issue } : issue || {};
  const code = source.code || "";
  const laneMatch = String(source.message || "").match(/Làn\s+(\d+)/i);
  const laneIdFromMessage = laneMatch ? Number(laneMatch[1]) : null;
  const laneId = source.lane_id != null ? Number(source.lane_id) : laneIdFromMessage;
  return {
    id: `${code || "validation"}-${source.lane_id ?? "global"}-${source.maneuver || "general"}-${index}`,
    code,
    level: normalizeValidationLevel(source.level),
    laneId,
    laneLabel: extractLaneLabel(source),
    maneuverKey: extractManeuverKey(source),
    maneuverLabel: extractManeuverSubject(source),
    message: VALIDATION_MESSAGE_BY_CODE[code] || cleanValidationText(source.message || "Cấu hình cần được kiểm tra lại."),
    suggestion: VALIDATION_SUGGESTION_BY_CODE[code] || cleanValidationText(source.suggestion || ""),
  };
}

function getValidationIssueScopeKey(issue) {
  return `${issue.laneLabel || "global"}|${issue.maneuverLabel || "general"}`;
}

function removeRedundantValidationIssues(issues, lanesById) {
  const missingGeometryScopes = new Set(
    issues
      .filter((issue) => issue.code === "MANEUVER_ENABLED_BUT_MISSING_GEOMETRY")
      .map((issue) => getValidationIssueScopeKey(issue)),
  );

  return issues.filter((issue) => {
    if (issue.code === "MISSING_EXIT_CONFIRM") {
      if (missingGeometryScopes.has(getValidationIssueScopeKey(issue))) {
        return false;
      }

      const lane = issue.laneId != null ? lanesById.get(issue.laneId) : null;
      const maneuverCfg =
        lane && issue.maneuverKey ? lane.maneuvers?.[issue.maneuverKey] || null : null;
      if (maneuverCfg) {
        const hasExitLine = Array.isArray(maneuverCfg.exit_line) && maneuverCfg.exit_line.length >= 2;
        const hasExitZone = Array.isArray(maneuverCfg.exit_zone) && maneuverCfg.exit_zone.length >= 3;
        if (hasExitLine || hasExitZone) {
          return false;
        }
      }
    }
    return true;
  });
}

function joinValidationSummaryParts(parts) {
  if (parts.length <= 1) return parts[0] || "";
  if (parts.length === 2) return `${parts[0]} và ${parts[1]}`;
  return `${parts.slice(0, -1).join(", ")} và ${parts[parts.length - 1]}`;
}

function getValidationSummaryText(counts) {
  const parts = [];
  if (counts.error) parts.push(`${counts.error} lỗi`);
  if (counts.warning) parts.push(`${counts.warning} cảnh báo`);
  if (counts.info) parts.push(`${counts.info} lưu ý`);
  return `Có ${joinValidationSummaryParts(parts)} cấu hình. Nhấn để xem chi tiết.`;
}

function groupValidationIssues(issues) {
  const groups = new Map();
  issues.forEach((issue) => {
    if (!groups.has(issue.laneLabel)) {
      groups.set(issue.laneLabel, { label: issue.laneLabel, items: [] });
    }
    groups.get(issue.laneLabel).items.push(issue);
  });
  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      items: group.items.sort(
        (a, b) =>
          VALIDATION_LEVEL_ORDER[a.level] - VALIDATION_LEVEL_ORDER[b.level] ||
          String(a.maneuverLabel).localeCompare(String(b.maneuverLabel), "vi"),
      ),
    }))
    .sort((a, b) => validationGroupSortValue(a.label) - validationGroupSortValue(b.label) || a.label.localeCompare(b.label, "vi"));
}

function ValidationIssuesPanel({ issues, lanes }) {
  const [expanded, setExpanded] = useState(false);
  const lanesById = useMemo(
    () => new Map((lanes || []).map((lane) => [Number(lane.lane_id), lane])),
    [lanes],
  );
  const normalizedIssues = useMemo(
    () =>
      removeRedundantValidationIssues(
        issues.map((issue, index) => normalizeValidationIssue(issue, index)),
        lanesById,
      ),
    [issues, lanesById],
  );
  const counts = useMemo(
    () =>
      normalizedIssues.reduce(
        (acc, issue) => ({
          ...acc,
          [issue.level]: acc[issue.level] + 1,
        }),
        { error: 0, warning: 0, info: 0 },
      ),
    [normalizedIssues],
  );
  const dominantLevel = counts.error ? "error" : counts.warning ? "warning" : "info";
  const groups = useMemo(() => groupValidationIssues(normalizedIssues), [normalizedIssues]);

  useEffect(() => {
    setExpanded(false);
  }, [issues]);

  if (normalizedIssues.length === 0) return null;

  return (
    <section className={`validation-issues-panel ${dominantLevel}`}>
      <button
        type="button"
        className="validation-summary-button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span className="validation-summary-icon" aria-hidden="true">
          {VALIDATION_LEVEL_ICONS[dominantLevel]}
        </span>
        <span className="validation-summary-text">{getValidationSummaryText(counts)}</span>
        <span className="validation-summary-action">
          {expanded ? "Thu gọn" : "Xem chi tiết"}
          <span className={expanded ? "validation-chevron expanded" : "validation-chevron"}>
            <ActionIcon type="chevron-down" />
          </span>
        </span>
      </button>

      {expanded ? (
        <div className="validation-detail-panel">
          {groups.map((group) => (
            <div key={group.label} className="validation-detail-group">
              <div className="validation-detail-group-title">{group.label}</div>
              <div className="validation-detail-list">
                {group.items.map((issue) => (
                  <div key={issue.id} className={`validation-detail-item ${issue.level}`}>
                    <span className="validation-detail-level" aria-label={issue.level}>
                      {VALIDATION_LEVEL_ICONS[issue.level]}
                    </span>
                    <div className="validation-detail-copy">
                      <div className="validation-detail-message">
                        {issue.maneuverLabel ? <strong>{issue.maneuverLabel}: </strong> : null}
                        {issue.message}
                      </div>
                      {issue.suggestion ? <div className="validation-detail-suggestion">Cách xử lý: {issue.suggestion}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default function ManagementView({ cameras, selectedCameraId, onSelectCamera, onRefreshCameras }) {
  const [draft, setDraft] = useState(createCameraDraft());
  const [activeCameraId, setActiveCameraId] = useState(selectedCameraId || null);
  const [selectedLaneId, setSelectedLaneId] = useState(1);
  const [selectedManeuver, setSelectedManeuver] = useState("straight");
  const [selectedVertexIndex, setSelectedVertexIndex] = useState(null);
  const [editTarget, setEditTarget] = useState("lane_polygon");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [isNewCamera, setIsNewCamera] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [polygonLocked, setPolygonLocked] = useState(false);
  const [hasBackgroundImage, setHasBackgroundImage] = useState(false);
  const [backgroundRevision, setBackgroundRevision] = useState("0");
  const [backgroundBusy, setBackgroundBusy] = useState(false);
  const [configValidation, setConfigValidation] = useState([]);

  useEffect(() => {
    if (!selectedCameraId && cameras[0]?.camera_id) {
      setActiveCameraId(cameras[0].camera_id);
    }
  }, [selectedCameraId, cameras]);

  useEffect(() => {
    const targetId = activeCameraId || selectedCameraId;
    if (!targetId || isNewCamera) return;
    fetchCameraDetail(targetId)
      .then((detail) => {
        const normalized = normalizeCameraDetail(detail);
        setDraft(normalized);
        setSelectedLaneId(normalized.lane_config.lanes[0]?.lane_id || 1);
        setSelectedManeuver("straight");
        setSelectedVertexIndex(null);
        setIsDirty(false);
        setPolygonLocked(false);
        setHasBackgroundImage(Boolean(detail.has_background_image));
        setBackgroundRevision(`${Date.now()}`);
        setConfigValidation(detail.config_validation || []);
        setMessage(detail.runtime_applied ? "Cấu hình hiện tại đang được hệ thống sử dụng." : "");
      })
      .catch(() => {
        setDraft(createCameraDraft());
        setSelectedManeuver("straight");
        setSelectedVertexIndex(null);
        setIsDirty(false);
        setHasBackgroundImage(false);
        setConfigValidation([]);
      });
  }, [activeCameraId, selectedCameraId, isNewCamera]);

  const selectedLane = useMemo(
    () => draft.lane_config.lanes.find((lane) => lane.lane_id === selectedLaneId) || draft.lane_config.lanes[0] || null,
    [draft, selectedLaneId],
  );

  const selectedManeuverConfig = useMemo(() => {
    if (!selectedLane) return null;
    return selectedLane.maneuvers?.[selectedManeuver] || null;
  }, [selectedLane, selectedManeuver]);
  const selectedManeuverEnabled = Boolean(selectedManeuverConfig?.enabled ?? true);
  const selectedManeuverAllowed = selectedManeuverEnabled && Boolean(selectedManeuverConfig?.allowed ?? false);

  const selectedPoints = useMemo(() => {
    return getTargetPoints({
      lane: selectedLane,
      laneConfig: draft.lane_config,
      editTarget,
      selectedManeuver,
    });
  }, [draft.lane_config, editTarget, selectedLane, selectedManeuver]);

  const polygonStatus = useMemo(() => {
    if (!selectedLane) return { warnings: [] };
    const targetLabel = getEditTargetLabel(editTarget, selectedLane?.lane_id ?? null, selectedManeuver);
    const warnings = [];
    const minimumPoints = getEditTargetMinimumPoints(editTarget);
    const geometryNoun = getEditTargetGeometryNoun(editTarget);
    if (selectedPoints.length > 0 && selectedPoints.length < minimumPoints) {
      warnings.push(`${targetLabel} hiện có dưới ${minimumPoints} điểm, chưa đủ để tạo ${geometryNoun} hợp lệ.`);
    }
    if (isPolygonEditTarget(editTarget) && selectedPoints.length >= 4 && polygonSelfIntersects(selectedPoints)) {
      warnings.push(`${targetLabel} đang tự cắt nhau, nên chỉnh lại để tránh vùng hình học khó kiểm soát.`);
    }
    return { warnings };
  }, [editTarget, selectedLane, selectedPoints, selectedManeuver]);

  useEffect(() => {
    setSelectedVertexIndex(null);
  }, [editTarget, selectedLaneId, selectedManeuver]);

  const updateDraft = (updater, options = {}) => {
    setDraft((current) => {
      const next = updater(current);
      const laneIds = next.lane_config.lanes.map((lane) => Number(lane.lane_id));
      const sanitizedLanes = next.lane_config.lanes.map((lane) => ({
        ...lane,
        allowed_lane_changes: (lane.allowed_lane_changes || [lane.lane_id]).filter((value) => laneIds.includes(Number(value))),
      }));
      return {
        ...next,
        camera: {
          ...next.camera,
          monitored_lanes: laneIds,
        },
        lane_config: {
          ...next.lane_config,
          camera_id: next.camera.camera_id,
          frame_width: Number(next.camera.frame_width) || next.lane_config.frame_width,
          frame_height: Number(next.camera.frame_height) || next.lane_config.frame_height,
          lanes: sanitizedLanes,
        },
      };
    });
    if (options.markDirty !== false) {
      setIsDirty(true);
      setMessage('Đang có thay đổi chưa lưu. Nhấn "Lưu cấu hình làn đường" để áp dụng cấu hình mới.');
    }
  };

  const updateLane = (laneId, updater) => {
    updateDraft((current) => ({
      ...current,
      lane_config: {
        ...current.lane_config,
        lanes: current.lane_config.lanes.map((lane) => (lane.lane_id === laneId ? updater(lane) : lane)),
      },
    }));
  };

  const updateSelectedManeuverConfig = (updater) => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => {
      const maneuvers = lane.maneuvers || {};
      const currentCfg = maneuvers[selectedManeuver] || {
        enabled: true,
        allowed: false,
        movement_path: [],
        corridor_preset: selectedManeuver === "u_turn" ? "wide" : "normal",
        corridor_width_px: null,
        turn_corridor: [],
        exit_line: [],
        exit_zone: [],
      };
      const updatedCfg = updater(currentCfg);
      const nextCfg = {
        ...updatedCfg,
        allowed: Boolean(updatedCfg.enabled ?? true) && Boolean(updatedCfg.allowed ?? false),
      };
      const nextManeuvers = { ...maneuvers, [selectedManeuver]: nextCfg };
      const nextAllowed = MANEUVERS.filter((maneuver) => {
        const cfg = nextManeuvers[maneuver];
        return Boolean(cfg?.enabled && cfg?.allowed);
      });
      return {
        ...lane,
        maneuvers: nextManeuvers,
        allowed_maneuvers: nextAllowed,
      };
    });
  };

  const addLane = () => {
    const existingIds = draft.lane_config.lanes.map((lane) => lane.lane_id);
    const nextLaneId = existingIds.length ? Math.max(...existingIds) + 1 : 1;
    updateDraft((current) => ({
      ...current,
      lane_config: {
        ...current.lane_config,
        lanes: [...current.lane_config.lanes, createEmptyLane(nextLaneId, current.camera.frame_width, current.camera.frame_height)],
      },
    }));
    setSelectedLaneId(nextLaneId);
    setSelectedVertexIndex(null);
    setMessage(`Đã thêm làn ${nextLaneId}.`);
  };

  const removeLane = (laneId) => {
    const remaining = draft.lane_config.lanes.filter((lane) => lane.lane_id !== laneId);
    updateDraft((current) => ({
      ...current,
      lane_config: {
        ...current.lane_config,
        lanes: current.lane_config.lanes.filter((lane) => lane.lane_id !== laneId),
      },
    }));
    setSelectedLaneId(remaining[0]?.lane_id || 1);
    setSelectedVertexIndex(null);
    setMessage(`Đã xóa làn ${laneId}.`);
  };

  const updateTargetGeometry = (updater) => {
    if (!selectedLane) return;
    if (editTarget === "movement_path" || editTarget === "exit_line" || editTarget === "exit_zone") {
      updateLane(selectedLane.lane_id, (lane) => {
        const currentManeuvers = lane.maneuvers || {};
        const currentManeuverCfg = currentManeuvers[selectedManeuver] || {
          enabled: true,
          allowed: false,
          movement_path: [],
          corridor_preset: selectedManeuver === "u_turn" ? "wide" : "normal",
          corridor_width_px: null,
          turn_corridor: [],
          exit_line: [],
          exit_zone: [],
        };
        return {
          ...lane,
          maneuvers: {
            ...currentManeuvers,
            [selectedManeuver]: {
              ...currentManeuverCfg,
              [editTarget]: updater(currentManeuverCfg[editTarget] || []),
            },
          },
        };
      });
      return;
    }

    updateLane(selectedLane.lane_id, (lane) => ({
      ...lane,
      [editTarget === "lane_polygon" ? "polygon" : editTarget]: updater(
        lane[editTarget === "lane_polygon" ? "polygon" : editTarget] || [],
      ),
    }));
  };

  const handleCanvasPoint = (point) => {
    if (!selectedLane) return;
    if (isLineEditTarget(editTarget) && selectedPoints.length >= 2) {
      setMessage("Đối tượng dạng đường chỉ cho phép tối đa 2 điểm.");
      return;
    }
    updateTargetGeometry((points) => [...points, point]);
    setSelectedVertexIndex(selectedPoints.length);
    setMessage(`Đã thêm điểm mới vào ${getEditTargetGeometryNoun(editTarget)}.`);
  };

  const replaceTargetPolygon = (nextPoints) => {
    if (!selectedLane) return;
    updateTargetGeometry(() => nextPoints);
    setMessage("Đã cập nhật polygon trên canvas, chưa lưu xuống backend.");
  };

  const deleteSelectedVertex = () => {
    if (!selectedLane || selectedVertexIndex == null) return;
    updateTargetGeometry((points) => points.filter((_, index) => index !== selectedVertexIndex));
    setSelectedVertexIndex(null);
    setMessage("Đã xóa điểm đang chọn.");
  };

  const undoPoint = () => {
    if (!selectedLane) return;
    updateTargetGeometry((points) => points.slice(0, -1));
    setSelectedVertexIndex(null);
    setMessage("Đã xóa điểm vừa vẽ.");
  };

  const clearPolygon = () => {
    if (!selectedLane) return;
    updateTargetGeometry(() => []);
    setSelectedVertexIndex(null);
    setMessage("Đã xóa toàn bộ polygon hiện tại.");
  };

  const startCreateCamera = () => {
    if (isDirty && !window.confirm("Cấu hình hiện tại chưa lưu. Tạo camera mới và bỏ thay đổi này?")) return;
    setIsNewCamera(true);
    setActiveCameraId(null);
    const draftState = createCameraDraft(`cam_${String(cameras.length + 1).padStart(2, "0")}`);
    setDraft(draftState);
    setSelectedLaneId(1);
    setSelectedManeuver("straight");
    setSelectedVertexIndex(null);
    setEditTarget("lane_polygon");
    setIsDirty(false);
    setPolygonLocked(false);
    setHasBackgroundImage(false);
    setBackgroundRevision("0");
    setMessage("");
  };

  const handleBackgroundUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !draft.camera.camera_id || isNewCamera) return;

    setBackgroundBusy(true);
    try {
      await uploadBackgroundImage(draft.camera.camera_id, file);
      setHasBackgroundImage(true);
      setBackgroundRevision(`${Date.now()}`);
      setMessage("Đã cập nhật ảnh nền cho camera hiện tại.");
    } catch (error) {
      setMessage(error.message || "Không thể upload ảnh nền.");
    } finally {
      setBackgroundBusy(false);
    }
  };

  const handleBackgroundClear = async () => {
    if (!draft.camera.camera_id || isNewCamera) return;
    setBackgroundBusy(true);
    try {
      await deleteBackgroundImage(draft.camera.camera_id);
      setHasBackgroundImage(false);
      setBackgroundRevision(`${Date.now()}`);
      setMessage("Đã xóa ảnh nền của camera hiện tại.");
    } catch (error) {
      setMessage(error.message || "Không thể xóa ảnh nền.");
    } finally {
      setBackgroundBusy(false);
    }
  };

  const saveCurrentCamera = async () => {
    setSaving(true);
    setMessage("");
    try {
      const validation = validatePolygonDraft(draft);
      if (validation.errors.length > 0) {
        setMessage(validation.errors[0]);
        return;
      }
      const payload = buildPayload(draft);
      const response = isNewCamera
        ? await createCamera(payload)
        : await updateCamera(payload.camera.camera_id, payload);
      const savedCameraId = response.camera.camera_id;
      setIsNewCamera(false);
      setActiveCameraId(savedCameraId);
      onSelectCamera(savedCameraId);
      await onRefreshCameras(savedCameraId);
      const freshDetail = await fetchCameraDetail(savedCameraId);
      const normalized = normalizeCameraDetail(freshDetail);
      setDraft(normalized);
      setSelectedLaneId(normalized.lane_config.lanes[0]?.lane_id || 1);
      setSelectedManeuver("straight");
      setSelectedVertexIndex(null);
      setIsDirty(false);
      setHasBackgroundImage(Boolean(freshDetail.has_background_image));
      setBackgroundRevision(`${Date.now()}`);
      setConfigValidation(freshDetail.config_validation || []);
      setMessage(
        response.runtime_applied
          ? "Đã lưu cấu hình và áp dụng ngay vào hệ thống."
          : "Đã lưu cấu hình camera, nhưng chưa áp dụng ngay vào hệ thống.",
      );
    } catch (error) {
      setMessage(error.message || "Không thể lưu cấu hình camera.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!activeCameraId) return;
    const confirmed = window.confirm(`Xóa camera ${activeCameraId}?`);
    if (!confirmed) return;
    setSaving(true);
    setMessage("");
    try {
      await deleteCamera(activeCameraId);
      await onRefreshCameras();
      setIsNewCamera(false);
      setActiveCameraId(null);
      setDraft(createCameraDraft());
      setSelectedLaneId(1);
      setSelectedManeuver("straight");
      setSelectedVertexIndex(null);
      setIsDirty(false);
      setHasBackgroundImage(false);
      setBackgroundRevision("0");
      setConfigValidation([]);
      setMessage(`Đã xóa ${activeCameraId}.`);
    } catch (error) {
      setMessage(error.message || "Không thể xóa camera.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="management-layout">
      <aside className="panel sidebar-panel management-sidebar">
        <div className="panel-header compact management-sidebar-header">
          <div>
            <div className="panel-kicker">Danh mục</div>
            <h3>Danh sách camera</h3>
          </div>
          <button className="button secondary management-add-camera-button" onClick={startCreateCamera}>
            Thêm camera
          </button>
        </div>
        <div className="entity-list management-camera-list">
          {cameras.map((camera) => (
            <button
              key={camera.camera_id}
              className={camera.camera_id === activeCameraId && !isNewCamera ? "camera-card active" : "camera-card"}
              onClick={() => {
                if (isDirty && !window.confirm("Cấu hình hiện tại chưa lưu. Chuyển camera và bỏ thay đổi này?")) return;
                setIsNewCamera(false);
                setActiveCameraId(camera.camera_id);
                onSelectCamera(camera.camera_id);
                setSelectedVertexIndex(null);
                setMessage("");
              }}
            >
              <div className="row-title">{camera.camera_id}</div>
              <div className="row-sub">
                {camera.location.road_name}
                {camera.location.intersection ? ` · ${camera.location.intersection}` : ""}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section className="management-main">
        <section className="panel management-camera-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Thông tin camera</div>
              <h2>{isNewCamera ? "Thêm camera mới" : `Chỉnh sửa ${draft.camera.camera_id || "camera"}`}</h2>
            </div>
            <div className="action-row management-header-actions">
              {!isNewCamera && activeCameraId ? (
                <button className="button danger" onClick={handleDelete} disabled={saving}>
                  Xóa camera
                </button>
              ) : null}
              <button className="button primary" onClick={saveCurrentCamera} disabled={saving || !isDirty}>
                {saving ? "Đang lưu..." : "Lưu cấu hình làn đường"}
              </button>
            </div>
          </div>

          <div className="status-strip management-status-strip">
            <div className={isDirty ? "badge warning" : "badge success"}>
              {isDirty ? "Chưa lưu" : "Đã đồng bộ backend"}
            </div>
            <div className="row-sub">
              {isDirty
                ? "Các thay đổi hiện mới nằm trên giao diện cấu hình và chưa được áp dụng cho hệ thống."
                : "Màn hình giám sát và hệ thống đang sử dụng đúng cấu hình hiện tại."}
            </div>
          </div>

          <div className="management-form-sections">
            <section className="management-subcard">
              <div className="management-subcard-title">RTSP / Kết nối</div>
              <div className="form-grid management-form-grid">
                <label className="field camera-id-field">
                  <span>Camera ID</span>
                  <input
                    className="camera-id-input"
                    value={draft.camera.camera_id}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, camera_id: event.target.value },
                        lane_config: { ...current.lane_config, camera_id: event.target.value },
                      }))
                    }
                    disabled={!isNewCamera}
                  />
                </label>
                <label className="field">
                  <span>Nguồn RTSP / video</span>
                  <input
                    value={draft.camera.rtsp_url}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, rtsp_url: event.target.value },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Loại camera</span>
                  <select
                    value={draft.camera.camera_type}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, camera_type: event.target.value },
                      }))
                    }
                  >
                    <option value="roadside">Bên đường</option>
                    <option value="overhead">Trên cao</option>
                    <option value="intersection">Nút giao</option>
                  </select>
                </label>
                <label className="field">
                  <span>Hướng quan sát</span>
                  <input
                    value={draft.camera.view_direction}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, view_direction: event.target.value },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Frame width</span>
                  <input
                    type="number"
                    value={draft.camera.frame_width}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, frame_width: Number(event.target.value) || 1280 },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Frame height</span>
                  <input
                    type="number"
                    value={draft.camera.frame_height}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: { ...current.camera, frame_height: Number(event.target.value) || 720 },
                      }))
                    }
                  />
                </label>
              </div>
            </section>

            <section className="management-subcard">
              <div className="management-subcard-title">Vị trí camera</div>
              <div className="form-grid management-form-grid">
                <label className="field">
                  <span>Tuyến đường</span>
                  <input
                    value={draft.camera.location.road_name}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: {
                          ...current.camera,
                          location: { ...current.camera.location, road_name: event.target.value },
                        },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Ngã tư / nút giao</span>
                  <input
                    value={draft.camera.location.intersection_name}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: {
                          ...current.camera,
                          location: { ...current.camera.location, intersection_name: event.target.value },
                        },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>GPS lat</span>
                  <input
                    value={draft.camera.location.gps_lat}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: {
                          ...current.camera,
                          location: { ...current.camera.location, gps_lat: event.target.value },
                        },
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>GPS lng</span>
                  <input
                    value={draft.camera.location.gps_lng}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        camera: {
                          ...current.camera,
                          location: { ...current.camera.location, gps_lng: event.target.value },
                        },
                      }))
                    }
                  />
                </label>
              </div>
            </section>
          </div>

          {message ? <div className="message-bar">{message}</div> : null}
        </section>

        <section className="panel lane-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Trình chỉnh sửa làn</div>
              <h3>Cấu hình làn đường và các vùng phục vụ nhận diện hướng rẽ</h3>
            </div>
          </div>

          <div className="lane-editor-grid">
            <div className="lane-list-panel">
              <div className="lane-list">
                {draft.lane_config.lanes.map((lane) => (
                  <div
                    key={lane.lane_id}
                    className={lane.lane_id === selectedLaneId ? "lane-chip lane-chip-row active" : "lane-chip lane-chip-row"}
                  >
                    <button className="lane-chip-main" onClick={() => setSelectedLaneId(lane.lane_id)}>
                      Làn {lane.lane_id}
                    </button>
                    <button
                      className="lane-chip-delete"
                      onClick={(event) => {
                        event.stopPropagation();
                        removeLane(lane.lane_id);
                      }}
                      aria-label={`Xóa làn ${lane.lane_id}`}
                      title={`Xóa làn ${lane.lane_id}`}
                    >
                      <ActionIcon type="lane-delete" />
                    </button>
                  </div>
                ))}
              </div>
              <button className="button secondary lane-add-button" onClick={addLane}>
                <ActionIcon type="lane-add" />
                Thêm làn
              </button>
            </div>

            {selectedLane ? (
              <div className="lane-settings">
                <section className="management-subcard">
                  <div className="management-subcard-title">Quy tắc làn và đối tượng chỉnh sửa</div>
                  <div className="inline-fields lane-inline-fields">
                    <label className="field lane-field-changes">
                      <span>Các làn được phép chuyển</span>
                      <MultiSelectDropdown
                        summary={
                          draft.lane_config.lanes
                            .filter((laneOption) => (selectedLane.allowed_lane_changes || []).includes(laneOption.lane_id))
                            .map((laneOption) => `Làn ${laneOption.lane_id}`)
                            .join(", ")
                        }
                        placeholder="Chọn làn"
                        options={draft.lane_config.lanes}
                        getKey={(laneOption) => laneOption.lane_id}
                        getLabel={(laneOption) => `Làn ${laneOption.lane_id}`}
                        isChecked={(laneOption) => (selectedLane.allowed_lane_changes || []).includes(laneOption.lane_id)}
                        isDisabled={(laneOption) => laneOption.lane_id === selectedLane.lane_id}
                        getNote={(laneOption) => (laneOption.lane_id === selectedLane.lane_id ? "Hiện tại" : null)}
                        onToggle={(laneOption, checked) =>
                          updateLane(selectedLane.lane_id, (lane) => ({
                            ...lane,
                            allowed_lane_changes: checked
                              ? [...new Set([...(lane.allowed_lane_changes || []), laneOption.lane_id])]
                              : (lane.allowed_lane_changes || []).filter((value) => value !== laneOption.lane_id),
                          }))
                        }
                      />
                    </label>
                    <label className="field lane-field-vehicles">
                      <span>Loại phương tiện được phép</span>
                      <MultiSelectDropdown
                        summary={
                          VEHICLE_TYPES.filter((vehicleType) => (selectedLane.allowed_vehicle_types || []).includes(vehicleType))
                            .map((vehicleType) => getVehicleTypeLabel(vehicleType))
                            .join(", ")
                        }
                        placeholder="Chọn phương tiện"
                        options={VEHICLE_TYPES}
                        getKey={(vehicleType) => vehicleType}
                        getLabel={(vehicleType) => getVehicleTypeLabel(vehicleType)}
                        isChecked={(vehicleType) => (selectedLane.allowed_vehicle_types || []).includes(vehicleType)}
                        onToggle={(vehicleType, checked) =>
                          updateLane(selectedLane.lane_id, (lane) => ({
                            ...lane,
                            allowed_vehicle_types: checked
                              ? [...new Set([...(lane.allowed_vehicle_types || []), vehicleType])]
                              : (lane.allowed_vehicle_types || []).filter((value) => value !== vehicleType),
                          }))
                        }
                      />
                    </label>
                    <label className="field lane-field-target">
                      <span>Đối tượng chỉnh sửa</span>
                      <select value={editTarget} onChange={(event) => setEditTarget(event.target.value)}>
                        <optgroup label="Theo từng làn">
                          <option value="lane_polygon">Biên làn xe</option>
                          <option value="approach_zone">Vùng chuẩn bị rẽ</option>
                          <option value="commit_line">Vạch bắt đầu rẽ</option>
                        </optgroup>
                        <optgroup label="Theo hướng đang chọn">
                          <option value="movement_path">Đường đi (movement path)</option>
                          <option value="exit_line">Vạch xác nhận đầu ra</option>
                          <option value="exit_zone">Vùng xác nhận đầu ra</option>
                        </optgroup>
                      </select>
                    </label>
                  </div>
                </section>

                <section className="management-subcard">
                  <div className="management-subcard-title">Hành vi và chính sách cho phép</div>
                  <div className="inline-fields lane-maneuver-inline">
                    <label className="field">
                      <span>Hành vi đang cấu hình</span>
                      <select value={selectedManeuver} onChange={(event) => setSelectedManeuver(event.target.value)}>
                        {MANEUVERS.map((maneuver) => (
                          <option key={maneuver} value={maneuver}>
                            {getManeuverLabel(maneuver)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Độ rộng corridor</span>
                      <select
                        value={selectedManeuverConfig?.corridor_preset || (selectedManeuver === "u_turn" ? "wide" : "normal")}
                        onChange={(event) =>
                          updateSelectedManeuverConfig((cfg) => ({
                            ...cfg,
                            corridor_preset: event.target.value,
                          }))
                        }
                      >
                        {CORRIDOR_PRESET_OPTIONS.map((preset) => (
                          <option key={preset} value={preset}>
                            {CORRIDOR_PRESET_LABELS[preset]}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="checkbox-group">
                    <label className="checkbox-pill">
                      <input
                        type="checkbox"
                        checked={selectedManeuverEnabled}
                        onChange={(event) =>
                          updateSelectedManeuverConfig((cfg) => ({
                            ...cfg,
                            enabled: event.target.checked,
                            allowed: event.target.checked ? Boolean(cfg.allowed ?? false) : false,
                          }))
                        }
                      />
                      <span>Bật theo dõi {getManeuverLabel(selectedManeuver)}</span>
                    </label>
                    <label className={selectedManeuverEnabled ? "checkbox-pill" : "checkbox-pill disabled"}>
                      <input
                        type="checkbox"
                        checked={selectedManeuverAllowed}
                        disabled={!selectedManeuverEnabled}
                        onChange={(event) =>
                          updateSelectedManeuverConfig((cfg) => ({
                            ...cfg,
                            allowed: event.target.checked,
                          }))
                        }
                      />
                      <span>
                        {selectedManeuverEnabled
                          ? `${selectedManeuverAllowed ? "Cho phép" : "Cấm"} ${getManeuverLabel(selectedManeuver)} ở làn này`
                          : `${getManeuverLabel(selectedManeuver)} đang tắt theo dõi`}
                      </span>
                    </label>
                  </div>
                </section>

                <section className="management-subcard">
                  <div className="management-subcard-title">Công cụ chỉnh sửa</div>
                  <div className="editor-toolbar-row">
                    <div className="editor-action-toolbar compact-toolbar">
                      <div className="editor-action-toolbar-label">Ảnh nền camera</div>
                      <div className="editor-actions tight-actions">
                        <label className={`button secondary compact-button smaller-button${isNewCamera || backgroundBusy ? " disabled" : ""}`}>
                          <input
                            type="file"
                            accept=".jpg,.png,image/jpeg,image/png"
                            hidden
                            disabled={isNewCamera || backgroundBusy}
                            onChange={handleBackgroundUpload}
                          />
                          <ActionIcon type="image-upload" />
                          {backgroundBusy ? "Đang xử lý" : "Tải ảnh"}
                        </label>
                        <button
                          className="button ghost compact-button smaller-button"
                          onClick={handleBackgroundClear}
                          disabled={isNewCamera || backgroundBusy || !hasBackgroundImage}
                        >
                          <ActionIcon type="image-delete" />
                          Xóa ảnh
                        </button>
                        <div className={hasBackgroundImage ? "badge success toolbar-badge" : "badge subtle toolbar-badge"}>
                          {hasBackgroundImage ? "Có ảnh nền" : "Chưa có ảnh"}
                        </div>
                      </div>
                      {isNewCamera ? (
                        <div className="toolbar-note">Lưu camera trước để gắn ảnh nền theo `camera_id` cố định.</div>
                      ) : null}
                    </div>

                    <div className="editor-action-toolbar compact-toolbar">
                      <div className="editor-action-toolbar-label">Thao tác polygon</div>
                      <div className="editor-actions tight-actions">
                        <button
                          className={`${polygonLocked ? "button secondary" : "button ghost"} compact-button smaller-button`}
                          onClick={() => {
                            setPolygonLocked((value) => {
                              const nextValue = !value;
                              setMessage(nextValue ? "Đã khóa polygon để tránh chỉnh nhầm." : "Đã mở khóa polygon để tiếp tục chỉnh.");
                              return nextValue;
                            });
                          }}
                        >
                          <ActionIcon type={polygonLocked ? "unlock" : "lock"} />
                          {polygonLocked ? "Mở khóa" : "Khóa"}
                        </button>
                        <button className="button secondary compact-button smaller-button" onClick={undoPoint}>
                          <ActionIcon type="undo" />
                          Xóa điểm
                        </button>
                        <button className="button ghost compact-button smaller-button" onClick={deleteSelectedVertex} disabled={selectedVertexIndex == null}>
                          <ActionIcon type="vertex-delete" />
                          Xóa vertex
                        </button>
                        <button className="button ghost compact-button smaller-button" onClick={clearPolygon}>
                          <ActionIcon type="polygon-delete" />
                          Xóa đa giác
                        </button>
                      </div>
                    </div>
                  </div>
                </section>

                {polygonStatus.warnings.length > 0 ? (
                  <div className="message-bar warning">
                    {polygonStatus.warnings.map((warning) => (
                      <div key={warning}>{warning}</div>
                    ))}
                  </div>
                ) : null}

              </div>
            ) : (
              <div className="empty-state slim">Tạo làn đầu tiên để bắt đầu cấu hình.</div>
            )}
          </div>

          <div className="editor-canvas-wrap management-canvas-wrap">
            <CameraCanvas
              frameWidth={draft.camera.frame_width}
              frameHeight={draft.camera.frame_height}
              lanes={draft.lane_config.lanes}
              vehicles={[]}
              backgroundImageUrl={
                !isNewCamera && hasBackgroundImage && draft.camera.camera_id
                  ? getBackgroundImageUrl(draft.camera.camera_id, backgroundRevision)
                  : null
              }
              selectedLaneId={selectedLaneId}
              selectedVertexIndex={selectedVertexIndex}
              editable={!polygonLocked}
              onCanvasClick={handleCanvasPoint}
              onPolygonReplace={replaceTargetPolygon}
              onVertexSelect={setSelectedVertexIndex}
              editTarget={editTarget}
              selectedManeuver={selectedManeuver}
            />
          </div>

          <ValidationIssuesPanel issues={configValidation} lanes={draft.lane_config.lanes} />
        </section>
      </section>
    </div>
  );
}
