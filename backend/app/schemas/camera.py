from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CameraLocation(BaseModel):
    road_name: str
    intersection_name: Optional[str] = None
    # Optional GPS fields for simulation / reporting
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class CameraType(str):
    pass


class CameraConfig(BaseModel):
    camera_id: str
    rtsp_url: str
    camera_type: Literal["roadside", "overhead", "intersection"]
    location: CameraLocation
    # lanes monitored by THIS camera (must be unique per camera)
    monitored_lanes: list[int]

    # Fixed resolution for this camera (polygons are defined in this pixel coordinate system)
    frame_width: int
    frame_height: int

