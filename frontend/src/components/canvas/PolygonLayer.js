function drawVertices(ctx, points, options = {}) {
  const basePointColor = options.pointColor || options.strokeStyle || ctx.strokeStyle;
  points.forEach(([x, y], index) => {
    const hovered = options.isEditableTarget && index === options.hoverVertexIndex;
    const selected = options.isEditableTarget && index === options.selectedVertexIndex;
    ctx.beginPath();
    ctx.arc(x, y, hovered || selected ? 7 : 5, 0, Math.PI * 2);
    ctx.fillStyle = selected
      ? "rgba(0, 214, 143, 0.96)"
      : hovered
        ? "rgba(255, 209, 102, 0.96)"
        : basePointColor;
    ctx.fill();
    if (options.isEditableTarget) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(7, 19, 31, 0.9)";
      ctx.stroke();
    }
  });
}

function drawDirectionArrow(ctx, points, options = {}) {
  if (!options.showDirection || !points || points.length < 2) return;

  const end = points[points.length - 1];
  let start = points[points.length - 2];
  for (let index = points.length - 2; index >= 0; index -= 1) {
    const candidate = points[index];
    if (Math.hypot(end[0] - candidate[0], end[1] - candidate[1]) > 1) {
      start = candidate;
      break;
    }
  }

  const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
  if (!Number.isFinite(angle)) return;

  const size = options.arrowSize || 13;
  const spread = Math.PI / 6;
  ctx.beginPath();
  ctx.moveTo(end[0], end[1]);
  ctx.lineTo(end[0] - size * Math.cos(angle - spread), end[1] - size * Math.sin(angle - spread));
  ctx.lineTo(end[0] - size * Math.cos(angle + spread), end[1] - size * Math.sin(angle + spread));
  ctx.closePath();
  ctx.fillStyle = options.arrowColor || options.strokeStyle || "rgba(255,255,255,0.9)";
  ctx.fill();
}

export function drawPolyline(ctx, points, options = {}) {
  if (!points || points.length < 2) return;
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) {
    ctx.lineTo(points[i][0], points[i][1]);
  }
  ctx.strokeStyle = options.strokeStyle || "rgba(255,255,255,0.9)";
  ctx.lineWidth = options.lineWidth || 2;
  if (options.dashed) ctx.setLineDash([8, 6]);
  ctx.lineCap = options.lineCap || "round";
  ctx.lineJoin = options.lineJoin || "round";
  ctx.stroke();
  ctx.setLineDash([]);
  drawDirectionArrow(ctx, points, options);
  drawVertices(ctx, points, options);
  ctx.restore();
}

export function drawCorridorPreview(ctx, points, widthPx, options = {}) {
  if (!points || points.length < 2) return;
  const lineWidth = Math.max(Number(widthPx) || 1, 1);
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) {
    ctx.lineTo(points[i][0], points[i][1]);
  }
  ctx.strokeStyle = options.strokeStyle || "rgba(52, 152, 219, 0.18)";
  ctx.lineWidth = lineWidth;
  ctx.lineCap = "butt";
  ctx.lineJoin = "miter";
  ctx.miterLimit = options.miterLimit || 10;
  ctx.stroke();
  ctx.restore();
}

export function drawPolygon(ctx, points, options = {}) {
  if (!points || points.length < 2) return;
  ctx.save();
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
  drawVertices(ctx, points, options);
  ctx.restore();
}
