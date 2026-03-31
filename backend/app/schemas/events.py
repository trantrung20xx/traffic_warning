from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PolygonPoint(BaseModel):
    x: float
    y: float


class BBox(BaseModel):
    # Pixel coordinates in the camera frame coordinate system
    x1: float
    y1: float
    x2: float
    y2: float


class TrackVehicle(BaseModel):
    vehicle_id: int
    vehicle_type: str
    lane_id: Optional[int] = None
    raw_lane_id: Optional[int] = None
    bbox: BBox


class TrackMessage(BaseModel):
    type: Literal["track"] = "track"
    camera_id: str
    timestamp: datetime
    vehicles: list[TrackVehicle]


class ViolationLocation(BaseModel):
    road_name: str
    intersection: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class ViolationEvent(BaseModel):
    # Must match the JSON schema you provided (field names)
    camera_id: str
    location: ViolationLocation
    vehicle_id: int
    vehicle_type: str
    lane_id: int
    violation: str
    timestamp: str = Field(
        description="ISO-8601 timestamp string"
    )

    @staticmethod
    def from_parts(
        *,
        camera_id: str,
        location: ViolationLocation,
        vehicle_id: int,
        vehicle_type: str,
        lane_id: int,
        violation: str,
        ts: Optional[datetime] = None,
    ) -> "ViolationEvent":
        if ts is None:
            ts = datetime.now(timezone.utc)
        # Ensure same field types as required by your schema
        return ViolationEvent(
            camera_id=camera_id,
            location=location,
            vehicle_id=vehicle_id,
            vehicle_type=vehicle_type,
            lane_id=lane_id,
            violation=violation,
            timestamp=ts.replace(microsecond=0).isoformat(),
        )


class ViolationMessage(BaseModel):
    type: Literal["violation"] = "violation"
    event: ViolationEvent

