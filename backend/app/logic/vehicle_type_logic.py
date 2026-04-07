from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class VehicleTypeState:
    recent_observations: deque[tuple[datetime, str, float]] = field(default_factory=deque)


class TemporalVehicleTypeAssigner:
    """
    Stabilize vehicle class predictions across a short tracking window.

    A single frame can easily confuse car/truck/bus under occlusion or motion blur.
    We therefore aggregate recent predictions for the same tracked vehicle_id and
    resolve the label using confidence-weighted voting with a slight recency bias.
    """

    def __init__(self, *, history_window_ms: int = 4000, history_size: int = 12):
        self._history_window = timedelta(milliseconds=int(history_window_ms))
        self._history_size = max(int(history_size), 1)
        self._vehicle_states: dict[int, VehicleTypeState] = {}

    def resolve_type(self, *, vehicle_id: int, predicted_type: str, confidence: float, ts: datetime) -> str:
        state = self._vehicle_states.get(vehicle_id)
        if state is None:
            state = VehicleTypeState()
            self._vehicle_states[vehicle_id] = state

        state.recent_observations.append((ts, predicted_type, float(confidence)))
        while len(state.recent_observations) > self._history_size:
            state.recent_observations.popleft()

        cutoff = ts - self._history_window
        while state.recent_observations and state.recent_observations[0][0] < cutoff:
            state.recent_observations.popleft()

        scores: dict[str, float] = {}
        total = len(state.recent_observations)
        for index, (_, observed_type, observed_confidence) in enumerate(state.recent_observations):
            recency_weight = 1.0 + (index / max(total - 1, 1)) * 0.15
            scores[observed_type] = scores.get(observed_type, 0.0) + observed_confidence * recency_weight

        return max(scores.items(), key=lambda item: item[1])[0] if scores else predicted_type

    def prune(self, *, current_ts: datetime, max_age_s: float = 10.0) -> None:
        cutoff = current_ts - timedelta(seconds=float(max_age_s))
        stale_vehicle_ids = []
        for vehicle_id, state in self._vehicle_states.items():
            while state.recent_observations and state.recent_observations[0][0] < cutoff:
                state.recent_observations.popleft()
            if not state.recent_observations:
                stale_vehicle_ids.append(vehicle_id)

        for vehicle_id in stale_vehicle_ids:
            del self._vehicle_states[vehicle_id]
