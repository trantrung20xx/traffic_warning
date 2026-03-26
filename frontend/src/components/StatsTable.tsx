import React from "react";
import { StatsRow } from "../types";

type Props = {
  rows: StatsRow[];
};

export default function StatsTable({ rows }: Props) {
  return (
    <div className="panel">
      <div className="panel-title">Statistics</div>
      <div className="table">
        {rows.length === 0 ? (
          <div className="muted">Chưa có dữ liệu.</div>
        ) : (
          rows.slice(0, 50).map((r, idx) => (
            <div className="row" key={`${r.camera_id ?? "all"}-${r.violation}-${idx}`}>
              <div className="row-main">
                <div className="row-title">
                  {r.violation} · {r.vehicle_type}
                </div>
                <div className="row-sub">
                  {r.camera_id ? `camera ${r.camera_id}` : "all"} {r.road_name ? `· ${r.road_name}` : ""}
                  {r.intersection ? ` (${r.intersection})` : ""}
                </div>
              </div>
              <div className="row-meta">
                <div className="big">{r.count}</div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

