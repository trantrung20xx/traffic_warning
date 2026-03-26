from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.config import LanePolygon
from app.logic.polygon import bbox_bottom_center, point_in_polygon


@dataclass
class VehicleState:
    vehicle_id: int
    vehicle_type: str

    primary_lane_id: Optional[int] = None
    # Wrong-lane detection uses realtime timestamp (independent of FPS)
    illegal_lane_started_ts: Optional[datetime] = None

    # Maneuver inference state
    first_maneuver: Optional[str] = None
    maneuver_hit_counts: dict[str, int] = field(default_factory=dict)

    # Keep emitted violations to avoid duplicates
    emitted: set[str] = field(default_factory=set)
    last_seen_ts: Optional[datetime] = None


class ViolationLogic:
    """
    Rule engine (NOT end-to-end AI).
    Violation decision is based on:
    - camera-specific lane polygons + turn regions
    - vehicle's track_id history (vehicle_id)
    """

    def __init__(
        self,
        lane_polygons: list[LanePolygon],
        *,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
    ):
        if not lane_polygons:
            raise ValueError("lane_polygons must be non-empty")
        self._lane_by_id = {lp.lane_id: lp for lp in lane_polygons}
        self._wrong_lane_min_duration_ms = int(wrong_lane_min_duration_ms)
        self._turn_region_min_hits = int(turn_region_min_hits)
        self._vehicle_states: dict[int, VehicleState] = {}

    def update_and_maybe_generate_violation(
        self,
        *,
        vehicle_id: int,
        vehicle_type: str,
        lane_id: Optional[int],
        bbox_xyxy: list[float] | tuple[float, ...],
        ts: datetime,
    ) -> list[dict]:
        """
        Returns a list of violation candidates:
        [
          {
            "lane_id": int,
            "violation": str,
          },
          ...
        ]
        """
        st = self._vehicle_states.get(vehicle_id)
        if st is None:
            st = VehicleState(vehicle_id=vehicle_id, vehicle_type=vehicle_type)
            self._vehicle_states[vehicle_id] = st
        st.last_seen_ts = ts

        px, py = bbox_bottom_center(bbox_xyxy)
        primary_lane_id = st.primary_lane_id

        # Assign primary lane as soon as we have a confident lane_id
        if primary_lane_id is None and lane_id is not None:
            st.primary_lane_id = lane_id
            primary_lane_id = lane_id

        if primary_lane_id is None:
            return []

        lp = self._lane_by_id.get(primary_lane_id)
        if lp is None:
            return []

        violations: list[dict] = []

        # ----------------------
        # Wrong lane
        # ----------------------
        allowed_lane_changes = lp.allowed_lane_changes
        if allowed_lane_changes is None:
            allowed_lane_changes = [primary_lane_id]

        if lane_id is not None:
            if lane_id in allowed_lane_changes:
                st.illegal_lane_started_ts = None
            else:
                if st.illegal_lane_started_ts is None:
                    st.illegal_lane_started_ts = ts
                duration_ms = int((ts - st.illegal_lane_started_ts).total_seconds() * 1000.0)
                if duration_ms >= self._wrong_lane_min_duration_ms and "wrong_lane" not in st.emitted:
                    st.emitted.add("wrong_lane")
                    violations.append({"lane_id": primary_lane_id, "violation": "wrong_lane"})

        # ----------------------
        # Wrong turn (via turn regions)
        # ----------------------
        if lp.turn_regions:
            for maneuver, poly in lp.turn_regions.items():
                if point_in_polygon(px, py, poly):
                    st.maneuver_hit_counts[maneuver] = st.maneuver_hit_counts.get(maneuver, 0) + 1
                    if (
                        st.first_maneuver is None
                        and st.maneuver_hit_counts[maneuver] >= self._turn_region_min_hits
                    ):
                        st.first_maneuver = maneuver

                        allowed_maneuvers = lp.allowed_maneuvers or []
                        if allowed_maneuvers and maneuver not in allowed_maneuvers:
                            if f"turn_{maneuver}_not_allowed" not in st.emitted:
                                st.emitted.add(f"turn_{maneuver}_not_allowed")
                                violations.append(
                                    {
                                        "lane_id": primary_lane_id,
                                        "violation": f"turn_{maneuver}_not_allowed",
                                    }
                                )
                    break
        return violations

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        """
        Prevent unbounded growth of per-vehicle state in long-running simulations.
        """
        cutoff_ms = (current_ts.timestamp() - float(max_age_s))
        to_delete: list[int] = []
        for vid, st in self._vehicle_states.items():
            if st.last_seen_ts is None:
                continue
            if st.last_seen_ts.timestamp() < cutoff_ms:
                to_delete.append(vid)
        for vid in to_delete:
            del self._vehicle_states[vid]

