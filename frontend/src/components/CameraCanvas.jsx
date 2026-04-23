import React, { useEffect, useMemo, useRef, useState } from "react";
import { drawBackgroundImage, useBackgroundImage } from "./canvas/BackgroundImageLayer";
import { drawPolygon } from "./canvas/PolygonLayer";
import {
  denormalizeLane,
  denormalizePoints,
  getEditTargetLabel,
  getManeuverLabel,
  getTargetPoints,
  getVehicleTypeLabel,
  isGlobalEditTarget,
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

function denormalizeGeometryCollection(collection, frameWidth, frameHeight) {
  return Object.fromEntries(
    Object.entries(collection || {}).map(([maneuver, points]) => [
      maneuver,
      denormalizePoints(points || [], frameWidth, frameHeight),
    ]),
  );
}

export default function CameraCanvas({
  frameWidth,
  frameHeight,
  lanes,
  turnCorridors = {},
  exitZones = {},
  exitLines = {},
  vehicles,
  processingFps = null,
  overlay = false,
  selectedLaneId = null,
  selectedVertexIndex = null,
  editable = false,
  onCanvasClick = null,
  onPolygonReplace = null,
  onVertexSelect = null,
  editTarget = "lane_polygon",
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

  const renderedTurnCorridors = useMemo(
    () => denormalizeGeometryCollection(turnCorridors, frameWidth, frameHeight),
    [frameHeight, frameWidth, turnCorridors],
  );

  const renderedExitZones = useMemo(
    () => denormalizeGeometryCollection(exitZones, frameWidth, frameHeight),
    [exitZones, frameHeight, frameWidth],
  );

  const renderedExitLines = useMemo(
    () => denormalizeGeometryCollection(exitLines, frameWidth, frameHeight),
    [exitLines, frameHeight, frameWidth],
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
      laneConfig: {
        turn_corridors: renderedTurnCorridors,
        exit_zones: renderedExitZones,
        exit_lines: renderedExitLines,
      },
      editTarget,
    });
  }, [editTarget, renderedExitLines, renderedExitZones, renderedSelectedLane, renderedTurnCorridors]);

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
    if (!editable || (!selectedLane && !isGlobalEditTarget(editTarget))) {
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
    });

    Object.entries(renderedTurnCorridors).forEach(([maneuver, region]) => {
      if (region.length < 2) return;
      const isEditableRegion = editable && editTarget === `turn_corridor:${maneuver}`;
      drawPolygon(ctx, region, {
        dashed: true,
        strokeStyle: isEditableRegion ? "rgba(255, 209, 102, 1)" : "rgba(52, 152, 219, 0.95)",
        lineWidth: isEditableRegion ? 3 : 2,
        isEditableTarget: isEditableRegion,
        hoverVertexIndex,
        selectedVertexIndex,
      });
      const anchor = region[0];
      if (anchor) {
        ctx.fillStyle = "rgba(255,255,255,0.88)";
        ctx.font = "12px sans-serif";
        ctx.fillText(`quỹ đạo ${getManeuverLabel(maneuver)}`, anchor[0] + 6, anchor[1] - 6);
      }
    });

    Object.entries(renderedExitZones).forEach(([maneuver, region]) => {
      if (region.length < 2) return;
      const isEditableRegion = editable && editTarget === `exit_zone:${maneuver}`;
      drawPolygon(ctx, region, {
        dashed: true,
        strokeStyle: isEditableRegion ? "rgba(255, 209, 102, 1)" : "rgba(155, 89, 182, 0.95)",
        lineWidth: isEditableRegion ? 3 : 2,
        isEditableTarget: isEditableRegion,
        hoverVertexIndex,
        selectedVertexIndex,
      });
      const anchor = region[0];
      if (anchor) {
        ctx.fillStyle = "rgba(255,255,255,0.88)";
        ctx.font = "12px sans-serif";
        ctx.fillText(`xác nhận lỗi ${getManeuverLabel(maneuver)}`, anchor[0] + 6, anchor[1] - 6);
      }
    });

    Object.entries(renderedExitLines).forEach(([maneuver, line]) => {
      if (line.length < 2) return;
      const isEditableLine = editable && editTarget === `exit_line:${maneuver}`;
      drawPolygon(ctx, line, {
        dashed: false,
        strokeStyle: isEditableLine ? "rgba(255, 209, 102, 1)" : "rgba(230, 126, 34, 0.95)",
        lineWidth: isEditableLine ? 3 : 2,
        isEditableTarget: isEditableLine,
        hoverVertexIndex,
        selectedVertexIndex,
      });
      const anchor = line[0];
      if (anchor) {
        ctx.fillStyle = "rgba(255,255,255,0.88)";
        ctx.font = "12px sans-serif";
        ctx.fillText(`đường xác nhận lỗi ${getManeuverLabel(maneuver)}`, anchor[0] + 6, anchor[1] - 6);
      }
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
        `${getEditTargetLabel(editTarget, parsedEditTarget.scope === "lane" ? selectedLaneId : null)}: keo diem de chinh, keo trong vung de di chuyen, click canh de chen diem.`,
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
    renderedLanes,
    renderedExitLines,
    renderedTurnCorridors,
    renderedExitZones,
    selectedVertexIndex,
    selectedLaneId,
    vehicles,
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

