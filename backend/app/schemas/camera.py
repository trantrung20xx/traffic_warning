from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CameraLocation(BaseModel):
    road_name: str
    intersection_name: Optional[str] = None
    # Tọa độ GPS là tùy chọn, chủ yếu phục vụ mô phỏng và báo cáo.
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class CameraConfig(BaseModel):
    camera_id: str
    rtsp_url: str
    camera_type: Literal["roadside", "overhead", "intersection"]
    view_direction: Optional[str] = None
    location: CameraLocation
    # Danh sách làn do camera này giám sát; không được trùng giữa các camera.
    monitored_lanes: list[int]

    # Độ phân giải chuẩn của camera. Polygon được lưu dạng chuẩn hóa và khi chạy sẽ
    # đổi lại theo đúng hệ pixel của kích thước này.
    frame_width: int
    frame_height: int

