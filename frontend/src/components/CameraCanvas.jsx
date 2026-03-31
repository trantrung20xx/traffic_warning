import React, { useEffect, useMemo, useRef, useState } from "react";
import { getManeuverLabel, getVehicleTypeLabel } from "../utils";

const VERTEX_HIT_RADIUS = 12;
const EDGE_HIT_DISTANCE = 10;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function clampPoint([x, y], frameWidth, frameHeight) {
  return [clamp(Math.round(x), 0, frameWidth), clamp(Math.round(y), 0, frameHeight)];
}

function pointInPolygon(point, polygon) {
  if (!polygon || polygon.length < 3) return false;
  let inside = false;
  const [x, y] = point;
  for (let i = 0; i < polygon.length; i += 1) {
    const [x1, y1] = polygon[i];
    const [x2, y2] = polygon[(i + 1) % polygon.length];
    const intersects = (y1 > y) !== (y2 > y);
    if (!intersects) continue;
    const xIntersect = ((x2 - x1) * (y - y1)) / (y2 - y1 || 1e-9) + x1;
    if (xIntersect > x) inside = !inside;
  }
  return inside;
}

function distanceBetween(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function projectPointToSegment(point, start, end) {
  const [px, py] = point;
  const [x1, y1] = start;
  const [x2, y2] = end;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lengthSquared = dx * dx + dy * dy;

  if (lengthSquared === 0) {
    return { point: start, distance: distanceBetween(point, start) };
  }

  const t = clamp(((px - x1) * dx + (py - y1) * dy) / lengthSquared, 0, 1);
  const projected = [x1 + dx * t, y1 + dy * t];
  return { point: projected, distance: distanceBetween(point, projected) };
}

function findClosestVertex(points, point) {
  if (!points?.length) return null;
  let best = null;
  points.forEach((candidate, index) => {
    const distance = distanceBetween(candidate, point);
    if (distance <= VERTEX_HIT_RADIUS && (!best || distance < best.distance)) {
      best = { index, distance };
    }
  });
  return best;
}

function findClosestEdge(points, point) {
  if (!points || points.length < 2) return null;
  const edgeCount = points.length >= 3 ? points.length : points.length - 1;
  let best = null;

  for (let index = 0; index < edgeCount; index += 1) {
    const start = points[index];
    const end = points[(index + 1) % points.length];
    const projection = projectPointToSegment(point, start, end);
    if (projection.distance <= EDGE_HIT_DISTANCE && (!best || projection.distance < best.distance)) {
      best = { index, distance: projection.distance, point: projection.point };
    }
  }

  return best;
}

export default function CameraCanvas({
  frameWidth,
  frameHeight,
  lanes,
  vehicles,
  overlay = false,
  showTurnRegions = false,
  selectedLaneId = null,
  selectedVertexIndex = null,
  editable = false,
  onCanvasClick = null,
  onPolygonReplace = null,
  onVertexSelect = null,
  editTarget = "lane",
}) {
  const canvasRef = useRef(null);
  const dragStateRef = useRef(null);
  const editablePointsRef = useRef([]);
  const [hoverEdge, setHoverEdge] = useState(null);
  const [hoverVertexIndex, setHoverVertexIndex] = useState(null);
  const [isDragging, setIsDragging] = useState(false);

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

  const selectedLane = useMemo(
    () => lanes.find((lane) => lane.lane_id === selectedLaneId) || null,
    [lanes, selectedLaneId],
  );

  const editablePoints = useMemo(() => {
    if (!selectedLane) return [];
    if (editTarget === "lane") return selectedLane.polygon || [];
    return selectedLane.turn_regions?.[editTarget] || [];
  }, [editTarget, selectedLane]);

  useEffect(() => {
    editablePointsRef.current = editablePoints;
  }, [editablePoints]);

  useEffect(() => {
    dragStateRef.current = null;
    setIsDragging(false);
    setHoverEdge(null);
    setHoverVertexIndex(null);
  }, [editTarget, selectedLaneId, editable]);

  const replaceEditablePolygon = (points) => {
    if (!onPolygonReplace) return;
    onPolygonReplace(points.map((point) => clampPoint(point, frameWidth, frameHeight)));
  };

  const getCanvasPointFromClient = (clientX, clientY) => {
    const canvas = canvasRef.current;
    if (!canvas) return [0, 0];
    const rect = canvas.getBoundingClientRect();
    const scaleX = frameWidth / rect.width;
    const scaleY = frameHeight / rect.height;
    return [(clientX - rect.left) * scaleX, (clientY - rect.top) * scaleY];
  };

  const getCanvasPoint = (event) => {
    return getCanvasPointFromClient(event.clientX, event.clientY);
  };

  const updateHoverState = (point) => {
    if (!editable || !selectedLane) {
      setHoverEdge(null);
      setHoverVertexIndex(null);
      return;
    }

    const vertex = findClosestVertex(editablePointsRef.current, point);
    if (vertex) {
      setHoverVertexIndex(vertex.index);
      setHoverEdge(null);
      return;
    }

    setHoverVertexIndex(null);
    setHoverEdge(findClosestEdge(editablePointsRef.current, point));
  };

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
    points.forEach(([x, y], index) => {
      const hovered = options.isEditableTarget && index === hoverVertexIndex;
      const selected = options.isEditableTarget && index === selectedVertexIndex;
      ctx.beginPath();
      ctx.arc(x, y, hovered || selected ? 7 : 5, 0, Math.PI * 2);
      ctx.fillStyle = selected
        ? "rgba(0, 214, 143, 0.96)"
        : hovered
          ? "rgba(255, 209, 102, 0.96)"
          : options.pointColor || ctx.strokeStyle;
      ctx.fill();
      if (options.isEditableTarget) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(7, 19, 31, 0.9)";
        ctx.stroke();
      }
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

    lanes.forEach((lane) => {
      const pts = lane.polygon;
      if (!pts || pts.length < 2) return;

      const isEditableLane = editable && lane.lane_id === selectedLaneId && editTarget === "lane";
      drawPolygon(ctx, pts, {
        fillStyle: pts.length >= 3 ? laneColors.get(lane.lane_id) || "rgba(200,200,200,0.15)" : null,
        strokeStyle: lane.lane_id === selectedLaneId ? "rgba(255,255,255,1)" : "rgba(255,255,255,0.85)",
        lineWidth: lane.lane_id === selectedLaneId ? 3 : 2,
        isEditableTarget: isEditableLane,
      });

      if (pts.length >= 3) {
        const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
        const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
        ctx.fillStyle = "rgba(255,255,255,0.98)";
        ctx.font = "700 14px sans-serif";
        ctx.fillText(`làn ${lane.lane_id}`, cx, cy);
      }

      if (showTurnRegions) {
        Object.entries(lane.turn_regions || {}).forEach(([maneuver, region]) => {
          const isEditableRegion = editable && lane.lane_id === selectedLaneId && maneuver === editTarget;
          drawPolygon(ctx, region, {
            dashed: true,
            strokeStyle:
              lane.lane_id === selectedLaneId && maneuver === editTarget
                ? "rgba(255, 209, 102, 1)"
                : "rgba(255,255,255,0.7)",
            lineWidth: isEditableRegion ? 3 : 2,
            isEditableTarget: isEditableRegion,
          });
          if (region.length >= 1) {
            ctx.fillStyle = "rgba(255,255,255,0.88)";
            ctx.font = "12px sans-serif";
            ctx.fillText(getManeuverLabel(maneuver), region[0][0] + 6, region[0][1] - 6);
          }
        });
      }
    });

    if (editable && hoverEdge?.point) {
      const [hx, hy] = hoverEdge.point;
      ctx.beginPath();
      ctx.arc(hx, hy, 6, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 209, 102, 0.95)";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(7, 19, 31, 0.9)";
      ctx.stroke();
    }

    vehicles.forEach((v) => {
      const x1 = v.bbox.x1;
      const y1 = v.bbox.y1;
      const w = v.bbox.x2 - v.bbox.x1;
      const h = v.bbox.y2 - v.bbox.y1;

      ctx.strokeStyle = v.lane_id != null ? "rgba(255, 80, 80, 0.95)" : "rgba(140,140,140,0.95)";
      ctx.lineWidth = 3;
      ctx.strokeRect(x1, y1, w, h);

      const label = `#${v.vehicle_id} ${getVehicleTypeLabel(v.vehicle_type)}${v.lane_id != null ? ` làn ${v.lane_id}` : ""}`;
      ctx.font = "14px sans-serif";
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(x1, y1 - 22, ctx.measureText(label).width + 10, 22);
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.fillText(label, x1 + 5, y1 - 7);
    });

    if (editable) {
      // This matches real traffic-camera setup tools: adjust fixed road polygons by vertices or whole-region drag.
      // Scale/rotate are intentionally omitted because the camera image is fixed and rules depend on exact pixels.
      ctx.fillStyle = "rgba(255,255,255,0.78)";
      ctx.font = "12px sans-serif";
      ctx.fillText("Keo diem de chinh, keo trong vung de di chuyen, click canh de chen diem. Khong scale/rotate vi camera co dinh.", 14, 22);
    }
  }, [
    editTarget,
    editable,
    frameWidth,
    frameHeight,
    hoverEdge,
    hoverVertexIndex,
    laneColors,
    lanes,
    selectedVertexIndex,
    selectedLaneId,
    showTurnRegions,
    vehicles,
  ]);

  const handleMouseDown = (event) => {
    if (!editable) return;
    const point = getCanvasPoint(event);
    const points = editablePointsRef.current;
    const vertex = findClosestVertex(points, point);

    if (vertex) {
      onVertexSelect?.(vertex.index);
      dragStateRef.current = { mode: "vertex", vertexIndex: vertex.index };
      setIsDragging(true);
      return;
    }

    const edge = findClosestEdge(points, point);
    if (edge && onPolygonReplace) {
      const nextPoint = clampPoint(edge.point, frameWidth, frameHeight);
      const nextPoints = [...points.slice(0, edge.index + 1), nextPoint, ...points.slice(edge.index + 1)];
      replaceEditablePolygon(nextPoints);
      onVertexSelect?.(edge.index + 1);
      dragStateRef.current = { mode: "vertex", vertexIndex: edge.index + 1 };
      setHoverVertexIndex(edge.index + 1);
      setHoverEdge(null);
      setIsDragging(true);
      return;
    }

    if (points.length >= 3 && pointInPolygon(point, points) && onPolygonReplace) {
      dragStateRef.current = {
        mode: "polygon",
        startPoint: point,
        startPoints: points.map(([x, y]) => [x, y]),
      };
      setIsDragging(true);
      return;
    }

    if (onCanvasClick) {
      onVertexSelect?.(null);
      onCanvasClick(clampPoint(point, frameWidth, frameHeight));
    }
  };

  const handleMouseMove = (event) => {
    const point = getCanvasPoint(event);
    const dragState = dragStateRef.current;

    if (!dragState) {
      updateHoverState(point);
      return;
    }

    if (dragState.mode === "vertex") {
      replaceEditablePolygon(
        editablePointsRef.current.map((vertex, index) =>
          index === dragState.vertexIndex ? clampPoint(point, frameWidth, frameHeight) : vertex,
        ),
      );
      return;
    }

    if (dragState.mode === "polygon") {
      const dx = point[0] - dragState.startPoint[0];
      const dy = point[1] - dragState.startPoint[1];
      replaceEditablePolygon(
        dragState.startPoints.map(([x, y]) => clampPoint([x + dx, y + dy], frameWidth, frameHeight)),
      );
    }
  };

  const stopDragging = () => {
    dragStateRef.current = null;
    setIsDragging(false);
  };

  useEffect(() => {
    if (!isDragging) return undefined;

    const handleWindowMouseMove = (event) => {
      const point = getCanvasPointFromClient(event.clientX, event.clientY);
      const dragState = dragStateRef.current;
      if (!dragState) return;

      if (dragState.mode === "vertex") {
        replaceEditablePolygon(
          editablePointsRef.current.map((vertex, index) =>
            index === dragState.vertexIndex ? clampPoint(point, frameWidth, frameHeight) : vertex,
          ),
        );
        return;
      }

      if (dragState.mode === "polygon") {
        const dx = point[0] - dragState.startPoint[0];
        const dy = point[1] - dragState.startPoint[1];
        replaceEditablePolygon(
          dragState.startPoints.map(([x, y]) => clampPoint([x + dx, y + dy], frameWidth, frameHeight)),
        );
      }
    };

    const handleWindowMouseUp = () => {
      stopDragging();
    };

    window.addEventListener("mousemove", handleWindowMouseMove);
    window.addEventListener("mouseup", handleWindowMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleWindowMouseMove);
      window.removeEventListener("mouseup", handleWindowMouseUp);
    };
  }, [frameHeight, frameWidth, isDragging]);

  return (
    <canvas
      ref={canvasRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={stopDragging}
      onMouseLeave={() => {
        if (!isDragging) {
          setHoverEdge(null);
          setHoverVertexIndex(null);
        }
      }}
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
              cursor: editable ? (isDragging ? "grabbing" : "crosshair") : "default",
            }
          : {
              width: "100%",
              height: "auto",
              background: "#12202f",
              borderRadius: 10,
              display: "block",
              cursor: editable ? (isDragging ? "grabbing" : "crosshair") : "default",
            }
      }
    />
  );
}

