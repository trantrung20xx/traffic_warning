import React, { useMemo, useRef, useState } from "react";
import {
  aggregateTimeSeries,
  formatTimeSeriesAxisLabel,
  formatTimeSeriesTooltip,
  getTimeSeriesGranularityLabel,
} from "../utils";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function buildTickIndexSet(points, granularity) {
  const count = points.length;
  if (count === 0) return new Set();

  const maxTicks = granularity === "hour" ? 8 : 7;
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

export default function TimeSeriesChart({ series, granularity }) {
  const [hoveredPoint, setHoveredPoint] = useState(null);
  const containerRef = useRef(null);
  const points = useMemo(() => aggregateTimeSeries(series, granularity), [granularity, series]);
  const maxValue = Math.max(...points.map((point) => point.total), 1);
  const width = 900;
  const height = 280;
  const paddingLeft = 34;
  const paddingRight = 24;
  const paddingTop = 28;
  const paddingBottom = 52;
  const tickIndexSet = useMemo(() => buildTickIndexSet(points, granularity), [granularity, points]);

  const getX = (index) => {
    return paddingLeft + (index * (width - paddingLeft - paddingRight)) / Math.max(points.length - 1, 1);
  };

  const getY = (point) => {
    return height - paddingBottom - (point.total / maxValue) * (height - paddingTop - paddingBottom);
  };

  const polyline = points
    .map((point, index) => `${getX(index)},${getY(point)}`)
    .join(" ");

  const handleHover = (event, point) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHoveredPoint({
      point,
      left: clamp(event.clientX - rect.left, 70, rect.width - 70),
      top: clamp(event.clientY - rect.top - 12, 18, rect.height - 18),
    });
  };

  const handleFocusPoint = (point, index) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHoveredPoint({
      point,
      left: clamp((getX(index) / width) * rect.width, 70, rect.width - 70),
      top: clamp((getY(point) / height) * rect.height - 12, 18, rect.height - 18),
    });
  };

  const hoveredTooltip = hoveredPoint ? formatTimeSeriesTooltip(hoveredPoint.point, granularity) : null;

  return (
    <div className="timeseries-card" ref={containerRef} onMouseLeave={() => setHoveredPoint(null)}>
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
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
              const y = paddingTop + ratio * (height - paddingTop - paddingBottom);
              return <line key={ratio} x1={paddingLeft} x2={width - paddingRight} y1={y} y2={y} className="grid-line" />;
            })}

            {granularity === "hour"
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
              x2={width - paddingRight}
              y1={height - paddingBottom}
              y2={height - paddingBottom}
              className="grid-line grid-line-axis"
            />

            <polyline
              fill="none"
              stroke="var(--accent)"
              strokeWidth="4"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={polyline}
            />

            {points.map((point, index) => {
              const x = getX(index);
              const y = getY(point);
              const labels = formatTimeSeriesAxisLabel(point, granularity);
              const isTickVisible = tickIndexSet.has(index);
              const isHovered = hoveredPoint?.point.bucket === point.bucket;
              return (
                <g key={point.bucket}>
                  <circle cx={x} cy={y} r={isHovered ? "6.5" : "4.5"} className="point-dot" />
                  <circle
                    cx={x}
                    cy={y}
                    r="14"
                    className="point-hit-area"
                    onMouseEnter={(event) => handleHover(event, point)}
                    onMouseMove={(event) => handleHover(event, point)}
                    onFocus={() => handleFocusPoint(point, index)}
                    onBlur={() => setHoveredPoint(null)}
                    tabIndex={0}
                  />
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
                left: `${hoveredPoint.left}px`,
                top: `${hoveredPoint.top}px`,
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
