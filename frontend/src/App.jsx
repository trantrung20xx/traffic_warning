import React, { startTransition, useEffect, useState } from "react";
import MonitoringView from "./views/MonitoringView";
import AnalyticsView from "./views/AnalyticsView";
import ManagementView from "./views/ManagementView";
import { fetchCameras } from "./api";

export default function App() {
  const [view, setView] = useState("monitor");
  const [cameras, setCameras] = useState([]);
  const [selectedCameraId, setSelectedCameraId] = useState(null);
  const [loadingCameras, setLoadingCameras] = useState(true);
  const [configRevision, setConfigRevision] = useState(0);

  const refreshCameras = async (preferredCameraId = null) => {
    setLoadingCameras(true);
    try {
      const rows = await fetchCameras();
      startTransition(() => {
        setCameras(rows);
        setConfigRevision((value) => value + 1);
        setSelectedCameraId((current) => {
          if (preferredCameraId && rows.some((camera) => camera.camera_id === preferredCameraId)) {
            return preferredCameraId;
          }
          if (current && rows.some((camera) => camera.camera_id === current)) {
            return current;
          }
          return rows[0]?.camera_id || null;
        });
      });
    } finally {
      setLoadingCameras(false);
    }
  };

  useEffect(() => {
    refreshCameras();
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">Hệ thống cảnh báo vi phạm giao thông</div>
          <h1>Trung tâm điều hành cảnh báo giao thông</h1>
        </div>
        <div className="nav-tabs">
          <button className={view === "monitor" ? "nav-tab active" : "nav-tab"} onClick={() => setView("monitor")}>
            Giám sát
          </button>
          <button className={view === "analytics" ? "nav-tab active" : "nav-tab"} onClick={() => setView("analytics")}>
            Thống kê
          </button>
          <button className={view === "management" ? "nav-tab active" : "nav-tab"} onClick={() => setView("management")}>
            Quản lý camera
          </button>
        </div>
      </header>

      <main className="workspace">
        {view === "monitor" ? (
          <MonitoringView
            cameras={cameras}
            selectedCameraId={selectedCameraId}
            onSelectCamera={setSelectedCameraId}
            loading={loadingCameras}
            configRevision={configRevision}
          />
        ) : null}
        {view === "analytics" ? (
          <AnalyticsView cameras={cameras} selectedCameraId={selectedCameraId} onSelectCamera={setSelectedCameraId} />
        ) : null}
        {view === "management" ? (
          <ManagementView
            cameras={cameras}
            selectedCameraId={selectedCameraId}
            onSelectCamera={setSelectedCameraId}
            onRefreshCameras={refreshCameras}
          />
        ) : null}
      </main>
    </div>
  );
}

