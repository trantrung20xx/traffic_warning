from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ViolationCountRow(BaseModel):
    camera_id: Optional[str] = None
    road_name: Optional[str] = None
    intersection: Optional[str] = None
    vehicle_type: str
    violation: str
    count: int


class StatsResponse(BaseModel):
    from_timestamp: Optional[str] = None
    to_timestamp: Optional[str] = None
    rows: list[ViolationCountRow]

