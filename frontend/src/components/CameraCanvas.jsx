import React, { useEffect, useMemo, useRef, useState } from "react";
import { drawBackgroundImage, useBackgroundImage } from "./canvas/BackgroundImageLayer";
import { drawPolygon } from "./canvas/PolygonLayer";
import {
  denormalizeLane,
  getEditTargetLabel,
  getManeuverLabel,
  getTargetPoints,
  getVehicleTypeLabel,
  isLineEditTarget,
  normalizePoint,
  parseEditTarget,
} from "../utils";

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

function drawTrajectory(ctx, points) {
  const pathPoints = (points || []).filter(
    (point) =>
      Array.isArray(point) &&
      point.length >= 2 &&
      Number.isFinite(Number(point[0])) &&
      Number.isFinite(Number(point[1])),
  );
  if (pathPoints.length < 2) return;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(Number(pathPoints[0][0]), Number(pathPoints[0][1]));

  if (pathPoints.length === 2) {
    ctx.lineTo(Number(pathPoints[1][0]), Number(pathPoints[1][1]));
  } else {
    for (let idx = 1; idx < pathPoints.length - 1; idx += 1) {
      const current = pathPoints[idx];
      const next = pathPoints[idx + 1];
      const midX = (Number(current[0]) + Number(next[0])) / 2;
      const midY = (Number(current[1]) + Number(next[1])) / 2;
      ctx.quadraticCurveTo(Number(current[0]), Number(current[1]), midX, midY);
    }
    const last = pathPoints[pathPoints.length - 1];
    ctx.lineTo(Number(last[0]), Number(last[1]));
  }

  ctx.strokeStyle = "rgba(0, 255, 128, 0.92)";
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.setLineDash([]);
  ctx.stroke();
  ctx.restore();
}

export default function CameraCanvas({
  frameWidth,
  frameHeight,
  lanes,
  vehicles,
  trajectoryOverlays = [],
  processingFps = null,
  overlay = false,
  selectedLaneId = null,
  selectedVertexIndex = null,
  editable = false,
  onCanvasClick = null,
  onPolygonReplace = null,
  onVertexSelect = null,
  editTarget = "lane_polygon",
  selectedManeuver = "straight",
  backgroundImageUrl = null,
}) {
  const canvasRef = useRef(null);
  const dragStateRef = useRef(null);
  const editablePointsRef = useRef([]);
  const [hoverEdge, setHoverEdge] = useState(null);
  const [hoverVertexIndex, setHoverVertexIndex] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const backgroundImage = useBackgroundImage(backgroundImageUrl);

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

  const renderedLanes = useMemo(
    () => lanes.map((lane) => denormalizeLane(lane, frameWidth, frameHeight)),
    [frameHeight, frameWidth, lanes],
  );

  const selectedLane = useMemo(
    () => lanes.find((lane) => lane.lane_id === selectedLaneId) || null,
    [lanes, selectedLaneId],
  );

  const renderedSelectedLane = useMemo(
    () => renderedLanes.find((lane) => lane.lane_id === selectedLaneId) || null,
    [renderedLanes, selectedLaneId],
  );

  const editablePoints = useMemo(() => {
    return getTargetPoints({
      lane: renderedSelectedLane,
      laneConfig: null,
      editTarget,
      selectedManeuver,
    });
  }, [editTarget, renderedSelectedLane, selectedManeuver]);

  const parsedEditTarget = useMemo(() => parseEditTarget(editTarget), [editTarget]);

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
    onPolygonReplace(points.map((point) => normalizePoint(clampPoint(point, frameWidth, frameHeight), frameWidth, frameHeight)));
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
    if (isLineEditTarget(editTarget)) {
      setHoverEdge(null);
      return;
    }
    setHoverEdge(findClosestEdge(editablePointsRef.current, point));
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = frameWidth;
    canvas.height = frameHeight;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, frameWidth, frameHeight);
    drawBackgroundImage(ctx, backgroundImage, frameWidth, frameHeight);

    renderedLanes.forEach((lane) => {
      const pts = lane.polygon;
      if (!pts || pts.length < 2) return;

      const isEditableLane = editable && lane.lane_id === selectedLaneId && editTarget === "lane_polygon";
      drawPolygon(ctx, pts, {
        fillStyle: pts.length >= 3 ? laneColors.get(lane.lane_id) || "rgba(200,200,200,0.15)" : null,
        strokeStyle: lane.lane_id === selectedLaneId ? "rgba(255,255,255,1)" : "rgba(255,255,255,0.85)",
        lineWidth: lane.lane_id === selectedLaneId ? 3 : 2,
        isEditableTarget: isEditableLane,
        hoverVertexIndex,
        selectedVertexIndex,
      });

      if (pts.length >= 3) {
        const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
        const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
        ctx.fillStyle = "rgba(255,255,255,0.98)";
        ctx.font = "700 14px sans-serif";
        ctx.fillText(`làn ${lane.lane_id}`, cx, cy);
      }

      [
        {
          key: "approach_zone",
          points: lane.approach_zone || [],
          strokeStyle: "rgba(46, 204, 113, 0.95)",
          label: "chuẩn bị rẽ",
          dashed: true,
        },
        {
          key: "commit_gate",
          points: lane.commit_gate || [],
          strokeStyle: "rgba(230, 126, 34, 0.95)",
          label: "bắt đầu rẽ",
          dashed: true,
        },
        {
          key: "commit_line",
          points: lane.commit_line || [],
          strokeStyle: "rgba(230, 126, 34, 0.95)",
          label: "vạch bắt đầu rẽ",
          dashed: false,
        },
      ].forEach((geometry) => {
        if (geometry.points.length < 2) return;
        const isEditableGeometry = editable && lane.lane_id === selectedLaneId && editTarget === geometry.key;
        drawPolygon(ctx, geometry.points, {
          dashed: geometry.dashed,
          strokeStyle: isEditableGeometry ? "rgba(255, 209, 102, 1)" : geometry.strokeStyle,
          lineWidth: isEditableGeometry ? 3 : 2,
          isEditableTarget: isEditableGeometry,
          hoverVertexIndex,
          selectedVertexIndex,
        });
        const anchor = geometry.points[0];
        if (anchor) {
          ctx.fillStyle = "rgba(255,255,255,0.88)";
          ctx.font = "12px sans-serif";
          ctx.fillText(`${geometry.label} · làn ${lane.lane_id}`, anchor[0] + 6, anchor[1] - 6);
        }
      });

      Object.entries(lane.maneuvers || {}).forEach(([maneuver, cfg]) => {
        const maneuverLabel = getManeuverLabel(maneuver);
        const movementPath = cfg.movement_path || [];
        const turnCorridor = cfg.turn_corridor || [];
        const exitZone = cfg.exit_zone || [];
        const exitLine = cfg.exit_line || [];

        const isSelectedManeuver = lane.lane_id === selectedLaneId && maneuver === selectedManeuver;

        if (movementPath.length >= 2) {
          const isEditablePath = editable && isSelectedManeuver && editTarget === "movement_path";
          drawPolygon(ctx, movementPath, {
            dashed: true,
            strokeStyle: isEditablePath ? "rgba(255, 209, 102, 1)" : "rgba(52, 152, 219, 0.95)",
            lineWidth: isEditablePath ? 3 : 2,
            isEditableTarget: isEditablePath,
            hoverVertexIndex,
            selectedVertexIndex,
          });
          const anchor = movementPath[0];
          if (anchor) {
            ctx.fillStyle = "rgba(255,255,255,0.88)";
            ctx.font = "12px sans-serif";
            ctx.fillText(`${maneuverLabel} · lane ${lane.lane_id}`, anchor[0] + 6, anchor[1] - 6);
          }
        } else if (turnCorridor.length >= 2) {
          drawPolygon(ctx, turnCorridor, {
            dashed: true,
            strokeStyle: "rgba(52, 152, 219, 0.65)",
            lineWidth: 1.5,
          });
        }

        if (exitZone.length >= 3) {
          const isEditableExitZone = editable && isSelectedManeuver && editTarget === "exit_zone";
          drawPolygon(ctx, exitZone, {
            dashed: true,
            strokeStyle: isEditableExitZone ? "rgba(255, 209, 102, 1)" : "rgba(155, 89, 182, 0.95)",
            lineWidth: isEditableExitZone ? 3 : 2,
            isEditableTarget: isEditableExitZone,
            hoverVertexIndex,
            selectedVertexIndex,
          });
        }

        if (exitLine.length >= 2) {
          const isEditableExitLine = editable && isSelectedManeuver && editTarget === "exit_line";
          drawPolygon(ctx, exitLine, {
            dashed: false,
            strokeStyle: isEditableExitLine ? "rgba(255, 209, 102, 1)" : "rgba(230, 126, 34, 0.95)",
            lineWidth: isEditableExitLine ? 3 : 2,
            isEditableTarget: isEditableExitLine,
            hoverVertexIndex,
            selectedVertexIndex,
          });
        }
      });
    });

    if (editable && !isLineEditTarget(editTarget) && hoverEdge?.point) {
      const [hx, hy] = hoverEdge.point;
      ctx.beginPath();
      ctx.arc(hx, hy, 6, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 209, 102, 0.95)";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(7, 19, 31, 0.9)";
      ctx.stroke();
    }

    trajectoryOverlays.forEach((row) => {
      drawTrajectory(ctx, row?.points || []);
    });

    vehicles.forEach((v) => {
      const x1 = v.bbox.x1;
      const y1 = v.bbox.y1;
      const w = v.bbox.x2 - v.bbox.x1;
      const h = v.bbox.y2 - v.bbox.y1;
      const strokeColor = v.isViolating ? "rgba(255, 107, 87, 0.98)" : "rgba(0, 214, 143, 0.98)";
      const labelBackground = v.isViolating ? "rgba(120, 28, 18, 0.82)" : "rgba(7, 76, 54, 0.82)";

      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = 3;
      ctx.strokeRect(x1, y1, w, h);

      const label = `#${v.vehicle_id} ${getVehicleTypeLabel(v.vehicle_type)}${v.lane_id != null ? ` làn ${v.lane_id}` : ""}`;
      ctx.font = "14px sans-serif";
      ctx.fillStyle = labelBackground;
      ctx.fillRect(x1, y1 - 22, ctx.measureText(label).width + 10, 22);
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.fillText(label, x1 + 5, y1 - 7);
    });

    if (processingFps != null && Number.isFinite(processingFps)) {
      const fpsLabel = `FPS: ${processingFps.toFixed(1)}`;
      ctx.font = "700 14px sans-serif";
      const textWidth = ctx.measureText(fpsLabel).width;
      const boxWidth = textWidth + 18;
      const boxHeight = 28;
      const boxX = frameWidth - boxWidth - 14;
      const boxY = 14;
      const radius = 10;

      ctx.beginPath();
      ctx.moveTo(boxX + radius, boxY);
      ctx.lineTo(boxX + boxWidth - radius, boxY);
      ctx.quadraticCurveTo(boxX + boxWidth, boxY, boxX + boxWidth, boxY + radius);
      ctx.lineTo(boxX + boxWidth, boxY + boxHeight - radius);
      ctx.quadraticCurveTo(boxX + boxWidth, boxY + boxHeight, boxX + boxWidth - radius, boxY + boxHeight);
      ctx.lineTo(boxX + radius, boxY + boxHeight);
      ctx.quadraticCurveTo(boxX, boxY + boxHeight, boxX, boxY + boxHeight - radius);
      ctx.lineTo(boxX, boxY + radius);
      ctx.quadraticCurveTo(boxX, boxY, boxX + radius, boxY);
      ctx.closePath();
      ctx.fillStyle = "rgba(7, 19, 31, 0.78)";
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(255,255,255,0.16)";
      ctx.stroke();

      ctx.fillStyle = "rgba(255,255,255,0.96)";
      ctx.fillText(fpsLabel, boxX + 9, boxY + 18);
    }

    if (editable) {
      // Cách chỉnh này giống công cụ cấu hình camera giao thông thực tế: kéo từng đỉnh
      // hoặc kéo cả vùng. Không hỗ trợ scale/rotate vì camera cố định và luật phụ thuộc pixel chính xác.
      ctx.fillStyle = "rgba(255,255,255,0.78)";
      ctx.font = "12px sans-serif";
      ctx.fillText(
        `${getEditTargetLabel(editTarget, parsedEditTarget.scope === "lane" || parsedEditTarget.scope === "lane_maneuver" ? selectedLaneId : null, selectedManeuver)}: keo diem de chinh, keo trong vung de di chuyen, click canh de chen diem.`,
        14,
        22,
      );
    }
  }, [
    editTarget,
    editable,
    frameWidth,
    frameHeight,
    backgroundImage,
    hoverEdge,
    hoverVertexIndex,
    laneColors,
    parsedEditTarget.scope,
    selectedManeuver,
    renderedLanes,
    selectedVertexIndex,
    selectedLaneId,
    vehicles,
    trajectoryOverlays,
    processingFps,
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
    if (edge && onPolygonReplace && !isLineEditTarget(editTarget)) {
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

    if (isLineEditTarget(editTarget) && edge && onPolygonReplace) {
      dragStateRef.current = {
        mode: "polygon",
        startPoint: point,
        startPoints: points.map(([x, y]) => [x, y]),
      };
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
      onCanvasClick(normalizePoint(clampPoint(point, frameWidth, frameHeight), frameWidth, frameHeight));
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

