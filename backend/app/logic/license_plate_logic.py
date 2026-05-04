from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


LICENSE_PLATE_STATUSES = {"pending", "confirmed", "uncertain", "unreadable"}


def normalize_license_plate_text(raw_text: Optional[str]) -> Optional[str]:
    """Chuẩn hóa biển số theo định dạng chữ/số in hoa để giảm nhiễu OCR."""
    if raw_text is None:
        return None
    cleaned = "".join(char for char in str(raw_text).upper() if char.isalnum())
    if len(cleaned) < 6 or len(cleaned) > 12:
        return None
    return cleaned


@dataclass(frozen=True)
class PlateCandidate:
    text: str
    confidence: float
    ts: datetime


@dataclass
class LicensePlateState:
    vehicle_id: int
    best_text: Optional[str] = None
    status: str = "pending"
    confidence: Optional[float] = None
    candidates: list[PlateCandidate] = field(default_factory=list)
    first_seen_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None
    attempt_count: int = 0
    confirmed_ts: Optional[datetime] = None


@dataclass(frozen=True)
class LicensePlateSnapshot:
    license_plate: Optional[str]
    status: str
    confidence: Optional[float]


class LicensePlateTemporalResolver:
    """
    Hợp nhất nhiều lần OCR theo từng track để tránh chốt biển số từ một frame đơn lẻ.
    """

    def __init__(
        self,
        *,
        candidate_window_ms: int = 4000,
        min_ocr_confidence: float = 0.65,
        consensus_min_hits: int = 2,
        max_attempts_before_unreadable: int = 6,
    ):
        self._candidate_window_ms = max(int(candidate_window_ms), 200)
        self._min_ocr_confidence = float(min_ocr_confidence)
        self._consensus_min_hits = max(int(consensus_min_hits), 1)
        self._max_attempts_before_unreadable = max(int(max_attempts_before_unreadable), 1)
        self._states: dict[int, LicensePlateState] = {}

    def touch(self, *, vehicle_id: int, ts: datetime) -> None:
        state = self._states.get(vehicle_id)
        if state is None:
            state = LicensePlateState(vehicle_id=vehicle_id, first_seen_ts=ts, last_seen_ts=ts)
            self._states[vehicle_id] = state
            return
        state.last_seen_ts = ts
        if state.first_seen_ts is None:
            state.first_seen_ts = ts

    def observe_attempt(
        self,
        *,
        vehicle_id: int,
        ts: datetime,
        raw_text: Optional[str],
        confidence: Optional[float],
    ) -> None:
        self.touch(vehicle_id=vehicle_id, ts=ts)
        state = self._states[vehicle_id]
        state.attempt_count += 1

        normalized_text = normalize_license_plate_text(raw_text)
        normalized_confidence = float(confidence) if confidence is not None else 0.0
        if (
            normalized_text
            and normalized_confidence >= self._min_ocr_confidence
            and normalized_confidence <= 1.0
        ):
            state.candidates.append(
                PlateCandidate(
                    text=normalized_text,
                    confidence=normalized_confidence,
                    ts=ts,
                )
            )

        self._recompute_state(state=state, reference_ts=ts)

    def snapshot_for(self, *, vehicle_id: int) -> LicensePlateSnapshot:
        state = self._states.get(vehicle_id)
        if state is None:
            return LicensePlateSnapshot(license_plate=None, status="pending", confidence=None)
        return LicensePlateSnapshot(
            license_plate=state.best_text,
            status=state.status if state.status in LICENSE_PLATE_STATUSES else "pending",
            confidence=state.confidence,
        )

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        stale_vehicle_ids: list[int] = []
        for vehicle_id, state in self._states.items():
            if state.last_seen_ts is None:
                stale_vehicle_ids.append(vehicle_id)
                continue
            self._prune_candidates(state=state, reference_ts=current_ts)
            if state.last_seen_ts.timestamp() < cutoff_ts:
                stale_vehicle_ids.append(vehicle_id)
        for vehicle_id in stale_vehicle_ids:
            del self._states[vehicle_id]

    def _recompute_state(self, *, state: LicensePlateState, reference_ts: datetime) -> None:
        self._prune_candidates(state=state, reference_ts=reference_ts)

        if not state.candidates:
            state.best_text = None
            state.confidence = None
            state.status = (
                "unreadable"
                if state.attempt_count >= self._max_attempts_before_unreadable
                else "pending"
            )
            return

        by_text: dict[str, list[PlateCandidate]] = {}
        for candidate in state.candidates:
            by_text.setdefault(candidate.text, []).append(candidate)

        ranked: list[tuple[int, float, float, float, str]] = []
        for text, candidates in by_text.items():
            count = len(candidates)
            avg_conf = sum(item.confidence for item in candidates) / float(count)
            best_conf = max(item.confidence for item in candidates)
            latest_ts = max(item.ts.timestamp() for item in candidates)
            ranked.append((count, avg_conf, best_conf, latest_ts, text))

        ranked.sort(reverse=True)
        top_count, top_avg_conf, _, top_latest_ts, top_text = ranked[0]

        state.best_text = top_text
        state.confidence = top_avg_conf

        if top_count >= self._consensus_min_hits and top_avg_conf >= self._min_ocr_confidence:
            state.status = "confirmed"
            if state.confirmed_ts is None:
                state.confirmed_ts = datetime.fromtimestamp(top_latest_ts, tz=reference_ts.tzinfo)
            return

        if len(ranked) >= 2:
            state.status = "uncertain"
            return

        state.status = (
            "unreadable"
            if state.attempt_count >= self._max_attempts_before_unreadable
            else "pending"
        )
        if state.status == "unreadable":
            state.best_text = None
            state.confidence = None

    def _prune_candidates(self, *, state: LicensePlateState, reference_ts: datetime) -> None:
        cutoff_ts = reference_ts.timestamp() - (self._candidate_window_ms / 1000.0)
        state.candidates = [candidate for candidate in state.candidates if candidate.ts.timestamp() >= cutoff_ts]
