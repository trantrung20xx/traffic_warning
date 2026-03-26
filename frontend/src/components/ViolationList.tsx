import React from "react";
import { ViolationEvent } from "../types";

type Props = {
  events: ViolationEvent[];
};

export default function ViolationList({ events }: Props) {
  return (
    <div className="panel">
      <div className="panel-title">Realtime Violations</div>
      <div className="table">
        {events.length === 0 ? (
          <div className="muted">Chưa có vi phạm.</div>
        ) : (
          events.slice(0, 30).map((e) => (
            <div className="row" key={`${e.camera_id}-${e.vehicle_id}-${e.violation}-${e.timestamp}`}>
              <div className="row-main">
                <div className="row-title">
                  {e.violation} (lane {e.lane_id})
                </div>
                <div className="row-sub">
                  {e.vehicle_type} · vehicle #{e.vehicle_id}
                </div>
              </div>
              <div className="row-meta">
                <div>{e.camera_id}</div>
                <div className="muted">{e.timestamp}</div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

