import React, { useEffect, useMemo, useRef, useState } from "react";
import CameraCanvas from "../components/CameraCanvas";
import StatPill from "../components/StatPill";
import ViolationDetailModal from "../components/ViolationDetailModal";
import { connectTracks, connectViolations, fetchCameraDetail, getCameraPreviewUrl } from "../api";
import { formatTimestamp, getCameraTypeLabel, getVehicleTypeLabel, getViolationLabel } from "../utils";

const DEFAULT_MONITORING_UI_CONFIG = {
  trajectory: {
    default_limit: 30,
    min_limit: 10,
    max_limit: 80,
    max_points_per_vehicle: 48,
    stale_ms: 1500,
    min_point_distance_px: 1.5,
  },
  violation: {
    list_max_rows: 80,
    highlight_duration_ms: 15000,
  },
  processing_fps: {
    stale_after_ms: 1000,
    poll_interval_ms: 500,
  },
};

function toFiniteNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeMonitoringUiConfig(rawConfig) {
  const fallback = DEFAULT_MONITORING_UI_CONFIG;
  const rawTrajectory = rawConfig?.trajectory || {};
  const rawViolation = rawConfig?.violation || {};
  const rawProcessingFps = rawConfig?.processing_fps || {};

  const trajectoryMinLimit = Math.max(1, Math.round(toFiniteNumber(rawTrajectory.min_limit, fallback.trajectory.min_limit)));
  const trajectoryMaxLimit = Math.max(
    trajectoryMinLimit,
    Math.round(toFiniteNumber(rawTrajectory.max_limit, fallback.trajectory.max_limit)),
  );
  const trajectoryDefaultLimit = Math.min(
    Math.max(Math.round(toFiniteNumber(rawTrajectory.default_limit, fallback.trajectory.default_limit)), trajectoryMinLimit),
    trajectoryMaxLimit,
  );

  return {
    trajectory: {
      default_limit: trajectoryDefaultLimit,
      min_limit: trajectoryMinLimit,
      max_limit: trajectoryMaxLimit,
      max_points_per_vehicle: Math.max(
        2,
        Math.round(toFiniteNumber(rawTrajectory.max_points_per_vehicle, fallback.trajectory.max_points_per_vehicle)),
      ),
      stale_ms: Math.max(0, Math.round(toFiniteNumber(rawTrajectory.stale_ms, fallback.trajectory.stale_ms))),
      min_point_distance_px: Math.max(
        0,
        toFiniteNumber(rawTrajectory.min_point_distance_px, fallback.trajectory.min_point_distance_px),
      ),
    },
    violation: {
      list_max_rows: Math.max(1, Math.round(toFiniteNumber(rawViolation.list_max_rows, fallback.violation.list_max_rows))),
      highlight_duration_ms: Math.max(
        0,
        Math.round(toFiniteNumber(rawViolation.highlight_duration_ms, fallback.violation.highlight_duration_ms)),
      ),
    },
    processing_fps: {
      stale_after_ms: Math.max(
        0,
        Math.round(toFiniteNumber(rawProcessingFps.stale_after_ms, fallback.processing_fps.stale_after_ms)),
      ),
      poll_interval_ms: Math.max(
        100,
        Math.round(toFiniteNumber(rawProcessingFps.poll_interval_ms, fallback.processing_fps.poll_interval_ms)),
      ),
    },
  };
}

function clampTrajectoryLimit(value, trajectoryConfig) {
  const next = Math.round(toFiniteNumber(value, trajectoryConfig.default_limit));
  return Math.min(Math.max(next, trajectoryConfig.min_limit), trajectoryConfig.max_limit);
}

function getVehicleTrajectoryPoint(vehicle) {
  const bbox = vehicle?.bbox;
  if (!bbox) return null;

  const x1 = Number(bbox.x1);
  const y2 = Number(bbox.y2);
  const x2 = Number(bbox.x2);
  if (!Number.isFinite(x1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
    return null;
  }
  return [(x1 + x2) / 2, y2];
}

function pointDistance(left, right) {
  return Math.hypot(left[0] - right[0], left[1] - right[1]);
}

export default function MonitoringView({ cameras, selectedCameraId, onSelectCamera, loading, configRevision }) {
  const [detail, setDetail] = useState(null);
  const [vehicles, setVehicles] = useState([]);
  const [violations, setViolations] = useState([]);
  const [processingFps, setProcessingFps] = useState(null);
  const [selectedViolation, setSelectedViolation] = useState(null);
  const [trajectoryLimit, setTrajectoryLimit] = useState(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
  const [trajectoryRows, setTrajectoryRows] = useState([]);
  const vehicleSeenOrderRef = useRef(new Map());
  const nextVehicleOrderRef = useRef(0);
  const violatingVehicleIdsRef = useRef(new Map());
  const lastTrackUpdateRef = useRef(0);
  const liveTrajectoriesRef = useRef(new Map());
  const trajectoryLimitRef = useRef(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
  const monitoringUiConfig = useMemo(() => normalizeMonitoringUiConfig(detail?.ui?.monitoring), [detail]);

  useEffect(() => {
    const normalizedLimit = clampTrajectoryLimit(trajectoryLimit, monitoringUiConfig.trajectory);
    if (normalizedLimit !== trajectoryLimit) {
      setTrajectoryLimit(normalizedLimit);
      return;
    }
    trajectoryLimitRef.current = normalizedLimit;
    setTrajectoryRows((rows) => rows.slice(0, normalizedLimit));
  }, [monitoringUiConfig.trajectory, trajectoryLimit]);

  useEffect(() => {
    if (!selectedCameraId) {
      setDetail(null);
      setSelectedViolation(null);
      setTrajectoryRows([]);
      setTrajectoryLimit(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
      liveTrajectoriesRef.current = new Map();
      return;
    }
    fetchCameraDetail(selectedCameraId)
      .then((nextDetail) => {
        setDetail(nextDetail);
        const uiConfig = normalizeMonitoringUiConfig(nextDetail?.ui?.monitoring);
        setTrajectoryLimit(uiConfig.trajectory.default_limit);
      })
      .catch(() => {
        setDetail(null);
        setTrajectoryLimit(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
      });
    setVehicles([]);
    setViolations([]);
    setProcessingFps(null);
    setTrajectoryRows([]);
    setSelectedViolation(null);
    vehicleSeenOrderRef.current = new Map();
    nextVehicleOrderRef.current = 0;
    violatingVehicleIdsRef.current = new Map();
    lastTrackUpdateRef.current = 0;
    liveTrajectoriesRef.current = new Map();
  }, [configRevision, selectedCameraId]);

  const updateLiveTrajectories = (nextVehicles) => {
    const now = Date.now();
    const nextMap = new Map(liveTrajectoriesRef.current);
    const trajectoryUi = monitoringUiConfig.trajectory;

    nextVehicles.forEach((vehicle) => {
      const vehicleId = vehicle.vehicle_id;
      if (vehicleId == null) return;

      const point = getVehicleTrajectoryPoint(vehicle);
      if (!point) return;

      const current = nextMap.get(vehicleId) || {
        vehicle_id: vehicleId,
        vehicle_type: vehicle.vehicle_type,
        lane_id: vehicle.lane_id,
        points: [],
        lastSeenMs: now,
      };
      const points = [...current.points];
      const lastPoint = points[points.length - 1];
      if (!lastPoint) {
        points.push(point);
      } else if (pointDistance(lastPoint, point) >= trajectoryUi.min_point_distance_px) {
        points.push(point);
      } else {
        points[points.length - 1] = point;
      }

      nextMap.set(vehicleId, {
        ...current,
        vehicle_type: vehicle.vehicle_type,
        lane_id: vehicle.lane_id,
        points: points.slice(-trajectoryUi.max_points_per_vehicle),
        lastSeenMs: now,
      });
    });

    nextMap.forEach((row, vehicleId) => {
      if (now - row.lastSeenMs > trajectoryUi.stale_ms) {
        nextMap.delete(vehicleId);
      }
    });

    liveTrajectoriesRef.current = nextMap;
    setTrajectoryRows(
      [...nextMap.values()]
        .filter((row) => row.points.length >= 2)
        .sort((left, right) => right.lastSeenMs - left.lastSeenMs)
        .slice(0, trajectoryLimitRef.current)
        .map(({ lastSeenMs, ...row }) => row),
    );
  };

  useEffect(() => {
    if (!selectedCameraId) return undefined;
    const violationUi = monitoringUiConfig.violation;
    const trackWs = connectTracks(selectedCameraId, (message) => {
      lastTrackUpdateRef.current = Date.now();
      const now = Date.now();
      violatingVehicleIdsRef.current.forEach((expiresAt, vehicleId) => {
        if (expiresAt <= now) {
          violatingVehicleIdsRef.current.delete(vehicleId);
        }
      });
      setVehicles(
        (message.vehicles || []).map((vehicle) => ({
          ...vehicle,
          isViolating: (violatingVehicleIdsRef.current.get(vehicle.vehicle_id) || 0) > now,
        })),
      );
      updateLiveTrajectories(message.vehicles || []);
      setProcessingFps(Number.isFinite(message.processing_fps) ? message.processing_fps : null);
    });
    const violationWs = connectViolations(selectedCameraId, (event) => {
      violatingVehicleIdsRef.current.set(event.vehicle_id, Date.now() + violationUi.highlight_duration_ms);
      setViolations((prev) => [event, ...prev].slice(0, violationUi.list_max_rows));
    });
    return () => {
      trackWs.close();
      violationWs.close();
    };
  }, [monitoringUiConfig.violation, selectedCameraId]);

  useEffect(() => {
    if (!selectedCameraId) return undefined;
    const processingFpsUi = monitoringUiConfig.processing_fps;
    const timer = window.setInterval(() => {
      const lastUpdate = lastTrackUpdateRef.current;
      if (lastUpdate && Date.now() - lastUpdate > processingFpsUi.stale_after_ms) {
        setProcessingFps(null);
      }
    }, processingFpsUi.poll_interval_ms);
    return () => window.clearInterval(timer);
  }, [monitoringUiConfig.processing_fps, selectedCameraId]);

  const camera = detail?.camera || null;
  const laneConfig = detail?.lane_config || null;
  const orderedVehicles = [...vehicles]
    .map((vehicle) => {
      if (!vehicleSeenOrderRef.current.has(vehicle.vehicle_id)) {
        vehicleSeenOrderRef.current.set(vehicle.vehicle_id, nextVehicleOrderRef.current);
        nextVehicleOrderRef.current += 1;
      }
      return {
        ...vehicle,
        seenOrder: vehicleSeenOrderRef.current.get(vehicle.vehicle_id) ?? 0,
      };
    })
    .sort((left, right) => right.seenOrder - left.seenOrder);

  const activeVehicleIds = new Set(vehicles.map((vehicle) => vehicle.vehicle_id));
  vehicleSeenOrderRef.current.forEach((_, vehicleId) => {
    if (!activeVehicleIds.has(vehicleId)) {
      vehicleSeenOrderRef.current.delete(vehicleId);
    }
  });

  const handleViolationKeyDown = (event, violation) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedViolation(violation);
    }
  };

  return (
    <>
      <div className="monitor-layout">
        <section className="panel hero-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">Luồng hình và vi phạm thời gian thực</div>
              <h2>Màn hình giám sát camera</h2>
            </div>
            <label className="field field-inline monitor-camera-picker">
              <span>Camera</span>
              <select
                className="monitor-camera-select"
                value={selectedCameraId || ""}
                onChange={(event) => onSelectCamera(event.target.value || null)}
              >
                {cameras.map((cameraRow) => (
                  <option key={cameraRow.camera_id} value={cameraRow.camera_id}>
                    {cameraRow.camera_id} - {cameraRow.location.road_name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {loading && cameras.length === 0 ? <div className="empty-state">Đang tải danh sách camera...</div> : null}
          {!selectedCameraId ? <div className="empty-state">Chưa có camera được cấu hình.</div> : null}

          {selectedCameraId && laneConfig ? (
            <>
              <div className="camera-meta-grid">
                <StatPill label="Camera ID" value={camera.camera_id} />
                <StatPill label="Loại camera" value={getCameraTypeLabel(camera.camera_type)} />
                <StatPill label="Hướng quan sát" value={camera.view_direction || "-"} />
                <StatPill
                  label="Vị trí"
                  value={`${camera.location.road_name}${camera.location.intersection_name ? ` · ${camera.location.intersection_name}` : ""}`}
                />
              </div>
              <div className="monitor-overlay-toolbar">
                <label className="field monitor-trajectory-limit">
                  <span>Số quỹ đạo hiển thị</span>
                  <input
                    type="number"
                    min={monitoringUiConfig.trajectory.min_limit}
                    max={monitoringUiConfig.trajectory.max_limit}
                    value={trajectoryLimit}
                    onChange={(event) => setTrajectoryLimit(clampTrajectoryLimit(event.target.value, monitoringUiConfig.trajectory))}
                  />
                </label>
                <div className="badge success">Quỹ đạo xanh lá · {trajectoryRows.length}</div>
              </div>
              <div className="video-stage">
                <img className="video-preview" alt="Xem trước camera" src={getCameraPreviewUrl(selectedCameraId)} />
                <CameraCanvas
                  overlay
                  frameWidth={laneConfig.frame_width}
                  frameHeight={laneConfig.frame_height}
                  lanes={laneConfig.lanes}
                  vehicles={vehicles}
                  trajectoryOverlays={trajectoryRows}
                  processingFps={processingFps}
                />
              </div>
            </>
          ) : null}
        </section>

        <aside className="stack-column monitor-sidebar">
          <section className="panel monitor-realtime-panel">
            <div className="panel-header compact">
              <div>
                <div className="panel-kicker">Thời gian thực</div>
                <h3>Xe đang được theo dõi</h3>
              </div>
              <div className="badge">{vehicles.length} xe</div>
            </div>
            <div className="entity-list tracked-vehicle-list">
              {orderedVehicles.length === 0 ? <div className="empty-state slim">Chưa có phương tiện đang hoạt động.</div> : null}
              {orderedVehicles.map((vehicle) => (
                <article className="list-row" key={`${vehicle.vehicle_id}-${vehicle.lane_id ?? "na"}`}>
                  <div>
                    <div className="row-title">
                      #{vehicle.vehicle_id} · {getVehicleTypeLabel(vehicle.vehicle_type)}
                    </div>
                    <div className="row-sub">
                      Làn ổn định: {vehicle.lane_id ?? "đang ổn định"}
                      {vehicle.raw_lane_id != null ? ` · hit hiện tại: ${vehicle.raw_lane_id}` : ""}
                    </div>
                  </div>
                  <div className={vehicle.bbox ? "badge success" : "badge subtle"}>{vehicle.bbox ? "Đang theo dõi" : "Chờ xử lý"}</div>
                </article>
              ))}
            </div>
          </section>
        </aside>

        <section className="panel monitor-full-width">
          <div className="panel-header compact">
            <div>
              <div className="panel-kicker">Luồng vi phạm</div>
              <h3>Danh sách vi phạm của camera đang xem</h3>
            </div>
            <div className="badge danger">{violations.length}</div>
          </div>
          <div className="entity-list violation-list">
            {violations.length === 0 ? <div className="empty-state slim">Chưa có vi phạm thời gian thực.</div> : null}
            {violations.map((event) => (
              <article
                className="list-row violation-row violation-trigger"
                key={`${event.camera_id}-${event.vehicle_id}-${event.violation}-${event.timestamp}`}
                onClick={() => setSelectedViolation(event)}
                onKeyDown={(keyEvent) => handleViolationKeyDown(keyEvent, event)}
                role="button"
                tabIndex={0}
              >
                <div>
                  <div className="row-title">
                    {getViolationLabel(event.violation)} · làn {event.lane_id}
                  </div>
                  <div className="row-sub">
                    {getVehicleTypeLabel(event.vehicle_type)} · xe #{event.vehicle_id}
                  </div>
                </div>
                <div className="row-meta">{formatTimestamp(event.timestamp)}</div>
              </article>
            ))}
          </div>
        </section>
      </div>

      <ViolationDetailModal open={Boolean(selectedViolation)} violation={selectedViolation} onClose={() => setSelectedViolation(null)} />
    </>
  );
}
