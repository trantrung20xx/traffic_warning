from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import desc, func, or_, select

from app.core.config import AnalyticsChartConfig
from app.core.evidence_images import build_evidence_image_url
from app.core.timezone import ensure_utc_datetime, to_vietnam_datetime, to_vietnam_isoformat
from app.db.models import Violation
from app.schemas.events import ViolationEvent


def insert_violation(session, event: ViolationEvent) -> int:
    # Đổi chuỗi timestamp của event về `datetime` để lưu đúng kiểu trong DB.
    ts = ensure_utc_datetime(datetime.fromisoformat(event.timestamp))

    row = Violation(
        camera_id=event.camera_id,
        road_name=event.location.road_name,
        intersection=event.location.intersection,
        gps_lat=event.location.gps_lat,
        gps_lng=event.location.gps_lng,
        vehicle_id=event.vehicle_id,
        vehicle_type=event.vehicle_type,
        lane_id=event.lane_id,
        violation=event.violation,
        evidence_image_path=event.image_path,
        license_plate=event.license_plate,
        license_plate_status=event.license_plate_status,
        license_plate_confidence=event.license_plate_confidence,
        license_plate_image_path=event.license_plate_image_path,
        track_session_id=event.track_session_id,
        timestamp_utc=ts,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return int(row.id)


def update_pending_violation_plate(
    session,
    *,
    camera_id: str,
    track_session_id: str,
    vehicle_id: int,
    license_plate: str,
    license_plate_status: str,
    license_plate_confidence: float,
    license_plate_image_path: str | None,
    min_confidence: float,
    allowed_current_statuses: tuple[str, ...] = ("pending", "unreadable"),
    violation_not_before_ts: datetime | None = None,
) -> int:
    normalized_status = str(license_plate_status or "").strip().lower()
    if normalized_status != "confirmed":
        return 0

    normalized_plate = str(license_plate or "").strip().upper()
    if not normalized_plate:
        return 0

    normalized_confidence = float(license_plate_confidence)
    if normalized_confidence < float(min_confidence):
        return 0

    normalized_allowed_statuses = tuple(
        status.strip().lower()
        for status in allowed_current_statuses
        if str(status or "").strip()
    )
    if not normalized_allowed_statuses:
        normalized_allowed_statuses = ("pending", "unreadable")
    normalized_image_path = str(license_plate_image_path or "").strip() or None

    query = (
        select(Violation)
        .where(Violation.camera_id == str(camera_id))
        .where(Violation.track_session_id == str(track_session_id))
        .where(Violation.vehicle_id == int(vehicle_id))
    )
    if violation_not_before_ts is not None:
        query = query.where(Violation.timestamp_utc >= ensure_utc_datetime(violation_not_before_ts))

    rows = session.execute(query).scalars().all()
    updated_rows = 0
    for row in rows:
        current_status = str(row.license_plate_status or "").strip().lower()
        current_plate = str(row.license_plate or "").strip().upper()

        is_pending_like = (
            not current_status
            or current_status in normalized_allowed_statuses
        )
        is_confirmed_backfillable = (
            current_status == "confirmed"
            and ((not current_plate) or current_plate == normalized_plate)
        )
        if not (is_pending_like or is_confirmed_backfillable):
            continue

        if current_status == "confirmed" and current_plate and current_plate != normalized_plate:
            continue

        changed = False
        if (not current_plate) or is_pending_like:
            if current_plate != normalized_plate:
                row.license_plate = normalized_plate
                changed = True
        if current_status != "confirmed":
            row.license_plate_status = "confirmed"
            changed = True
        if (
            row.license_plate_confidence is None
            or float(row.license_plate_confidence) < normalized_confidence
        ):
            row.license_plate_confidence = normalized_confidence
            changed = True
        if normalized_image_path and not str(row.license_plate_image_path or "").strip():
            row.license_plate_image_path = normalized_image_path
            changed = True

        if changed:
            updated_rows += 1

    if updated_rows > 0:
        session.commit()
    return updated_rows


def _violation_row_to_payload(row: Violation) -> dict:
    return {
        "id": row.id,
        "camera_id": row.camera_id,
        "location": {
            "road_name": row.road_name,
            "intersection": row.intersection,
            "gps_lat": row.gps_lat,
            "gps_lng": row.gps_lng,
        },
        "vehicle_id": row.vehicle_id,
        "vehicle_type": row.vehicle_type,
        "lane_id": row.lane_id,
        "violation": row.violation,
        "image_path": row.evidence_image_path,
        "image_url": build_evidence_image_url(row.evidence_image_path),
        "license_plate": row.license_plate,
        "license_plate_status": row.license_plate_status,
        "license_plate_confidence": row.license_plate_confidence,
        "license_plate_image_path": row.license_plate_image_path,
        "license_plate_image_url": build_evidence_image_url(row.license_plate_image_path),
        "track_session_id": row.track_session_id,
        "timestamp": to_vietnam_isoformat(row.timestamp_utc),
    }


def query_violation_counts(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
):
    # Aggregate nhanh theo camera/vị trí/loại xe/loại vi phạm cho endpoint stats.
    q = select(
        Violation.camera_id,
        Violation.road_name,
        Violation.intersection,
        Violation.vehicle_type,
        Violation.violation,
        func.count(Violation.id).label("count"),
    ).group_by(
        Violation.camera_id,
        Violation.road_name,
        Violation.intersection,
        Violation.vehicle_type,
        Violation.violation,
    )

    parsed_from = _parse_ts(from_ts)
    parsed_to = _parse_ts(to_ts)
    if parsed_from:
        # Lọc cận dưới thời gian theo UTC đã chuẩn hóa.
        q = q.where(Violation.timestamp_utc >= parsed_from)
    if parsed_to:
        # Lọc cận trên thời gian theo UTC đã chuẩn hóa.
        q = q.where(Violation.timestamp_utc <= parsed_to)

    rows = session.execute(q).all()
    return [
        {
            "camera_id": r.camera_id,
            "road_name": r.road_name,
            "intersection": r.intersection,
            "vehicle_type": r.vehicle_type,
            "violation": r.violation,
            "count": int(r.count),
        }
        for r in rows
    ]


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    # Mọi timestamp nhập vào đều đổi về UTC-aware datetime trước khi query.
    return ensure_utc_datetime(datetime.fromisoformat(value))


def _base_violation_query(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
    license_plate: Optional[str] = None,
):
    # Query nền dùng chung cho history và analytics, đảm bảo điều kiện lọc nhất quán.
    q = select(Violation)
    parsed_from = _parse_ts(from_ts)
    parsed_to = _parse_ts(to_ts)
    if parsed_from:
        q = q.where(Violation.timestamp_utc >= parsed_from)
    if parsed_to:
        q = q.where(Violation.timestamp_utc <= parsed_to)
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    if license_plate:
        normalized = str(license_plate).strip().lower()
        # So khớp không phân biệt hoa/thường để tìm biển số linh hoạt.
        q = q.where(func.lower(Violation.license_plate).like(f"%{normalized}%"))
    return q


def _determine_time_series_granularity(
    *,
    from_ts: Optional[str],
    to_ts: Optional[str],
    row_count: int,
    chart_config: AnalyticsChartConfig,
) -> str:
    parsed_from = _parse_ts(from_ts)
    parsed_to = _parse_ts(to_ts)

    if parsed_from and parsed_to and parsed_to > parsed_from:
        # Ưu tiên chọn granularity theo khoảng thời gian truy vấn khi có đủ from/to.
        duration = parsed_to - parsed_from
        if duration <= timedelta(hours=chart_config.minute_granularity_max_range_hours):
            return "minute"
        if duration <= timedelta(days=chart_config.hour_granularity_max_range_days):
            return "hour"
        if duration <= timedelta(days=chart_config.day_granularity_max_range_days):
            return "day"
        if duration <= timedelta(days=chart_config.week_granularity_max_range_days):
            return "week"
        return "month"

    if row_count <= chart_config.minute_granularity_max_range_hours * 60:
        # Fallback theo số lượng điểm khi thiếu khoảng thời gian rõ ràng.
        return "minute"
    if row_count <= chart_config.hour_granularity_max_range_days * 24:
        return "hour"
    if row_count <= chart_config.day_granularity_max_range_days:
        return "day"
    if row_count <= chart_config.week_granularity_max_range_days:
        return "week"
    return "month"


def _floor_bucket_in_vietnam(value: datetime, granularity: str) -> datetime:
    # Gom bucket theo giờ Việt Nam để biểu đồ nghiệp vụ khớp múi giờ người vận hành.
    local_dt = to_vietnam_datetime(value).replace(microsecond=0)

    if granularity == "minute":
        return local_dt.replace(second=0)
    if granularity == "hour":
        return local_dt.replace(minute=0, second=0)
    if granularity == "day":
        return local_dt.replace(hour=0, minute=0, second=0)
    if granularity == "week":
        local_dt = local_dt.replace(hour=0, minute=0, second=0)
        weekday = local_dt.isoweekday()
        return local_dt - timedelta(days=weekday - 1)
    return local_dt.replace(day=1, hour=0, minute=0, second=0)


def _advance_bucket_in_vietnam(value: datetime, granularity: str) -> datetime:
    if granularity == "minute":
        return value + timedelta(minutes=1)
    if granularity == "hour":
        return value + timedelta(hours=1)
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(days=7)
    if value.month == 12:
        # Chuyển tháng qua năm mới.
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _new_time_series_entry(bucket_dt: datetime, next_bucket_dt: datetime) -> dict:
    # Entry trung gian dùng defaultdict để cộng dồn breakdown thuận tiện.
    return {
        "bucket": bucket_dt.isoformat(),
        "bucket_end": next_bucket_dt.isoformat(),
        "total": 0,
        "camera_breakdown": defaultdict(int),
        "vehicle_breakdown": defaultdict(int),
        "violation_breakdown": defaultdict(int),
    }


def _build_time_series(
    rows,
    *,
    granularity: str,
    from_ts: Optional[str],
    to_ts: Optional[str],
    fill_missing: bool,
):
    # Xây time-series trong bộ nhớ từ raw rows; tránh phụ thuộc hàm bucket theo DB engine.
    bucket_map: dict[str, dict] = {}

    for row in rows:
        bucket_dt = _floor_bucket_in_vietnam(row.timestamp_utc, granularity)
        bucket_key = bucket_dt.isoformat()
        entry = bucket_map.setdefault(
            bucket_key,
            _new_time_series_entry(bucket_dt, _advance_bucket_in_vietnam(bucket_dt, granularity)),
        )
        # Cộng dồn one-pass cho tổng và từng breakdown.
        entry["total"] += 1
        entry["camera_breakdown"][row.camera_id] += 1
        entry["vehicle_breakdown"][row.vehicle_type] += 1
        entry["violation_breakdown"][row.violation] += 1

    if not fill_missing or not rows:
        return [
            {
                **entry,
                "camera_breakdown": dict(entry["camera_breakdown"]),
                "vehicle_breakdown": dict(entry["vehicle_breakdown"]),
                "violation_breakdown": dict(entry["violation_breakdown"]),
            }
            for _, entry in sorted(bucket_map.items())
        ]

    # Xác định range để fill bucket trống theo from/to hoặc theo min-max dữ liệu thực.
    range_start = _parse_ts(from_ts) or rows[0].timestamp_utc
    range_end = _parse_ts(to_ts) or rows[-1].timestamp_utc
    if range_end < range_start:
        # Hoán đổi khi đầu vào from/to đảo ngược.
        range_start, range_end = range_end, range_start

    current_bucket = _floor_bucket_in_vietnam(range_start, granularity)
    # end_exclusive là bucket ngay sau bucket chứa range_end.
    end_exclusive = _advance_bucket_in_vietnam(_floor_bucket_in_vietnam(range_end, granularity), granularity)

    series = []
    while current_bucket < end_exclusive:
        # Fill bucket rỗng để biểu đồ timeline không bị đứt đoạn.
        bucket_key = current_bucket.isoformat()
        entry = bucket_map.get(bucket_key) or _new_time_series_entry(
            current_bucket,
            _advance_bucket_in_vietnam(current_bucket, granularity),
        )
        series.append(
            {
                **entry,
                "camera_breakdown": dict(entry["camera_breakdown"]),
                "vehicle_breakdown": dict(entry["vehicle_breakdown"]),
                "violation_breakdown": dict(entry["violation_breakdown"]),
            }
        )
        current_bucket = _advance_bucket_in_vietnam(current_bucket, granularity)

    return series


def query_violation_history(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
    license_plate: Optional[str] = None,
    limit: Optional[int] = None,
):
    q = _base_violation_query(
        session,
        from_ts=from_ts,
        to_ts=to_ts,
        camera_id=camera_id,
        license_plate=license_plate,
    ).order_by(
        desc(Violation.timestamp_utc),
        desc(Violation.id),
    )
    if limit is not None:
        # Limit ở mức query để tránh kéo toàn bộ lịch sử lên bộ nhớ.
        q = q.limit(int(limit))
    rows = session.execute(q).scalars().all()
    return [_violation_row_to_payload(row) for row in rows]


def query_violation_detail_by_id(session, *, violation_id: int) -> dict | None:
    row = session.get(Violation, int(violation_id))
    if row is None:
        return None
    return _violation_row_to_payload(row)


def query_dashboard_analytics(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
    chart_config: Optional[AnalyticsChartConfig] = None,
):
    """Tổng hợp dữ liệu dashboard theo camera, tuyến đường và mốc giờ."""
    chart_config = chart_config or AnalyticsChartConfig()
    rows = session.execute(
        _base_violation_query(session, from_ts=from_ts, to_ts=to_ts, camera_id=camera_id).order_by(Violation.timestamp_utc)
    ).scalars().all()
    time_series_granularity = _determine_time_series_granularity(
        from_ts=from_ts,
        to_ts=to_ts,
        row_count=len(rows),
        chart_config=chart_config,
    )

    vehicle_type_totals: dict[str, int] = defaultdict(int)
    violation_totals: dict[str, int] = defaultdict(int)
    camera_summary_map: dict[str, dict] = {}
    road_summary_map: dict[str, dict] = {}

    for row in rows:
        # Một pass duy nhất để build đồng thời các breakdown cho dashboard.
        vehicle_type_totals[row.vehicle_type] += 1
        violation_totals[row.violation] += 1

        camera_entry = camera_summary_map.setdefault(
            row.camera_id,
            {
                "camera_id": row.camera_id,
                "road_name": row.road_name,
                "intersection": row.intersection,
                "total_violations": 0,
                "vehicle_type_totals": defaultdict(int),
                "violation_totals": defaultdict(int),
            },
        )
        camera_entry["total_violations"] += 1
        camera_entry["vehicle_type_totals"][row.vehicle_type] += 1
        camera_entry["violation_totals"][row.violation] += 1

        road_key = f"{row.road_name}::{row.intersection or ''}"
        road_entry = road_summary_map.setdefault(
            road_key,
            {
                "road_name": row.road_name,
                "intersection": row.intersection,
                "total_violations": 0,
                "camera_ids": set(),
                "vehicle_type_totals": defaultdict(int),
                "violation_totals": defaultdict(int),
            },
        )
        road_entry["total_violations"] += 1
        road_entry["camera_ids"].add(row.camera_id)
        road_entry["vehicle_type_totals"][row.vehicle_type] += 1
        road_entry["violation_totals"][row.violation] += 1

    def _normalize_summary(entry: dict) -> dict:
        # Chuyển defaultdict -> dict thuần để serialize JSON ổn định.
        return {
            **entry,
            "vehicle_type_totals": dict(entry["vehicle_type_totals"]),
            "violation_totals": dict(entry["violation_totals"]),
        }

    camera_summary = [_normalize_summary(v) for v in camera_summary_map.values()]
    camera_summary.sort(key=lambda item: (-item["total_violations"], item["camera_id"]))

    road_summary = []
    for entry in road_summary_map.values():
        road_summary.append(
            {
                "road_name": entry["road_name"],
                "intersection": entry["intersection"],
                "total_violations": entry["total_violations"],
                "camera_count": len(entry["camera_ids"]),
                "vehicle_type_totals": dict(entry["vehicle_type_totals"]),
                "violation_totals": dict(entry["violation_totals"]),
            }
        )
    road_summary.sort(key=lambda item: (-item["total_violations"], item["road_name"], item["intersection"] or ""))

    hourly_series = _build_time_series(
        rows,
        granularity="hour",
        from_ts=from_ts,
        to_ts=to_ts,
        fill_missing=False,
    )
    time_series = _build_time_series(
        rows,
        granularity=time_series_granularity,
        from_ts=from_ts,
        to_ts=to_ts,
        fill_missing=True,
    )

    return {
        "overview": {
            "total_violations": len(rows),
            "total_cameras": len(camera_summary_map),
            "vehicle_type_totals": dict(vehicle_type_totals),
            "violation_totals": dict(violation_totals),
        },
        "camera_summary": camera_summary,
        "road_summary": road_summary,
        "time_series_granularity": time_series_granularity,
        "time_series": time_series,
        "hourly_series": hourly_series,
    }

