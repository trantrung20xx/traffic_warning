import React, { useEffect, useMemo, useRef } from "react";
import { getManeuverLabel, getVehicleTypeLabel } from "../utils";

export default function CameraCanvas({
  frameWidth,
  frameHeight,
  lanes,
  vehicles,
  overlay = false,
  showTurnRegions = false,
  selectedLaneId = null,
  editable = false,
  onCanvasClick = null,
  editTarget = "lane",
}) {
  const canvasRef = useRef(null);

  const laneColors = useMemo(() => {
    const palette = [
      "rgba(0, 184, 148, 0.24)",
      "rgba(241, 196, 15, 0.24)",
      "rgba(52, 152, 219, 0.24)",
      "rgba(231, 76, 60, 0.24)",
    ];
    const m = new Map();
    lanes.forEach((l, idx) => m.set(l.lane_id, palette[idx % palette.length]));
    return m;
  }, [lanes]);

  const drawPolygon = (ctx, points, options = {}) => {
    if (!points || points.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) {
      ctx.lineTo(points[i][0], points[i][1]);
    }
    if (points.length >= 3) {
      ctx.closePath();
    }
    ctx.strokeStyle = options.strokeStyle || "rgba(255,255,255,0.9)";
    ctx.lineWidth = options.lineWidth || 2;
    if (options.dashed) ctx.setLineDash([8, 6]);
    if (options.fillStyle && points.length >= 3) {
      ctx.fillStyle = options.fillStyle;
      ctx.fill();
    }
    ctx.stroke();
    ctx.setLineDash([]);
    points.forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = options.pointColor || ctx.strokeStyle;
      ctx.fill();
    });
  };

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

      drawPolygon(ctx, pts, {
        fillStyle: laneColors.get(lane.lane_id) || "rgba(200,200,200,0.15)",
        strokeStyle: lane.lane_id === selectedLaneId ? "rgba(255,255,255,1)" : "rgba(255,255,255,0.85)",
        lineWidth: lane.lane_id === selectedLaneId ? 3 : 2,
      });

      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      ctx.fillStyle = "rgba(255,255,255,0.98)";
      ctx.font = "700 14px sans-serif";
      ctx.fillText(`làn ${lane.lane_id}`, cx, cy);

      if (showTurnRegions) {
        Object.entries(lane.turn_regions || {}).forEach(([maneuver, region]) => {
          drawPolygon(ctx, region, {
            dashed: true,
            strokeStyle:
              lane.lane_id === selectedLaneId && maneuver === editTarget
                ? "rgba(255, 209, 102, 1)"
                : "rgba(255,255,255,0.7)",
            lineWidth: 2,
          });
          if (region.length >= 1) {
            ctx.fillStyle = "rgba(255,255,255,0.88)";
            ctx.font = "12px sans-serif";
            ctx.fillText(getManeuverLabel(maneuver), region[0][0] + 6, region[0][1] - 6);
          }
        });
      }
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

      const label = `#${v.vehicle_id} ${getVehicleTypeLabel(v.vehicle_type)}${v.lane_id != null ? ` làn ${v.lane_id}` : ""}`;
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(x1, y1 - 22, ctx.measureText(label).width + 10, 22);
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.font = "14px sans-serif";
      ctx.fillText(label, x1 + 5, y1 - 7);
    });
  }, [editTarget, frameWidth, frameHeight, laneColors, lanes, selectedLaneId, showTurnRegions, vehicles]);

  const handleClick = (event) => {
    if (!editable || !onCanvasClick) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const scaleX = frameWidth / rect.width;
    const scaleY = frameHeight / rect.height;
    const x = Math.round((event.clientX - rect.left) * scaleX);
    const y = Math.round((event.clientY - rect.top) * scaleY);
    onCanvasClick([x, y]);
  };

  return (
    <canvas
      ref={canvasRef}
      onClick={handleClick}
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
              pointerEvents: editable ? "auto" : "none",
              cursor: editable ? "crosshair" : "default",
            }
          : {
              width: "100%",
              height: "auto",
              background: "#12202f",
              borderRadius: 10,
              display: "block",
              cursor: editable ? "crosshair" : "default",
            }
      }
    />
  );
}

