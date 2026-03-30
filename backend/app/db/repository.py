from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, func, select

from app.db.models import Violation
from app.schemas.events import ViolationEvent


def insert_violation(session, event: ViolationEvent) -> None:
    # Parse timestamp string back to datetime for DB storage
    ts = datetime.fromisoformat(event.timestamp)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

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
        timestamp_utc=ts,
    )
    session.add(row)
    session.commit()


def query_violation_counts(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
):
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

    if from_ts:
        f = datetime.fromisoformat(from_ts)
        if f.tzinfo is None:
            f = f.replace(tzinfo=timezone.utc)
        q = q.where(Violation.timestamp_utc >= f)
    if to_ts:
        t = datetime.fromisoformat(to_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        q = q.where(Violation.timestamp_utc <= t)

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
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _base_violation_query(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
):
    q = select(Violation)
    parsed_from = _parse_ts(from_ts)
    parsed_to = _parse_ts(to_ts)
    if parsed_from:
        q = q.where(Violation.timestamp_utc >= parsed_from)
    if parsed_to:
        q = q.where(Violation.timestamp_utc <= parsed_to)
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    return q


def query_violation_history(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
    limit: int = 200,
):
    q = _base_violation_query(session, from_ts=from_ts, to_ts=to_ts, camera_id=camera_id).order_by(
        desc(Violation.timestamp_utc),
        desc(Violation.id),
    )
    rows = session.execute(q.limit(int(limit))).scalars().all()
    return [
        {
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
            "timestamp": row.timestamp_utc.isoformat(),
        }
        for row in rows
    ]


def query_dashboard_analytics(
    session,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    camera_id: Optional[str] = None,
):
    rows = session.execute(
        _base_violation_query(session, from_ts=from_ts, to_ts=to_ts, camera_id=camera_id).order_by(Violation.timestamp_utc)
    ).scalars().all()

    vehicle_type_totals: dict[str, int] = defaultdict(int)
    violation_totals: dict[str, int] = defaultdict(int)
    camera_summary_map: dict[str, dict] = {}
    road_summary_map: dict[str, dict] = {}
    hourly_map: dict[str, dict] = {}

    for row in rows:
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

        bucket = row.timestamp_utc.replace(minute=0, second=0, microsecond=0).isoformat()
        hourly_entry = hourly_map.setdefault(
            bucket,
            {
                "bucket": bucket,
                "total": 0,
                "camera_breakdown": defaultdict(int),
                "vehicle_breakdown": defaultdict(int),
                "violation_breakdown": defaultdict(int),
            },
        )
        hourly_entry["total"] += 1
        hourly_entry["camera_breakdown"][row.camera_id] += 1
        hourly_entry["vehicle_breakdown"][row.vehicle_type] += 1
        hourly_entry["violation_breakdown"][row.violation] += 1

    def _normalize_summary(entry: dict) -> dict:
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

    hourly_series = []
    for bucket in sorted(hourly_map.keys()):
        hourly_entry = hourly_map[bucket]
        hourly_series.append(
            {
                "bucket": bucket,
                "total": hourly_entry["total"],
                "camera_breakdown": dict(hourly_entry["camera_breakdown"]),
                "vehicle_breakdown": dict(hourly_entry["vehicle_breakdown"]),
                "violation_breakdown": dict(hourly_entry["violation_breakdown"]),
            }
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
        "hourly_series": hourly_series,
    }

