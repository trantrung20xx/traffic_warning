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
    # Một lần đọc OCR hợp lệ trong cửa sổ thời gian đang xét.
    text: str
    confidence: float
    ts: datetime


@dataclass
class LicensePlateState:
    # vehicle_id là khóa state xuyên suốt thời gian track còn sống.
    vehicle_id: int
    # best_text/confidence là kết quả tạm thời tốt nhất hiện tại.
    best_text: Optional[str] = None
    status: str = "pending"
    confidence: Optional[float] = None
    best_hits: int = 0
    # Danh sách candidate dùng để voting trong cửa sổ ngắn.
    candidates: list[PlateCandidate] = field(default_factory=list)
    first_seen_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None
    # attempt_count tăng ở mọi lần thử (kể cả fail) để quyết định unreadable.
    attempt_count: int = 0
    confirmed_ts: Optional[datetime] = None


@dataclass(frozen=True)
class LicensePlateSnapshot:
    # Snapshot trả về cho lớp realtime/UI, không lộ toàn bộ state nội bộ.
    license_plate: Optional[str]
    status: str
    confidence: Optional[float]
    consensus_hits: int = 0
    attempt_count: int = 0
    confirmed_ts: Optional[datetime] = None


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
        # candidate_window_ms là khoảng giữ candidate trước khi bị coi là quá cũ.
        self._candidate_window_ms = max(int(candidate_window_ms), 200)
        # Ngưỡng confidence tối thiểu để chấp nhận một candidate OCR.
        self._min_ocr_confidence = float(min_ocr_confidence)
        # Số hit tối thiểu cho cùng text để chốt confirmed.
        self._consensus_min_hits = max(int(consensus_min_hits), 1)
        # Quá nhiều lần thử không ra kết quả thì chuyển unreadable.
        self._max_attempts_before_unreadable = max(int(max_attempts_before_unreadable), 1)
        # Map vehicle_id -> trạng thái OCR tích lũy.
        self._states: dict[int, LicensePlateState] = {}

    def touch(self, *, vehicle_id: int, ts: datetime) -> None:
        # touch được gọi mỗi frame để cập nhật nhịp sống của state.
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
        # Đảm bảo state tồn tại trước khi cộng dồn attempt/candidate.
        self.touch(vehicle_id=vehicle_id, ts=ts)
        state = self._states[vehicle_id]
        # attempt_count luôn tăng, dù OCR trả về text hay không.
        state.attempt_count += 1

        # Chuẩn hóa text + confidence để lọc nhiễu đầu vào OCR.
        normalized_text = normalize_license_plate_text(raw_text)
        normalized_confidence = float(confidence) if confidence is not None else 0.0
        if (
            normalized_text
            and normalized_confidence >= self._min_ocr_confidence
            and normalized_confidence <= 1.0
        ):
            # Chỉ giữ candidate đạt ngưỡng, giúp voting ít bị kéo lệch bởi đọc sai.
            state.candidates.append(
                PlateCandidate(
                    text=normalized_text,
                    confidence=normalized_confidence,
                    ts=ts,
                )
            )

        # Recompute lại best_text/status sau mỗi lần observe.
        self._recompute_state(state=state, reference_ts=ts)

    def snapshot_for(self, *, vehicle_id: int) -> LicensePlateSnapshot:
        state = self._states.get(vehicle_id)
        if state is None:
            return LicensePlateSnapshot(
                license_plate=None,
                status="pending",
                confidence=None,
                consensus_hits=0,
                attempt_count=0,
                confirmed_ts=None,
            )
        # Snapshot chỉ expose các trường cần cho pipeline/WS payload.
        return LicensePlateSnapshot(
            license_plate=state.best_text,
            status=state.status if state.status in LICENSE_PLATE_STATUSES else "pending",
            confidence=state.confidence,
            consensus_hits=max(int(state.best_hits), 0),
            attempt_count=max(int(state.attempt_count), 0),
            confirmed_ts=state.confirmed_ts,
        )

    def prune(self, *, current_ts: datetime, max_age_s: float) -> None:
        # Cắt state quá hạn để tránh tăng bộ nhớ khi vehicle rời scene.
        cutoff_ts = current_ts.timestamp() - float(max_age_s)
        stale_vehicle_ids: list[int] = []
        for vehicle_id, state in self._states.items():
            if state.last_seen_ts is None:
                stale_vehicle_ids.append(vehicle_id)
                continue
            # Dọn candidate cũ trước khi đánh giá stale theo thời gian.
            self._prune_candidates(state=state, reference_ts=current_ts)
            if state.last_seen_ts.timestamp() < cutoff_ts:
                stale_vehicle_ids.append(vehicle_id)
        for vehicle_id in stale_vehicle_ids:
            del self._states[vehicle_id]

    def discard(self, *, vehicle_id: int) -> None:
        # Hủy toàn bộ state OCR của vehicle khi track đã kết thúc/không còn hợp lệ.
        self._states.pop(int(vehicle_id), None)

    def _recompute_state(self, *, state: LicensePlateState, reference_ts: datetime) -> None:
        # Bước 1: loại candidate nằm ngoài cửa sổ thời gian.
        self._prune_candidates(state=state, reference_ts=reference_ts)

        if not state.candidates:
            # Không còn candidate hợp lệ thì hạ về pending/unreadable.
            state.best_text = None
            state.confidence = None
            state.best_hits = 0
            state.status = (
                "unreadable"
                if state.attempt_count >= self._max_attempts_before_unreadable
                else "pending"
            )
            return

        # Gom candidate theo text để voting theo cụm biển số.
        by_text: dict[str, list[PlateCandidate]] = {}
        for candidate in state.candidates:
            by_text.setdefault(candidate.text, []).append(candidate)

        # Rank theo: số lần xuất hiện, confidence TB, confidence tốt nhất, thời điểm mới nhất.
        ranked: list[tuple[int, float, float, float, str]] = []
        for text, candidates in by_text.items():
            count = len(candidates)
            avg_conf = sum(item.confidence for item in candidates) / float(count)
            best_conf = max(item.confidence for item in candidates)
            latest_ts = max(item.ts.timestamp() for item in candidates)
            ranked.append((count, avg_conf, best_conf, latest_ts, text))

        # reverse=True để cụm có chất lượng cao đứng đầu.
        ranked.sort(reverse=True)
        top_count, top_avg_conf, _, top_latest_ts, top_text = ranked[0]

        # Gán winner tạm thời theo bảng xếp hạng.
        state.best_text = top_text
        state.confidence = top_avg_conf
        state.best_hits = top_count

        if top_count >= self._consensus_min_hits and top_avg_conf >= self._min_ocr_confidence:
            # Đủ hit + đủ tin cậy thì chuyển confirmed.
            state.status = "confirmed"
            if state.confirmed_ts is None:
                # Lưu mốc confirmed đầu tiên để phục vụ audit/debug.
                state.confirmed_ts = datetime.fromtimestamp(top_latest_ts, tz=reference_ts.tzinfo)
            return

        if len(ranked) >= 2:
            # Có từ 2 phương án cạnh tranh trở lên => uncertain.
            state.status = "uncertain"
            return

        # Chỉ còn 1 phương án nhưng chưa đạt chuẩn consensus/confidence.
        state.status = (
            "unreadable"
            if state.attempt_count >= self._max_attempts_before_unreadable
            else "pending"
        )
        if state.status == "unreadable":
            # unreadable thì không trả text để tránh UI hiểu nhầm đã đọc đúng.
            state.best_text = None
            state.confidence = None

    def _prune_candidates(self, *, state: LicensePlateState, reference_ts: datetime) -> None:
        # Chỉ giữ candidate trong cửa sổ [now - candidate_window, now].
        cutoff_ts = reference_ts.timestamp() - (self._candidate_window_ms / 1000.0)
        state.candidates = [candidate for candidate in state.candidates if candidate.ts.timestamp() >= cutoff_ts]
