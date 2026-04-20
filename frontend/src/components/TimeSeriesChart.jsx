import React, { useMemo, useRef, useState } from "react";
import {
  aggregateTimeSeries,
  formatTimeSeriesAxisLabel,
  formatTimeSeriesTooltip,
  getTimeSeriesGranularityLabel,
  normalizeAnalyticsChartConfig,
} from "../utils";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function formatYAxisValue(value) {
  return new Intl.NumberFormat("vi-VN", {
    maximumFractionDigits: value >= 10 ? 0 : 1,
  }).format(value);
}

function buildTickIndexSet(points, granularity, chartConfig) {
  const count = points.length;
  if (count === 0) return new Set();
  const normalizedChartConfig = normalizeAnalyticsChartConfig(chartConfig);

  if (granularity === "minute") {
    const hourBoundaryIndexes = [];
    points.forEach((point, index) => {
      const minuteValue = Number(String(point.bucket).slice(14, 16));
      if (minuteValue % Math.max(normalizedChartConfig.minute_axis_label_interval_minutes, 1) === 0) {
        hourBoundaryIndexes.push(index);
      }
    });

    const indexSet = new Set([0, count - 1]);
    const step = Math.max(1, Math.ceil(hourBoundaryIndexes.length / Math.max(normalizedChartConfig.minute_axis_max_ticks, 1)));
    for (let index = 0; index < hourBoundaryIndexes.length; index += step) {
      indexSet.add(hourBoundaryIndexes[index]);
    }

    for (let index = 1; index < count; index += 1) {
      const currentDay = String(points[index].bucket).slice(0, 10);
      const previousDay = String(points[index - 1].bucket).slice(0, 10);
      if (currentDay !== previousDay) {
        indexSet.add(index);
      }
    }

    return indexSet;
  }

  const maxTicks =
    granularity === "hour"
      ? Math.max(normalizedChartConfig.hour_axis_max_ticks, 1)
      : Math.max(normalizedChartConfig.overview_axis_max_ticks, 1);
  const indexSet = new Set([0, count - 1]);
  const step = Math.max(1, Math.ceil(count / maxTicks));

  for (let index = 0; index < count; index += step) {
    indexSet.add(index);
  }

  if (granularity === "hour") {
    for (let index = 1; index < count; index += 1) {
      const currentDay = String(points[index].bucket).slice(0, 10);
      const previousDay = String(points[index - 1].bucket).slice(0, 10);
      if (currentDay !== previousDay) {
        indexSet.add(index);
      }
    }
  }

  return indexSet;
}

export default function TimeSeriesChart({ series, granularity, chartConfig }) {
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const containerRef = useRef(null);
  const points = useMemo(() => aggregateTimeSeries(series, granularity), [granularity, series]);
  const maxValue = Math.max(...points.map((point) => point.total), 1);
  const normalizedChartConfig = useMemo(() => normalizeAnalyticsChartConfig(chartConfig), [chartConfig]);
  const width = 900;
  const height = 280;
  const paddingLeft = 58;
  const paddingRight = 24;
  const paddingTop = 28;
  const paddingBottom = 52;
  const yAxisRatios = [0, 0.25, 0.5, 0.75, 1];
  const tickIndexSet = useMemo(
    () => buildTickIndexSet(points, granularity, normalizedChartConfig),
    [granularity, normalizedChartConfig, points],
  );
  const showPointMarkers = points.length <= normalizedChartConfig.point_markers_max_points;
  const hoveredPoint = hoveredIndex !== null ? points[hoveredIndex] : null;
  const plotWidth = width - paddingLeft - paddingRight;
  const plotHeight = height - paddingTop - paddingBottom;
  const yAxisTicks = yAxisRatios.map((ratio) => ({
    ratio,
    y: paddingTop + ratio * plotHeight,
    value: maxValue * (1 - ratio),
  }));

  const getX = (index) => {
    if (points.length <= 1) {
      return paddingLeft + plotWidth / 2;
    }
    return paddingLeft + (index * plotWidth) / (points.length - 1);
  };

  const getY = (point) => {
    return height - paddingBottom - (point.total / maxValue) * plotHeight;
  };

  const polyline = points
    .map((point, index) => `${getX(index)},${getY(point)}`)
    .join(" ");

  const setHoveredIndexFromPosition = (clientX) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect || points.length === 0) return;
    const localX = clamp(((clientX - rect.left) / rect.width) * width, paddingLeft, width - paddingRight);
    const ratio = plotWidth <= 0 ? 0 : (localX - paddingLeft) / plotWidth;
    const index = clamp(Math.round(ratio * Math.max(points.length - 1, 0)), 0, Math.max(points.length - 1, 0));
    setHoveredIndex(index);
  };

  const handlePlotHover = (event) => {
    setHoveredIndexFromPosition(event.clientX);
  };

  const handleFocusPoint = (index) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHoveredIndex(index);
  };

  const hoveredTooltip = hoveredPoint ? formatTimeSeriesTooltip(hoveredPoint, granularity) : null;
  const hoveredTooltipPosition = hoveredPoint
    ? (() => {
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) return null;
        return {
          left: clamp((getX(hoveredIndex) / width) * rect.width, 70, rect.width - 70),
          top: clamp((getY(hoveredPoint) / height) * rect.height - 12, 18, rect.height - 18),
        };
      })()
    : null;

  return (
    <div className="timeseries-card" ref={containerRef} onMouseLeave={() => setHoveredIndex(null)}>
      <div className="timeseries-meta">Đơn vị tổng hợp: {getTimeSeriesGranularityLabel(granularity)}</div>
      {points.length === 0 ? <div className="empty-state slim">Chưa có dữ liệu biểu đồ.</div> : null}
      {points.length > 0 ? (
        <>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="timeseries-svg"
            role="img"
            aria-label={`Biểu đồ vi phạm theo ${getTimeSeriesGranularityLabel(granularity)}`}
          >
            {yAxisTicks.map((tick) => (
              <g key={tick.ratio}>
                <line
                  x1={paddingLeft}
                  x2={width - paddingRight}
                  y1={tick.y}
                  y2={tick.y}
                  className="grid-line"
                />
                <text
                  x={paddingLeft - 10}
                  y={tick.y}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="value-label"
                >
                  {formatYAxisValue(tick.value)}
                </text>
              </g>
            ))}

            {granularity === "minute" || granularity === "hour"
              ? points.map((point, index) => {
                  if (index === 0) return null;
                  const currentDay = String(point.bucket).slice(0, 10);
                  const previousDay = String(points[index - 1].bucket).slice(0, 10);
                  if (currentDay === previousDay) return null;
                  const x = getX(index);
                  return (
                    <line
                      key={`boundary-${point.bucket}`}
                      x1={x}
                      x2={x}
                      y1={paddingTop}
                      y2={height - paddingBottom}
                      className="grid-line grid-line-boundary"
                    />
                  );
                })
              : null}

            <line
              x1={paddingLeft}
              x2={paddingLeft}
              y1={paddingTop}
              y2={height - paddingBottom}
              className="grid-line grid-line-axis"
            />
            <line
              x1={paddingLeft}
              x2={width - paddingRight}
              y1={height - paddingBottom}
              y2={height - paddingBottom}
              className="grid-line grid-line-axis"
            />

            {hoveredPoint ? (
              <line
                x1={getX(hoveredIndex)}
                x2={getX(hoveredIndex)}
                y1={paddingTop}
                y2={height - paddingBottom}
                className="grid-line grid-line-hover"
              />
            ) : null}

            <polyline
              fill="none"
              stroke="var(--accent)"
              strokeWidth="4"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={polyline}
            />

            <rect
              x={paddingLeft}
              y={paddingTop}
              width={plotWidth}
              height={plotHeight}
              className="plot-hit-area"
              onMouseEnter={handlePlotHover}
              onMouseMove={handlePlotHover}
            />

            {points.map((point, index) => {
              const x = getX(index);
              const y = getY(point);
              const labels = formatTimeSeriesAxisLabel(point, granularity);
              const isTickVisible = tickIndexSet.has(index);
              const isHovered = hoveredPoint?.bucket === point.bucket;
              return (
                <g key={point.bucket}>
                  {showPointMarkers || isHovered ? (
                    <circle cx={x} cy={y} r={isHovered ? "6.5" : "4.5"} className="point-dot" />
                  ) : null}
                  {showPointMarkers ? (
                    <circle
                      cx={x}
                      cy={y}
                      r="14"
                      className="point-hit-area"
                      onMouseEnter={() => setHoveredIndex(index)}
                      onMouseMove={() => setHoveredIndex(index)}
                      onFocus={() => handleFocusPoint(index)}
                      onBlur={() => setHoveredIndex(null)}
                      tabIndex={0}
                    />
                  ) : null}
                  {isTickVisible ? (
                    <text x={x} y={height - 24} textAnchor="middle" className="axis-label">
                      <tspan x={x} dy="0">
                        {labels.primary}
                      </tspan>
                      {labels.secondary ? (
                        <tspan x={x} dy="14" className="axis-label axis-label-secondary">
                          {labels.secondary}
                        </tspan>
                      ) : null}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>

          {hoveredTooltip ? (
            <div
              className="timeseries-tooltip"
              style={{
                left: `${hoveredTooltipPosition?.left ?? 0}px`,
                top: `${hoveredTooltipPosition?.top ?? 0}px`,
              }}
            >
              <div className="timeseries-tooltip-title">{hoveredTooltip.title}</div>
              <div className="timeseries-tooltip-total">{hoveredTooltip.total}</div>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
