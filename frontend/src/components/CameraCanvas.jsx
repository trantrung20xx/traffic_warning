import React, { useEffect, useMemo, useRef } from "react";

export default function CameraCanvas({ frameWidth, frameHeight, lanes, vehicles, overlay = false }) {
  const canvasRef = useRef(null);

  const laneColors = useMemo(() => {
    const palette = [
      "rgba(0, 180, 255, 0.22)",
      "rgba(0, 255, 140, 0.22)",
      "rgba(255, 180, 0, 0.22)",
      "rgba(255, 90, 90, 0.22)",
    ];
    const m = new Map();
    lanes.forEach((l, idx) => m.set(l.lane_id, palette[idx % palette.length]));
    return m;
  }, [lanes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = frameWidth;
    canvas.height = frameHeight;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, frameWidth, frameHeight);

    // Draw lane polygons
    lanes.forEach((lane) => {
      const pts = lane.polygon;
      if (!pts || pts.length < 3) return;

      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();

      ctx.fillStyle = laneColors.get(lane.lane_id) || "rgba(200,200,200,0.15)";
      ctx.fill();

      ctx.strokeStyle = "rgba(240,240,240,0.85)";
      ctx.lineWidth = 2;
      ctx.stroke();

      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      ctx.fillStyle = "rgba(240,240,240,0.95)";
      ctx.font = "14px Arial";
      ctx.fillText(`lane ${lane.lane_id}`, cx, cy);
    });

    // Draw vehicles
    vehicles.forEach((v) => {
      const x1 = v.bbox.x1;
      const y1 = v.bbox.y1;
      const w = v.bbox.x2 - v.bbox.x1;
      const h = v.bbox.y2 - v.bbox.y1;

      ctx.strokeStyle = v.lane_id != null ? "rgba(255, 80, 80, 0.95)" : "rgba(140,140,140,0.95)";
      ctx.lineWidth = 3;
      ctx.strokeRect(x1, y1, w, h);

      const label = `#${v.vehicle_id} ${v.vehicle_type}${v.lane_id != null ? ` lane ${v.lane_id}` : ""}`;
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(x1, y1 - 22, ctx.measureText(label).width + 10, 22);
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.font = "14px Arial";
      ctx.fillText(label, x1 + 5, y1 - 7);
    });
  }, [frameWidth, frameHeight, lanes, vehicles, laneColors]);

  return (
    <canvas
      ref={canvasRef}
      style={
        overlay
          ? {
              position: "absolute",
              left: 0,
              top: 0,
              width: "100%",
              height: "100%",
              background: "transparent",
              borderRadius: 10,
            }
          : { width: "100%", height: "auto", background: "#0b0f14", borderRadius: 10, display: "block" }
      }
    />
  );
}

