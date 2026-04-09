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
  MANEUVERS,
  VEHICLE_TYPES,
  buildPayload,
  createCameraDraft,
  createEmptyLane,
  getManeuverLabel,
  getVehicleTypeLabel,
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

export default function ManagementView({ cameras, selectedCameraId, onSelectCamera, onRefreshCameras }) {
  const [draft, setDraft] = useState(createCameraDraft());
  const [activeCameraId, setActiveCameraId] = useState(selectedCameraId || null);
  const [selectedLaneId, setSelectedLaneId] = useState(1);
  const [selectedVertexIndex, setSelectedVertexIndex] = useState(null);
  const [editTarget, setEditTarget] = useState("lane");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [isNewCamera, setIsNewCamera] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [polygonLocked, setPolygonLocked] = useState(false);
  const [hasBackgroundImage, setHasBackgroundImage] = useState(false);
  const [backgroundRevision, setBackgroundRevision] = useState("0");
  const [backgroundBusy, setBackgroundBusy] = useState(false);

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
        setSelectedVertexIndex(null);
        setIsDirty(false);
        setPolygonLocked(false);
        setHasBackgroundImage(Boolean(detail.has_background_image));
        setBackgroundRevision(`${Date.now()}`);
        setMessage(detail.runtime_applied ? "Cấu hình đang được backend áp dụng runtime." : "");
      })
      .catch(() => {
        setDraft(createCameraDraft());
        setSelectedVertexIndex(null);
        setIsDirty(false);
        setHasBackgroundImage(false);
      });
  }, [activeCameraId, selectedCameraId, isNewCamera]);

  const selectedLane = useMemo(
    () => draft.lane_config.lanes.find((lane) => lane.lane_id === selectedLaneId) || draft.lane_config.lanes[0] || null,
    [draft, selectedLaneId],
  );

  const selectedPoints = useMemo(() => {
    if (!selectedLane) return [];
    if (editTarget === "lane") return selectedLane.polygon || [];
    return selectedLane.turn_regions?.[editTarget] || [];
  }, [editTarget, selectedLane]);

  const polygonStatus = useMemo(() => {
    if (!selectedLane) return { warnings: [] };
    const targetLabel = editTarget === "lane" ? `Polygon làn ${selectedLane.lane_id}` : `${getManeuverLabel(editTarget)} của làn ${selectedLane.lane_id}`;
    const warnings = [];
    const minimumPoints = 3;
    if (selectedPoints.length > 0 && selectedPoints.length < minimumPoints) {
      warnings.push(`${targetLabel} hiện có dưới ${minimumPoints} điểm, chưa đủ để tạo vùng hợp lệ.`);
    }
    if (selectedPoints.length >= 4 && polygonSelfIntersects(selectedPoints)) {
      warnings.push(`${targetLabel} đang tự cắt nhau, nên chỉnh lại để tránh vùng hình học khó kiểm soát.`);
    }
    return { warnings };
  }, [editTarget, selectedLane, selectedPoints]);

  useEffect(() => {
    setSelectedVertexIndex(null);
  }, [editTarget, selectedLaneId]);

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
      setMessage('Có thay đổi chưa lưu. Nhấn "Lưu cấu hình làn đường" để backend áp dụng.');
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

  const updateTargetPolygon = (lane, updater) => {
    if (editTarget === "lane") {
      return {
        ...lane,
        polygon: updater(lane.polygon || []),
      };
    }
    return {
      ...lane,
      turn_regions: {
        ...(lane.turn_regions || {}),
        [editTarget]: updater(lane.turn_regions?.[editTarget] || []),
      },
    };
  };

  const handleCanvasPoint = (point) => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, (points) => [...points, point]));
    setSelectedVertexIndex(selectedPoints.length);
    setMessage("Đã thêm điểm mới vào polygon.");
  };

  const replaceTargetPolygon = (nextPoints) => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, () => nextPoints));
    setMessage("Đã cập nhật polygon trên canvas, chưa lưu xuống backend.");
  };

  const deleteSelectedVertex = () => {
    if (!selectedLane || selectedVertexIndex == null) return;
    updateLane(
      selectedLane.lane_id,
      (lane) => updateTargetPolygon(lane, (points) => points.filter((_, index) => index !== selectedVertexIndex)),
    );
    setSelectedVertexIndex(null);
    setMessage("Đã xóa điểm đang chọn.");
  };

  const undoPoint = () => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, (points) => points.slice(0, -1)));
    setSelectedVertexIndex(null);
    setMessage("Đã xóa điểm vừa vẽ.");
  };

  const clearPolygon = () => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, () => []));
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
    setSelectedVertexIndex(null);
    setEditTarget("lane");
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
      setSelectedVertexIndex(null);
      setIsDirty(false);
      setHasBackgroundImage(Boolean(freshDetail.has_background_image));
      setBackgroundRevision(`${Date.now()}`);
      setMessage(response.runtime_applied ? "Đã lưu và backend đã áp dụng ngay cấu hình lane mới." : "Đã lưu cấu hình camera.");
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
      setSelectedVertexIndex(null);
      setIsDirty(false);
      setHasBackgroundImage(false);
      setBackgroundRevision("0");
      setMessage(`Đã xóa ${activeCameraId}.`);
    } catch (error) {
      setMessage(error.message || "Không thể xóa camera.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="management-layout">
      <aside className="panel sidebar-panel">
        <div className="panel-header compact">
          <div>
            <div className="panel-kicker">Danh mục</div>
            <h3>Danh sách camera</h3>
          </div>
          <button className="button secondary" onClick={startCreateCamera}>
            Thêm camera
          </button>
        </div>
        <div className="entity-list">
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
        <section className="panel">
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

          <div className="status-strip">
            <div className={isDirty ? "badge warning" : "badge success"}>
              {isDirty ? "Chưa lưu" : "Đã đồng bộ backend"}
            </div>
            <div className="row-sub">
              {isDirty
                ? "Các thay đổi hiện chỉ nằm ở frontend state cho đến khi bạn nhấn lưu."
                : "Monitoring và logic backend đang dùng đúng cấu hình hiện tại."}
            </div>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>Camera ID</span>
              <input
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

          {message ? <div className="message-bar">{message}</div> : null}

        </section>

        <section className="panel lane-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Trình chỉnh sửa làn</div>
              <h3>Số lượng làn, chức năng làn và vùng đa giác rẽ</h3>
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
                      <option value="lane">Đa giác làn</option>
                      {MANEUVERS.map((maneuver) => (
                        <option key={maneuver} value={maneuver}>
                          Vùng rẽ: {getManeuverLabel(maneuver)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <div className="checkbox-group">
                  {MANEUVERS.map((maneuver) => {
                    const checked = (selectedLane.allowed_maneuvers || []).includes(maneuver);
                    return (
                      <label key={maneuver} className="checkbox-pill">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) =>
                            updateLane(selectedLane.lane_id, (lane) => ({
                              ...lane,
                              allowed_maneuvers: event.target.checked
                                ? [...new Set([...(lane.allowed_maneuvers || []), maneuver])]
                                : (lane.allowed_maneuvers || []).filter((value) => value !== maneuver),
                            }))
                          }
                        />
                        <span>{getManeuverLabel(maneuver)}</span>
                      </label>
                    );
                  })}
                </div>

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

          <div className="editor-canvas-wrap">
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
              showTurnRegions
              selectedLaneId={selectedLaneId}
              selectedVertexIndex={selectedVertexIndex}
              editable={!polygonLocked}
              onCanvasClick={handleCanvasPoint}
              onPolygonReplace={replaceTargetPolygon}
              onVertexSelect={setSelectedVertexIndex}
              editTarget={editTarget}
            />
          </div>
        </section>
      </section>
    </div>
  );
}
