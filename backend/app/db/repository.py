from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

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

