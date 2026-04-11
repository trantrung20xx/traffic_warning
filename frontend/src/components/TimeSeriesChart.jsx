import React from "react";
import { formatHourBucket } from "../utils";

export default function TimeSeriesChart({ series }) {
  const points = series || [];
  const maxValue = Math.max(...points.map((point) => point.total), 1);
  const width = 900;
  const height = 260;
  const padding = 30;

  const polyline = points
    .map((point, index) => {
      const x = padding + (index * (width - padding * 2)) / Math.max(points.length - 1, 1);
      const y = height - padding - (point.total / maxValue) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="timeseries-card">
      {points.length === 0 ? <div className="empty-state slim">Chưa có dữ liệu biểu đồ.</div> : null}
      {points.length > 0 ? (
        <svg viewBox={`0 0 ${width} ${height}`} className="timeseries-svg" role="img" aria-label="Biểu đồ vi phạm theo giờ">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = padding + ratio * (height - padding * 2);
            return <line key={ratio} x1={padding} x2={width - padding} y1={y} y2={y} className="grid-line" />;
          })}
          <polyline
            fill="none"
            stroke="var(--accent)"
            strokeWidth="4"
            strokeLinejoin="round"
            strokeLinecap="round"
            points={polyline}
          />
          {points.map((point, index) => {
            const x = padding + (index * (width - padding * 2)) / Math.max(points.length - 1, 1);
            const y = height - padding - (point.total / maxValue) * (height - padding * 2);
            return (
              <g key={point.bucket}>
                <circle cx={x} cy={y} r="5" className="point-dot" />
                <text x={x} y={height - 8} textAnchor="middle" className="axis-label">
                  {formatHourBucket(point.bucket)}
                </text>
                <text x={x} y={y - 12} textAnchor="middle" className="value-label">
                  {point.total}
                </text>
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}
