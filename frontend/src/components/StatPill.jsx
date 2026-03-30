import React from "react";

export default function StatPill({ label, value }) {
  return (
    <article className="stat-pill">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
