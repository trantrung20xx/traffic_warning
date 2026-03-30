import React, { useEffect, useMemo, useState } from "react";
import CameraCanvas from "../components/CameraCanvas";
import {
  createCamera,
  deleteCamera,
  fetchCameraDetail,
  updateCamera,
} from "../api";
import {
  MANEUVERS,
  buildPayload,
  createCameraDraft,
  createEmptyLane,
  getManeuverLabel,
  normalizeCameraDetail,
} from "../utils";

export default function ManagementView({ cameras, selectedCameraId, onSelectCamera, onRefreshCameras }) {
  const [draft, setDraft] = useState(createCameraDraft());
  const [activeCameraId, setActiveCameraId] = useState(selectedCameraId || null);
  const [selectedLaneId, setSelectedLaneId] = useState(1);
  const [editTarget, setEditTarget] = useState("lane");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [isNewCamera, setIsNewCamera] = useState(false);

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
      })
      .catch(() => {
        setDraft(createCameraDraft());
      });
  }, [activeCameraId, selectedCameraId, isNewCamera]);

  const selectedLane = useMemo(
    () => draft.lane_config.lanes.find((lane) => lane.lane_id === selectedLaneId) || draft.lane_config.lanes[0] || null,
    [draft, selectedLaneId],
  );

  const updateDraft = (updater) => {
    setDraft((current) => {
      const next = updater(current);
      return {
        ...next,
        camera: {
          ...next.camera,
          monitored_lanes: next.lane_config.lanes.map((lane) => Number(lane.lane_id)),
        },
        lane_config: {
          ...next.lane_config,
          camera_id: next.camera.camera_id,
          frame_width: Number(next.camera.frame_width) || next.lane_config.frame_width,
          frame_height: Number(next.camera.frame_height) || next.lane_config.frame_height,
        },
      };
    });
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
  };

  const undoPoint = () => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, (points) => points.slice(0, -1)));
  };

  const clearPolygon = () => {
    if (!selectedLane) return;
    updateLane(selectedLane.lane_id, (lane) => updateTargetPolygon(lane, () => []));
  };

  const startCreateCamera = () => {
    setIsNewCamera(true);
    setActiveCameraId(null);
    const draftState = createCameraDraft(`cam_${String(cameras.length + 1).padStart(2, "0")}`);
    setDraft(draftState);
    setSelectedLaneId(1);
    setEditTarget("lane");
    setMessage("");
  };

  const saveCurrentCamera = async () => {
    setSaving(true);
    setMessage("");
    try {
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
      setMessage("Đã lưu cấu hình camera.");
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
                setIsNewCamera(false);
                setActiveCameraId(camera.camera_id);
                onSelectCamera(camera.camera_id);
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
            <div className="action-row">
              {!isNewCamera && activeCameraId ? (
                <button className="button danger" onClick={handleDelete} disabled={saving}>
                  Xóa camera
                </button>
              ) : null}
              <button className="button primary" onClick={saveCurrentCamera} disabled={saving}>
                {saving ? "Đang lưu..." : "Lưu cấu hình"}
              </button>
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
            <div className="action-row">
              <button className="button secondary" onClick={addLane}>
                Thêm làn
              </button>
              {selectedLane ? (
                <button className="button ghost" onClick={() => removeLane(selectedLane.lane_id)}>
                  Xóa làn
                </button>
              ) : null}
            </div>
          </div>

          <div className="lane-editor-grid">
            <div className="lane-list">
              {draft.lane_config.lanes.map((lane) => (
                <button
                  key={lane.lane_id}
                  className={lane.lane_id === selectedLaneId ? "lane-chip active" : "lane-chip"}
                  onClick={() => setSelectedLaneId(lane.lane_id)}
                >
                  Làn {lane.lane_id}
                </button>
              ))}
            </div>

            {selectedLane ? (
              <div className="lane-settings">
                <div className="inline-fields">
                  <label className="field">
                    <span>ID làn</span>
                    <input
                      type="number"
                      value={selectedLane.lane_id}
                      onChange={(event) => {
                        const nextId = Number(event.target.value) || selectedLane.lane_id;
                        updateLane(selectedLane.lane_id, (lane) => ({ ...lane, lane_id: nextId }));
                        setSelectedLaneId(nextId);
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>Các làn được phép chuyển</span>
                    <input
                      value={(selectedLane.allowed_lane_changes || []).join(",")}
                      onChange={(event) =>
                        updateLane(selectedLane.lane_id, (lane) => ({
                          ...lane,
                          allowed_lane_changes: event.target.value
                            .split(",")
                            .map((value) => Number(value.trim()))
                            .filter((value) => Number.isFinite(value)),
                        }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Đối tượng đa giác</span>
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

                <div className="editor-actions">
                  <button className="button secondary" onClick={undoPoint}>
                    Xóa điểm vừa vẽ
                  </button>
                  <button className="button ghost" onClick={clearPolygon}>
                    Xóa đa giác hiện tại
                  </button>
                </div>
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
              showTurnRegions
              selectedLaneId={selectedLaneId}
              editable
              onCanvasClick={handleCanvasPoint}
              editTarget={editTarget}
            />
          </div>
        </section>
      </section>
    </div>
  );
}
