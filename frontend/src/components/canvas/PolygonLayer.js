export function drawPolygon(ctx, points, options = {}) {
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
    const hovered = options.isEditableTarget && index === options.hoverVertexIndex;
    const selected = options.isEditableTarget && index === options.selectedVertexIndex;
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
}
