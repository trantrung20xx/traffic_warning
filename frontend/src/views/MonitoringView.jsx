import React, { useEffect, useRef, useState } from "react";
import CameraCanvas from "../components/CameraCanvas";
import StatPill from "../components/StatPill";
import ViolationDetailModal from "../components/ViolationDetailModal";
import { connectTracks, connectViolations, fetchCameraDetail, getCameraPreviewUrl } from "../api";
import { formatTimestamp, getCameraTypeLabel, getVehicleTypeLabel, getViolationLabel } from "../utils";

export default function MonitoringView({ cameras, selectedCameraId, onSelectCamera, loading, configRevision }) {
  const [detail, setDetail] = useState(null);
  const [vehicles, setVehicles] = useState([]);
  const [violations, setViolations] = useState([]);
  const [processingFps, setProcessingFps] = useState(null);
  const [selectedViolation, setSelectedViolation] = useState(null);
  const vehicleSeenOrderRef = useRef(new Map());
  const nextVehicleOrderRef = useRef(0);
  const violatingVehicleIdsRef = useRef(new Map());
  const lastTrackUpdateRef = useRef(0);

  useEffect(() => {
    if (!selectedCameraId) {
      setDetail(null);
      setSelectedViolation(null);
      return;
    }
    fetchCameraDetail(selectedCameraId).then(setDetail).catch(() => setDetail(null));
    setVehicles([]);
    setViolations([]);
    setProcessingFps(null);
    setSelectedViolation(null);
    vehicleSeenOrderRef.current = new Map();
    nextVehicleOrderRef.current = 0;
    violatingVehicleIdsRef.current = new Map();
    lastTrackUpdateRef.current = 0;
  }, [configRevision, selectedCameraId]);

  useEffect(() => {
    if (!selectedCameraId) return undefined;
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
      setProcessingFps(Number.isFinite(message.processing_fps) ? message.processing_fps : null);
    });
    const violationWs = connectViolations(selectedCameraId, (event) => {
      violatingVehicleIdsRef.current.set(event.vehicle_id, Date.now() + 15000);
      setViolations((prev) => [event, ...prev].slice(0, 80));
    });
    return () => {
      trackWs.close();
      violationWs.close();
    };
  }, [selectedCameraId]);

  useEffect(() => {
    if (!selectedCameraId) return undefined;
    const timer = window.setInterval(() => {
      const lastUpdate = lastTrackUpdateRef.current;
      if (lastUpdate && Date.now() - lastUpdate > 1000) {
        setProcessingFps(null);
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [selectedCameraId]);

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
            <label className="field field-inline">
              <span>Camera</span>
              <select value={selectedCameraId || ""} onChange={(event) => onSelectCamera(event.target.value || null)}>
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
              <div className="video-stage">
                <img className="video-preview" alt="Xem trước camera" src={getCameraPreviewUrl(selectedCameraId)} />
                <CameraCanvas
                  overlay
                  frameWidth={laneConfig.frame_width}
                  frameHeight={laneConfig.frame_height}
                  lanes={laneConfig.lanes}
                  vehicles={vehicles}
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
