from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

class BBox(BaseModel):
    # Tọa độ pixel trong hệ trục của khung hình camera.
    x1: float
    y1: float
    x2: float
    y2: float


class TrackVehicle(BaseModel):
    vehicle_id: int
    vehicle_type: str
    lane_id: Optional[int] = None
    raw_lane_id: Optional[int] = None
    direction_status: Optional[str] = None
    direction_dot: Optional[float] = None
    license_plate: Optional[str] = None
    license_plate_status: Optional[str] = None
    license_plate_confidence: Optional[float] = None
    bbox: BBox


class TrackMessage(BaseModel):
    type: Literal["track"] = "track"
    camera_id: str
    timestamp: datetime
    processing_fps: Optional[float] = None
    stream_fps: Optional[float] = None
    vehicles: list[TrackVehicle]


class ViolationLocation(BaseModel):
    road_name: str
    intersection: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class ViolationEvent(BaseModel):
    # Tên trường cần giữ đúng với JSON schema dùng để trao đổi dữ liệu.
    id: Optional[int] = None
    camera_id: str
    location: ViolationLocation
    vehicle_id: int
    vehicle_type: str
    lane_id: int
    violation: str
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    license_plate: Optional[str] = None
    license_plate_status: Optional[str] = None
    license_plate_confidence: Optional[float] = None
    license_plate_image_path: Optional[str] = None
    license_plate_image_url: Optional[str] = None
    track_session_id: Optional[str] = None
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
        image_path: Optional[str] = None,
        image_url: Optional[str] = None,
        license_plate: Optional[str] = None,
        license_plate_status: Optional[str] = None,
        license_plate_confidence: Optional[float] = None,
        license_plate_image_path: Optional[str] = None,
        license_plate_image_url: Optional[str] = None,
        track_session_id: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> "ViolationEvent":
        if ts is None:
            ts = datetime.now(timezone.utc)
        # Giữ đúng kiểu dữ liệu mà schema yêu cầu để frontend và DB xử lý thống nhất.
        return ViolationEvent(
            camera_id=camera_id,
            location=location,
            vehicle_id=vehicle_id,
            vehicle_type=vehicle_type,
            lane_id=lane_id,
            violation=violation,
            image_path=image_path,
            image_url=image_url,
            license_plate=license_plate,
            license_plate_status=license_plate_status,
            license_plate_confidence=license_plate_confidence,
            license_plate_image_path=license_plate_image_path,
            license_plate_image_url=license_plate_image_url,
            track_session_id=track_session_id,
            timestamp=ts.replace(microsecond=0).isoformat(),
        )

