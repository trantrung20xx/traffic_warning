from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Debouncer:
    min_interval_s: float = 0.25
    _last_ts: float = field(default=0.0, init=False)

    def should_accept(self) -> bool:
        now = time.monotonic()
        if now - self._last_ts < self.min_interval_s:
            return False
        self._last_ts = now
        return True
