import React, { useEffect, useState } from "react";
import SimpleBarChart from "../components/SimpleBarChart";
import StatPill from "../components/StatPill";
import TimeSeriesChart from "../components/TimeSeriesChart";
import { fetchDashboard, fetchViolationHistory } from "../api";
import {
  formatTimestamp,
  getVehicleTypeLabel,
  getViolationLabel,
  nowLocalInput,
  startOfDayLocalInput,
  toIsoOrNull,
} from "../utils";

export default function AnalyticsView({ cameras, selectedCameraId, onSelectCamera }) {
  const [cameraFilter, setCameraFilter] = useState("");
  const [fromInput, setFromInput] = useState(startOfDayLocalInput());
  const [toInput, setToInput] = useState(nowLocalInput());
  const [dashboard, setDashboard] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (selectedCameraId && !cameraFilter) {
      setCameraFilter(selectedCameraId);
    }
  }, [selectedCameraId, cameraFilter]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      try {
        const [dashboardRes, historyRes] = await Promise.all([
          fetchDashboard({
            cameraId: cameraFilter || null,
            fromTs: toIsoOrNull(fromInput),
            toTs: toIsoOrNull(toInput),
          }),
          fetchViolationHistory({
            cameraId: cameraFilter || null,
            fromTs: toIsoOrNull(fromInput),
            toTs: toIsoOrNull(toInput),
            limit: 300,
          }),
        ]);
        if (!active) return;
        setDashboard(dashboardRes);
        setHistory(historyRes.rows || []);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => {
      active = false;
    };
  }, [cameraFilter, fromInput, toInput]);

  const overview = dashboard?.overview || {};
  const vehicleData = Object.entries(overview.vehicle_type_totals || {}).map(([label, value]) => ({
    label: getVehicleTypeLabel(label),
    value,
  }));
  const violationData = Object.entries(overview.violation_totals || {}).map(([label, value]) => ({
    label: getViolationLabel(label),
    value,
  }));
  const cameraData = (dashboard?.camera_summary || []).map((row) => ({
    label: row.camera_id,
    value: row.total_violations,
    subtitle: `${row.road_name}${row.intersection ? ` · ${row.intersection}` : ""}`,
  }));
  const roadData = (dashboard?.road_summary || []).map((row) => ({
    label: row.intersection ? `${row.road_name} · ${row.intersection}` : row.road_name,
    value: row.total_violations,
  }));

  return (
    <div className="analytics-layout">
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-kicker">Bảng điều khiển phân tích</div>
            <h2>Thống kê theo camera, địa điểm và toàn hệ thống</h2>
          </div>
          <div className="filter-row">
            <label className="field">
              <span>Camera</span>
              <select
                value={cameraFilter}
                onChange={(event) => {
                  setCameraFilter(event.target.value);
                  onSelectCamera(event.target.value || null);
                }}
              >
                <option value="">Tất cả camera</option>
                {cameras.map((camera) => (
                  <option key={camera.camera_id} value={camera.camera_id}>
                    {camera.camera_id} - {camera.location.road_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Từ thời điểm</span>
              <input type="datetime-local" value={fromInput} onChange={(event) => setFromInput(event.target.value)} />
            </label>
            <label className="field">
              <span>Đến thời điểm</span>
              <input type="datetime-local" value={toInput} onChange={(event) => setToInput(event.target.value)} />
            </label>
          </div>
        </div>

        <div className="summary-grid">
          <StatPill label="Tổng vi phạm" value={overview.total_violations ?? 0} />
          <StatPill label="Camera có vi phạm" value={overview.total_cameras ?? 0} />
          <StatPill label="Bản ghi lịch sử" value={history.length} />
          <StatPill label="Trạng thái" value={loading ? "Đang cập nhật" : "Ổn định"} />
        </div>

        <div className="chart-grid">
          <SimpleBarChart title="Vi phạm theo camera" data={cameraData} color="var(--accent)" />
          <SimpleBarChart title="Vi phạm theo loại xe" data={vehicleData} color="var(--warning)" />
          <SimpleBarChart title="Vi phạm theo loại hành vi" data={violationData} color="var(--danger)" />
          <SimpleBarChart title="Vi phạm theo khu vực" data={roadData} color="var(--accent-soft)" />
        </div>
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <div className="panel-kicker">Theo khung thời gian</div>
            <h3>Biểu đồ vi phạm theo giờ</h3>
          </div>
        </div>
        <TimeSeriesChart series={dashboard?.hourly_series || []} />
      </section>

      <section className="panel">
        <div className="panel-header compact">
          <div>
            <div className="panel-kicker">Lịch sử</div>
            <h3>Lịch sử xe vi phạm</h3>
          </div>
          <div className="badge">{history.length} dòng</div>
        </div>
        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>Thời gian</th>
                <th>Camera</th>
                <th>Địa điểm</th>
                <th>Xe</th>
                <th>ID xe</th>
                <th>Làn</th>
                <th>Vi phạm</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan="7" className="empty-cell">
                    Không có dữ liệu trong khoảng thời gian đã chọn.
                  </td>
                </tr>
              ) : null}
              {history.map((row) => (
                <tr key={row.id}>
                  <td>{formatTimestamp(row.timestamp)}</td>
                  <td>{row.camera_id}</td>
                  <td>
                    {row.location.road_name}
                    {row.location.intersection ? ` · ${row.location.intersection}` : ""}
                  </td>
                  <td>{getVehicleTypeLabel(row.vehicle_type)}</td>
                  <td>#{row.vehicle_id}</td>
                  <td>{row.lane_id}</td>
                  <td>{getViolationLabel(row.violation)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
