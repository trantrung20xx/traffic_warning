import React, { useEffect, useMemo, useState } from "react";
import CameraCanvas from "./components/CameraCanvas";
import ViolationList from "./components/ViolationList";
import StatsTable from "./components/StatsTable";
import {
  connectTracks,
  connectViolations,
  fetchCameras,
  fetchLanes,
  fetchStats,
  getCameraPreviewUrl,
} from "./api";

export default function App() {
  const [cameras, setCameras] = useState([]);
  const [selectedCameraId, setSelectedCameraId] = useState(null);

  const [lanes, setLanes] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [violations, setViolations] = useState([]);
  const [statsRows, setStatsRows] = useState([]);

  const [frameWidth, setFrameWidth] = useState(1280);
  const [frameHeight, setFrameHeight] = useState(720);

  useEffect(() => {
    fetchCameras()
      .then((cs) => {
        setCameras(cs);
        if (cs.length > 0) setSelectedCameraId(cs[0].camera_id);
      })
      .catch((err) => {
        console.error(err);
      });
  }, []);

  useEffect(() => {
    if (!selectedCameraId) return;
    fetchLanes(selectedCameraId)
      .then((res) => {
        setLanes(res.lanes);
        if (res.frame_width) setFrameWidth(res.frame_width);
        if (res.frame_height) setFrameHeight(res.frame_height);
      })
      .catch((err) => console.error(err));
    setVehicles([]);
    setViolations([]);
  }, [selectedCameraId]);

  useEffect(() => {
    if (!selectedCameraId) return;

    const trackWs = connectTracks(selectedCameraId, (msg) => {
      setVehicles(msg.vehicles || []);
    });
    const violationWs = connectViolations(selectedCameraId, (ev) => {
      setViolations((prev) => [ev, ...prev].slice(0, 200));
    });

    return () => {
      trackWs.close();
      violationWs.close();
    };
  }, [selectedCameraId]);

  useEffect(() => {
    const tick = async () => {
      try {
        const rows = await fetchStats();
        setStatsRows(rows || []);
      } catch (e) {
        // ignore
      }
    };
    tick();
    const t = window.setInterval(tick, 5000);
    return () => window.clearInterval(t);
  }, []);

  const selectedCamera = useMemo(
    () => cameras.find((c) => c.camera_id === selectedCameraId) || null,
    [cameras, selectedCameraId],
  );

  const previewUrl = selectedCameraId ? getCameraPreviewUrl(selectedCameraId) : "";

  return (
    <div className="app">
      <header className="header">
        <div className="brand">Traffic Warning</div>
        <div className="controls">
          <label className="label">
            Camera:&nbsp;
            <select
              className="select"
              value={selectedCameraId ?? ""}
              onChange={(e) => setSelectedCameraId(e.target.value || null)}
            >
              {cameras.map((c) => (
                <option key={c.camera_id} value={c.camera_id}>
                  {c.camera_id} · {c.location.road_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <main className="grid">
        <section className="left">
          <div className="panel panel-hero">
            <div className="panel-title">
              Live View: {selectedCameraId ?? "N/A"}
              {selectedCamera?.location?.intersection ? ` · ${selectedCamera.location.intersection}` : ""}
            </div>
            <div className="video-stack">
              {selectedCameraId ? (
                <img
                  className="video-preview"
                  alt="Live preview (MJPEG from backend)"
                  src={previewUrl}
                />
              ) : null}
              <CameraCanvas
                overlay
                frameWidth={frameWidth}
                frameHeight={frameHeight}
                lanes={lanes}
                vehicles={vehicles}
              />
            </div>
          </div>
        </section>

        <section className="right">
          <ViolationList events={violations} />
          <StatsTable rows={statsRows} />
        </section>
      </main>
    </div>
  );
}

