from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Column
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Violation(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, autoincrement=True)

    camera_id = Column(String, nullable=False, index=True)
    road_name = Column(String, nullable=False)
    intersection = Column(String, nullable=True)
    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)

    vehicle_id = Column(Integer, nullable=False, index=True)
    vehicle_type = Column(String, nullable=False)
    lane_id = Column(Integer, nullable=False)
    violation = Column(String, nullable=False, index=True)

    timestamp_utc = Column(DateTime(timezone=True), nullable=False, index=True)


