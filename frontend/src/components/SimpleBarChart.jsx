import React from "react";

export default function SimpleBarChart({ title, data, color }) {
  const normalized = [...(data || [])].sort((a, b) => b.value - a.value).slice(0, 8);
  const maxValue = normalized[0]?.value || 1;

  return (
    <article className="chart-card">
      <div className="chart-title">{title}</div>
      {normalized.length === 0 ? <div className="empty-state slim">Chưa có dữ liệu.</div> : null}
      <div className="bar-list">
        {normalized.map((item) => (
          <div className="bar-row" key={item.label}>
            <div className="bar-labels">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
            <div className="bar-track">
              <div
                className="bar-fill"
                style={{
                  width: `${(item.value / maxValue) * 100}%`,
                  background: color,
                }}
              />
            </div>
            {item.subtitle ? <div className="bar-subtitle">{item.subtitle}</div> : null}
          </div>
        ))}
      </div>
    </article>
  );
}
