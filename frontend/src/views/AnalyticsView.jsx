import React, { useEffect, useRef, useState } from "react";
import SimpleBarChart from "../components/SimpleBarChart";
import StatPill from "../components/StatPill";
import TimeSeriesChart from "../components/TimeSeriesChart";
import ViolationDetailModal from "../components/ViolationDetailModal";
import { connectViolations, exportViolationHistory, fetchDashboard, fetchViolationHistory } from "../api";
import {
  determineTimeSeriesGranularity,
  formatTimestamp,
  getTimeSeriesGranularityLabel,
  getVehicleTypeLabel,
  getViolationLabel,
  normalizeAnalyticsChartConfig,
  nowLocalInput,
  startOfDayLocalInput,
  toIsoOrNull,
} from "../utils";

export default function AnalyticsView({ cameras, selectedCameraId, onSelectCamera }) {
  const [cameraFilter, setCameraFilter] = useState("");
  const [fromInput, setFromInput] = useState(startOfDayLocalInput());
  const [toInput, setToInput] = useState(nowLocalInput());
  const [autoFollowNow, setAutoFollowNow] = useState(true);
  const [dashboard, setDashboard] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [exportingFormat, setExportingFormat] = useState("");
  const [exportMessage, setExportMessage] = useState("");
  const [selectedViolation, setSelectedViolation] = useState(null);
  const refreshTimerRef = useRef(null);

  const fromTs = toIsoOrNull(fromInput);
  const toTs = toIsoOrNull(toInput);

  useEffect(() => {
    if (selectedCameraId && !cameraFilter) {
      setCameraFilter(selectedCameraId);
    }
  }, [selectedCameraId, cameraFilter]);

  const fetchAnalyticsData = async () => {
    const currentToTs = autoFollowNow ? new Date().toISOString() : toTs;
    return await Promise.all([
      fetchDashboard({
        cameraId: cameraFilter || null,
        fromTs,
        toTs: currentToTs,
      }),
      fetchViolationHistory({
        cameraId: cameraFilter || null,
        fromTs,
        toTs: currentToTs,
      }),
    ]);
  };

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      try {
        const [dashboardRes, historyRes] = await fetchAnalyticsData();
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
  }, [cameraFilter, fromTs, toTs, autoFollowNow]);

  useEffect(() => {
    setExportMessage("");
  }, [cameraFilter, fromTs, toTs, autoFollowNow]);

  useEffect(() => {
    const eventMatchesCurrentFilter = (event) => {
      if (cameraFilter && event.camera_id !== cameraFilter) {
        return false;
      }
      const eventTime = new Date(event.timestamp).getTime();
      if (Number.isNaN(eventTime)) {
        return false;
      }
      if (fromTs) {
        const fromTime = new Date(fromTs).getTime();
        if (!Number.isNaN(fromTime) && eventTime < fromTime) {
          return false;
        }
      }
      if (!autoFollowNow && toTs) {
        const toTime = new Date(toTs).getTime();
        if (!Number.isNaN(toTime) && eventTime > toTime) {
          return false;
        }
      }
      return true;
    };

    const scheduleRefresh = () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
      }
      refreshTimerRef.current = setTimeout(async () => {
        setLoading(true);
        try {
          const [dashboardRes, historyRes] = await fetchAnalyticsData();
          setDashboard(dashboardRes);
          setHistory(historyRes.rows || []);
        } finally {
          setLoading(false);
        }
      }, 250);
    };

    const socket = connectViolations(cameraFilter || null, (event) => {
      if (eventMatchesCurrentFilter(event)) {
        scheduleRefresh();
      }
    });

    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      socket.close();
    };
  }, [cameraFilter, fromTs, toTs, autoFollowNow]);

  const overview = dashboard?.overview || {};
  const chartSeries = dashboard?.time_series || dashboard?.hourly_series || [];
  const chartConfig = normalizeAnalyticsChartConfig(dashboard?.chart_config);
  const timeSeriesGranularity =
    dashboard?.time_series_granularity ||
    determineTimeSeriesGranularity({
      fromTs: dashboard?.from_timestamp || fromTs,
      toTs: dashboard?.to_timestamp || (autoFollowNow ? new Date().toISOString() : toTs),
      pointCount: chartSeries.length,
      chartConfig,
    });
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
  const openViolationDetail = (violation) => setSelectedViolation(violation);

  const handleHistoryRowKeyDown = (event, violation) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openViolationDetail(violation);
    }
  };

  const handleExport = async (format) => {
    setExportMessage("");
    if (history.length === 0) {
      setExportMessage("Không có dữ liệu vi phạm trong khoảng thời gian đã chọn để xuất file.");
      return;
    }

    const currentToTs = autoFollowNow ? new Date().toISOString() : toTs;
    setExportingFormat(format);
    try {
      await exportViolationHistory({
        format,
        cameraId: cameraFilter || null,
        fromTs,
        toTs: currentToTs,
      });
    } catch (error) {
      setExportMessage(error?.message || "Không thể xuất lịch sử vi phạm.");
    } finally {
      setExportingFormat("");
    }
  };

  return (
    <>
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
                <input
                  type="datetime-local"
                  value={toInput}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setToInput(nextValue);
                    const nextTs = toIsoOrNull(nextValue);
                    const nextTime = nextTs ? new Date(nextTs).getTime() : Number.NaN;
                    setAutoFollowNow(!Number.isNaN(nextTime) && Math.abs(nextTime - Date.now()) <= 60 * 1000);
                  }}
                />
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
              <h3>Biểu đồ vi phạm theo {getTimeSeriesGranularityLabel(timeSeriesGranularity)}</h3>
            </div>
          </div>
          <TimeSeriesChart series={chartSeries} granularity={timeSeriesGranularity} chartConfig={chartConfig} />
        </section>

        <section className="panel">
          <div className="panel-header compact">
            <div>
              <div className="panel-kicker">Lịch sử</div>
              <h3>Lịch sử xe vi phạm</h3>
            </div>
            <div className="history-header-actions">
              <button
                className="button export-button compact-button"
                type="button"
                onClick={() => handleExport("csv")}
                disabled={loading || Boolean(exportingFormat)}
              >
                {exportingFormat === "csv" ? "Đang xuất CSV..." : "Export CSV"}
              </button>
              <button
                className="button export-button compact-button"
                type="button"
                onClick={() => handleExport("xlsx")}
                disabled={loading || Boolean(exportingFormat)}
              >
                {exportingFormat === "xlsx" ? "Đang xuất Excel..." : "Export Excel"}
              </button>
              <div className="badge">{history.length} vi phạm</div>
            </div>
          </div>
          {exportMessage ? <div className="message-bar warning">{exportMessage}</div> : null}
          <div className="history-table-wrap history-scroll-list">
            <table className="history-table">
              <thead>
                <tr>
                  <th>STT</th>
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
                    <td colSpan="8" className="empty-cell">
                      Không có dữ liệu trong khoảng thời gian đã chọn.
                    </td>
                  </tr>
                ) : null}
                {history.map((row, index) => (
                  <tr
                    key={row.id}
                    className="history-row-button"
                    onClick={() => openViolationDetail(row)}
                    onKeyDown={(event) => handleHistoryRowKeyDown(event, row)}
                    role="button"
                    tabIndex={0}
                  >
                    <td>{index + 1}</td>
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

      <ViolationDetailModal open={Boolean(selectedViolation)} violation={selectedViolation} onClose={() => setSelectedViolation(null)} />
    </>
  );
}
