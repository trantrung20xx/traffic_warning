import React from "react";
import AppIcon from "./AppIcon";

export default function StatPill({ label, value, icon }) {
  return (
    <article className="stat-pill">
      <div className="stat-pill-label">
        <span>{label}</span>
        {icon ? <AppIcon name={icon} size={17} /> : null}
      </div>
      <strong>{value}</strong>
    </article>
  );
}
