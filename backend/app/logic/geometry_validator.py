from __future__ import annotations

from itertools import combinations
from math import hypot
from typing import Any, Literal, Optional

from shapely.geometry import LineString, Polygon

from app.core.config import CameraLaneConfig


ValidationLevel = Literal["error", "warning", "info"]


def validate_lane_geometry(lane_config: CameraLaneConfig) -> list[dict[str, Any]]:
    """
    Semantic validator cho cấu hình lane/maneuver.

    Mục tiêu:
    - Giữ frontend đơn giản (không thêm tham số kỹ thuật).
    - Bắt sớm cấu hình dễ gây false positive/false negative.
    """
    issues: list[dict[str, Any]] = []

    lane_shapes: dict[int, Polygon] = {}
    lane_direction_vectors: dict[int, Optional[tuple[float, float]]] = {}
    for lane in lane_config.lanes:
        lane_shape = Polygon([(float(x), float(y)) for x, y in lane.polygon])
        lane_shapes[lane.lane_id] = lane_shape
        lane_direction_vectors[lane.lane_id] = _lane_direction_vector_for_validation(lane)

        if lane_shape.is_empty or lane_shape.area <= 1e-9:
            issues.append(
                _issue(
                    level="error",
                    code="LANE_POLYGON_INVALID",
                    message=f"Làn {lane.lane_id}: lane polygon rỗng hoặc diện tích quá nhỏ.",
                    lane_id=lane.lane_id,
                    suggestion="Vẽ lại biên làn với ít nhất 3 điểm tạo thành vùng có diện tích rõ ràng.",
                )
            )
            continue
        if not lane_shape.is_valid:
            issues.append(
                _issue(
                    level="warning",
                    code="LANE_POLYGON_SELF_INTERSECT",
                    message=f"Làn {lane.lane_id}: lane polygon tự cắt nhau.",
                    lane_id=lane.lane_id,
                    suggestion="Chỉnh lại thứ tự điểm để polygon không tự cắt.",
                )
            )

    # Lane-level geometry checks.
    for lane in lane_config.lanes:
        lane_shape = lane_shapes[lane.lane_id]
        if lane_shape.is_empty:
            continue

        if lane.commit_line:
            commit_line = LineString([(float(x), float(y)) for x, y in lane.commit_line])
            if commit_line.is_empty or commit_line.length <= 1e-9:
                issues.append(
                    _issue(
                        level="error",
                        code="COMMIT_LINE_INVALID",
                        message=f"Làn {lane.lane_id}: commit line không hợp lệ.",
                        lane_id=lane.lane_id,
                        suggestion="Vẽ commit line bằng đúng 2 điểm cách nhau rõ ràng.",
                    )
                )
            elif not commit_line.intersects(lane_shape):
                issues.append(
                    _issue(
                        level="error",
                        code="COMMIT_LINE_OUTSIDE_LANE",
                        message=f"Làn {lane.lane_id}: commit line không cắt lane polygon.",
                        lane_id=lane.lane_id,
                        suggestion="Đặt commit line cắt qua lane ở khu vực xe bắt đầu quyết định hướng.",
                    )
                )

        if lane.approach_zone:
            approach_shape = Polygon([(float(x), float(y)) for x, y in lane.approach_zone])
            if approach_shape.is_empty or approach_shape.area <= 1e-9:
                issues.append(
                    _issue(
                        level="warning",
                        code="APPROACH_ZONE_INVALID",
                        message=f"Làn {lane.lane_id}: approach zone rỗng hoặc quá nhỏ.",
                        lane_id=lane.lane_id,
                        suggestion="Mở rộng approach zone ở trước vị trí commit.",
                    )
                )
            else:
                overlap_ratio = _safe_overlap_ratio(approach_shape, lane_shape)
                if overlap_ratio < 0.12:
                    issues.append(
                        _issue(
                            level="warning",
                            code="APPROACH_ZONE_MISALIGNED",
                            message=f"Làn {lane.lane_id}: approach zone lệch lane (overlap thấp).",
                            lane_id=lane.lane_id,
                            suggestion="Đưa approach zone phủ lên lane trước commit để khóa lane nguồn ổn định.",
                        )
                    )

        if lane.commit_gate:
            gate_shape = Polygon([(float(x), float(y)) for x, y in lane.commit_gate])
            if gate_shape.is_empty or gate_shape.area <= 1e-9:
                issues.append(
                    _issue(
                        level="warning",
                        code="COMMIT_GATE_INVALID",
                        message=f"Làn {lane.lane_id}: commit gate rỗng hoặc quá nhỏ.",
                        lane_id=lane.lane_id,
                        suggestion="Vẽ lại commit gate đủ rộng để xe đi qua ổn định.",
                    )
                )
            else:
                overlap_ratio = _safe_overlap_ratio(gate_shape, lane_shape)
                if overlap_ratio < 0.10:
                    issues.append(
                        _issue(
                            level="warning",
                            code="COMMIT_GATE_MISALIGNED",
                            message=f"Làn {lane.lane_id}: commit gate nằm lệch lane.",
                            lane_id=lane.lane_id,
                            suggestion="Đặt commit gate bám lane gần điểm bắt đầu bẻ lái.",
                        )
                    )

        direction_rule = getattr(lane, "direction_rule", None)
        if direction_rule and bool(getattr(direction_rule, "enabled", False)):
            direction_path = list(getattr(direction_rule, "direction_path", None) or [])
            check_zone = list(getattr(direction_rule, "check_zone", None) or [])
            direction_path_vec: Optional[tuple[float, float]] = None

            if len(direction_path) < 2:
                issues.append(
                    _issue(
                        level="warning",
                        code="DIRECTION_PATH_MISSING",
                        message=f"Làn {lane.lane_id}: direction path chưa có hoặc chưa đủ 2 điểm.",
                        lane_id=lane.lane_id,
                        suggestion="Vẽ direction path bằng polyline 2+ điểm theo hướng xe đi hợp lệ.",
                    )
                )
            else:
                direction_path_length = _polyline_length(direction_path)
                if direction_path_length < 0.05:
                    issues.append(
                        _issue(
                            level="warning",
                            code="DIRECTION_PATH_TOO_SHORT",
                            message=f"Làn {lane.lane_id}: direction path quá ngắn, dễ nhiễu.",
                            lane_id=lane.lane_id,
                            suggestion="Kéo dài direction path qua đoạn xe chạy ổn định.",
                        )
                    )
                direction_line = _polyline_shape(direction_path)
                if direction_line is not None and lane_shape is not None and not lane_shape.is_empty:
                    distance_to_lane = float(direction_line.distance(lane_shape))
                    overlap_ratio = _line_inside_ratio(direction_line, lane_shape)
                    if distance_to_lane > 0.08 or overlap_ratio < 0.15:
                        issues.append(
                            _issue(
                                level="warning",
                                code="DIRECTION_PATH_FAR_FROM_LANE",
                                message=f"Làn {lane.lane_id}: direction path lệch xa lane.",
                                lane_id=lane.lane_id,
                                suggestion="Vẽ direction path bám theo luồng xe trong lane để giữ vector hướng ổn định.",
                            )
                        )
                direction_path_vec = _direction_path_vector(direction_path)

            if check_zone:
                check_zone_shape = Polygon([(float(x), float(y)) for x, y in check_zone])
                if check_zone_shape.is_empty or check_zone_shape.area <= 1e-9:
                    issues.append(
                        _issue(
                            level="warning",
                            code="DIRECTION_CHECK_ZONE_INVALID",
                            message=f"Làn {lane.lane_id}: direction check zone rỗng hoặc quá nhỏ.",
                            lane_id=lane.lane_id,
                            suggestion="Vẽ vùng kiểm tra hướng tối thiểu 3 điểm.",
                        )
                    )
                else:
                    overlap_ratio = _safe_overlap_ratio(check_zone_shape, lane_shape)
                    if overlap_ratio < 0.12:
                        issues.append(
                            _issue(
                                level="warning",
                                code="DIRECTION_CHECK_ZONE_MISALIGNED",
                                message=f"Làn {lane.lane_id}: direction check zone lệch khỏi lane.",
                                lane_id=lane.lane_id,
                                suggestion="Đưa vùng kiểm tra hướng nằm gọn trong lane để giảm false positive.",
                            )
                        )

    # Lane overlap checks.
    for lane_a, lane_b in combinations(lane_config.lanes, 2):
        poly_a = lane_shapes.get(lane_a.lane_id)
        poly_b = lane_shapes.get(lane_b.lane_id)
        if poly_a is None or poly_b is None or poly_a.is_empty or poly_b.is_empty:
            continue
        overlap_ratio = _safe_overlap_ratio(poly_a, poly_b)
        if overlap_ratio >= 0.12:
            issues.append(
                _issue(
                    level="warning",
                    code="LANE_OVERLAP_DANGEROUS",
                    message=f"Làn {lane_a.lane_id} và {lane_b.lane_id} overlap lớn, dễ gây lane drift.",
                    suggestion="Giảm overlap ở mép lane hoặc làm rõ biên lane gần giao cắt.",
                )
            )

    lane_maneuver_turn_zones: dict[tuple[int, str], Polygon] = {}
    lane_maneuver_exit_zones: dict[tuple[int, str], Polygon] = {}
    lane_maneuver_exit_lines: dict[tuple[int, str], LineString] = {}

    # Lane-centric maneuver checks.
    for lane in lane_config.lanes:
        lane_shape = lane_shapes.get(lane.lane_id)
        maneuvers = lane.maneuvers or {}
        if not maneuvers:
            continue

        expected_allowed = set(lane.allowed_maneuvers or [])
        inferred_allowed = {
            maneuver
            for maneuver, cfg in maneuvers.items()
            if bool(getattr(cfg, "enabled", True)) and bool(getattr(cfg, "allowed", False))
        }
        if expected_allowed and expected_allowed != inferred_allowed:
            issues.append(
                _issue(
                    level="info",
                    code="ALLOWED_MANEUVERS_MISMATCH",
                    message=(
                        f"Làn {lane.lane_id}: allowed_maneuvers không đồng nhất với trạng thái enabled/allowed "
                        f"trong maneuvers."
                    ),
                    lane_id=lane.lane_id,
                    suggestion="Ưu tiên chỉnh allowed theo từng maneuver để tránh mâu thuẫn khi chạy runtime.",
                )
            )

        for maneuver, cfg in maneuvers.items():
            turn_zone = list(cfg.turn_zone or [])
            exit_zone = list(cfg.exit_zone or [])
            exit_line = list(cfg.exit_line or [])

            has_any_geometry = bool(turn_zone or exit_zone or exit_line)

            enabled = bool(cfg.enabled)
            allowed = bool(cfg.allowed)

            if enabled and not has_any_geometry:
                issues.append(
                    _issue(
                        level="warning",
                        code="MANEUVER_ENABLED_BUT_MISSING_GEOMETRY",
                        message=f"Làn {lane.lane_id} - {maneuver}: đã bật nhưng chưa có turn zone/exit geometry.",
                        lane_id=lane.lane_id,
                        maneuver=maneuver,
                        suggestion="Vẽ turn zone và ít nhất một exit line/exit zone.",
                    )
                )
            if not enabled and has_any_geometry:
                issues.append(
                    _issue(
                        level="info",
                        code="MANEUVER_DISABLED_WITH_GEOMETRY",
                        message=f"Làn {lane.lane_id} - {maneuver}: đang tắt nhưng vẫn có geometry.",
                        lane_id=lane.lane_id,
                        maneuver=maneuver,
                        suggestion="Bật lại maneuver hoặc xóa geometry không dùng.",
                    )
                )
            if allowed and not enabled:
                issues.append(
                    _issue(
                        level="warning",
                        code="MANEUVER_ALLOWED_BUT_DISABLED",
                        message=f"Làn {lane.lane_id} - {maneuver}: allowed=true nhưng enabled=false.",
                        lane_id=lane.lane_id,
                        maneuver=maneuver,
                        suggestion="Để enabled=true nếu muốn hệ thống xác nhận hợp lệ cho maneuver này.",
                    )
                )

            turn_zone_shape: Optional[Polygon] = None
            if turn_zone:
                turn_zone_shape = Polygon([(float(x), float(y)) for x, y in turn_zone])
                lane_maneuver_turn_zones[(lane.lane_id, maneuver)] = turn_zone_shape
                if turn_zone_shape.is_empty or turn_zone_shape.area <= 1e-9:
                    issues.append(
                        _issue(
                            level="warning",
                            code="TURN_ZONE_INVALID",
                            message=f"Làn {lane.lane_id} - {maneuver}: turn zone rỗng hoặc quá nhỏ.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Mở rộng turn zone phủ theo vùng xe ổn định khi thực hiện maneuver.",
                        )
                    )
                elif lane_shape is not None and not lane_shape.is_empty and turn_zone_shape.distance(lane_shape) > 0.15:
                    issues.append(
                        _issue(
                            level="warning",
                            code="TURN_ZONE_FAR_FROM_LANE",
                            message=f"Làn {lane.lane_id} - {maneuver}: turn zone xa lane nguồn.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Dịch turn zone gần lane nguồn và nhánh rẽ thực tế.",
                        )
                    )

            if exit_zone:
                exit_zone_shape = Polygon([(float(x), float(y)) for x, y in exit_zone])
                lane_maneuver_exit_zones[(lane.lane_id, maneuver)] = exit_zone_shape
                if exit_zone_shape.is_empty or exit_zone_shape.area <= 1e-9:
                    issues.append(
                        _issue(
                            level="warning",
                            code="EXIT_ZONE_INVALID",
                            message=f"Làn {lane.lane_id} - {maneuver}: exit zone rỗng hoặc quá nhỏ.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Mở rộng exit zone quanh vị trí xe ổn định sau khi rẽ.",
                        )
                    )
                if turn_zone_shape is not None and not turn_zone_shape.is_empty and turn_zone_shape.distance(exit_zone_shape) > 0.08:
                    issues.append(
                        _issue(
                            level="warning",
                            code="EXIT_ZONE_FAR_FROM_TURN_ZONE",
                            message=f"Làn {lane.lane_id} - {maneuver}: exit zone nằm xa turn zone.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Đặt exit zone gần turn zone để tăng độ nhất quán tín hiệu xác nhận.",
                        )
                    )

            if exit_line:
                exit_line_shape = LineString([(float(x), float(y)) for x, y in exit_line])
                lane_maneuver_exit_lines[(lane.lane_id, maneuver)] = exit_line_shape
                if exit_line_shape.is_empty or exit_line_shape.length <= 1e-9:
                    issues.append(
                        _issue(
                            level="warning",
                            code="EXIT_LINE_INVALID",
                            message=f"Làn {lane.lane_id} - {maneuver}: exit line không hợp lệ.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Vẽ exit line bằng 2 điểm nằm trên nhánh ra thực tế.",
                        )
                    )
                if turn_zone_shape is not None and not turn_zone_shape.is_empty:
                    midpoint = exit_line_shape.interpolate(0.5, normalized=True)
                    if turn_zone_shape.distance(midpoint) > 0.08:
                        issues.append(
                            _issue(
                                level="warning",
                                code="EXIT_LINE_FAR_FROM_TURN_ZONE",
                                message=f"Làn {lane.lane_id} - {maneuver}: exit line nằm xa turn zone.",
                                lane_id=lane.lane_id,
                                maneuver=maneuver,
                                suggestion="Đặt exit line gần turn zone và nhánh rời để tăng độ chắc chắn.",
                            )
                        )

            if enabled and not (exit_line or exit_zone):
                issues.append(
                    _issue(
                        level="info",
                        code="MISSING_EXIT_CONFIRM",
                        message=f"Làn {lane.lane_id} - {maneuver}: chưa có exit line/zone để xác nhận đầu ra.",
                        lane_id=lane.lane_id,
                        maneuver=maneuver,
                        suggestion="Thêm exit line hoặc exit zone để tăng độ chắc chắn khi fusion evidence.",
                    )
                )

            # U-turn sanity: cần dấu hiệu đảo hướng rõ ràng.
            if maneuver == "u_turn" and turn_zone and len(turn_zone) >= 3:
                lane_dir = lane_direction_vectors.get(lane.lane_id)
                lane_center = _polygon_centroid(lane_shape)
                turn_zone_center = _polygon_centroid(turn_zone_shape) if turn_zone_shape is not None else _centroid(turn_zone)
                zone_dir = _normalize_vector(
                    (
                        float(turn_zone_center[0]) - float(lane_center[0]),
                        float(turn_zone_center[1]) - float(lane_center[1]),
                    )
                )
                if lane_dir is not None and zone_dir is not None:
                    dot = (lane_dir[0] * zone_dir[0]) + (lane_dir[1] * zone_dir[1])
                    if dot > -0.15:
                        issues.append(
                            _issue(
                                level="warning",
                                code="UTURN_ZONE_NOT_OPPOSITE",
                                message=f"Làn {lane.lane_id}: u_turn zone chưa thể hiện rõ hướng đảo chiều.",
                                lane_id=lane.lane_id,
                                maneuver=maneuver,
                                suggestion="Dời u_turn zone về vùng thể hiện rõ quỹ đạo đảo chiều.",
                            )
                        )
                if not (exit_line or exit_zone):
                    issues.append(
                        _issue(
                            level="warning",
                            code="UTURN_MISSING_EXIT_CONFIRM",
                            message=f"Làn {lane.lane_id}: u_turn chưa có exit line/zone xác nhận.",
                            lane_id=lane.lane_id,
                            maneuver=maneuver,
                            suggestion="Thêm exit line/zone riêng cho u_turn để tránh nhầm với left/right.",
                        )
                    )

    # Turn zone overlap ambiguity checks per lane.
    turn_zones_by_lane: dict[int, dict[str, Polygon]] = {}
    for (lane_id, maneuver), shape in lane_maneuver_turn_zones.items():
        turn_zones_by_lane.setdefault(lane_id, {})[maneuver] = shape

    for lane_id, lane_turn_zones in turn_zones_by_lane.items():
        for maneuver_a, maneuver_b in combinations(sorted(lane_turn_zones.keys()), 2):
            shape_a = lane_turn_zones[maneuver_a]
            shape_b = lane_turn_zones[maneuver_b]
            if shape_a.is_empty or shape_b.is_empty:
                continue
            overlap_ratio = _safe_overlap_ratio(shape_a, shape_b)
            if overlap_ratio >= 0.35:
                issues.append(
                    _issue(
                        level="warning",
                        code="TURN_ZONE_OVERLAP_AMBIGUOUS",
                        message=f"Làn {lane_id}: '{maneuver_a}' và '{maneuver_b}' overlap turn zone mạnh, dễ ambiguity.",
                        lane_id=lane_id,
                        suggestion="Tách turn zone hoặc thêm exit line riêng cho từng maneuver.",
                    )
                )
            if "u_turn" in {maneuver_a, maneuver_b} and overlap_ratio >= 0.20:
                other = maneuver_b if maneuver_a == "u_turn" else maneuver_a
                issues.append(
                    _issue(
                        level="warning",
                            code="UTURN_OVERLAP_HIGH",
                            message=f"Làn {lane_id}: u_turn overlap cao với '{other}'.",
                            lane_id=lane_id,
                            maneuver="u_turn",
                            suggestion="Điều chỉnh u_turn turn zone tách rõ khỏi left/right.",
                        )
                    )

    level_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (level_order.get(str(item.get("level")), 99), str(item.get("code", "")), str(item.get("message", ""))))
    return issues


def _issue(
    *,
    level: ValidationLevel,
    code: str,
    message: str,
    lane_id: Optional[int] = None,
    maneuver: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "level": level,
        "code": code,
        "message": message,
    }
    if lane_id is not None:
        payload["lane_id"] = int(lane_id)
    if maneuver is not None:
        payload["maneuver"] = str(maneuver)
    if suggestion:
        payload["suggestion"] = suggestion
    return payload


def _safe_overlap_ratio(shape_a: Polygon, shape_b: Polygon) -> float:
    if shape_a.is_empty or shape_b.is_empty:
        return 0.0
    intersection_area = shape_a.intersection(shape_b).area
    baseline = min(max(shape_a.area, 1e-9), max(shape_b.area, 1e-9))
    return float(intersection_area / baseline)


def _polyline_length(points: list[list[float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for idx in range(1, len(points)):
        x1, y1 = points[idx - 1]
        x2, y2 = points[idx]
        total += hypot(float(x2) - float(x1), float(y2) - float(y1))
    return float(total)


def _centroid(points: list[list[float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    sx = sum(float(point[0]) for point in points)
    sy = sum(float(point[1]) for point in points)
    size = max(len(points), 1)
    return (sx / size, sy / size)


def _polygon_centroid(shape: Optional[Polygon]) -> tuple[float, float]:
    if shape is None or shape.is_empty:
        return (0.0, 0.0)
    center = shape.centroid
    return (float(center.x), float(center.y))


def _normalize_vector(vector: tuple[float, float]) -> Optional[tuple[float, float]]:
    vx = float(vector[0])
    vy = float(vector[1])
    mag = hypot(vx, vy)
    if mag <= 1e-9:
        return None
    return (vx / mag, vy / mag)


def _polyline_shape(points: list[list[float]]) -> Optional[LineString]:
    if len(points) < 2:
        return None
    normalized_points: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            normalized_points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    if len(normalized_points) < 2:
        return None
    shape = LineString(normalized_points)
    if shape.is_empty or shape.length <= 1e-9:
        return None
    return shape


def _line_inside_ratio(line: LineString, polygon: Polygon) -> float:
    if line.is_empty or line.length <= 1e-9 or polygon.is_empty:
        return 0.0
    inside_length = float(line.intersection(polygon).length)
    return max(min(inside_length / max(float(line.length), 1e-9), 1.0), 0.0)


def _direction_path_vector(points: list[list[float]]) -> Optional[tuple[float, float]]:
    line = _polyline_shape(points)
    if line is None:
        return None
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    start = coords[0]
    for index in range(len(coords) - 1, 0, -1):
        end = coords[index]
        vec = _normalize_vector((end[0] - start[0], end[1] - start[1]))
        if vec is not None:
            return vec
    return None


def _lane_direction_vector_for_validation(lane) -> Optional[tuple[float, float]]:
    direction_rule = getattr(lane, "direction_rule", None)
    if direction_rule is not None and bool(getattr(direction_rule, "enabled", False)):
        direction_path = list(getattr(direction_rule, "direction_path", None) or [])
        vec = _direction_path_vector(direction_path)
        if vec is not None:
            return vec

    return None
