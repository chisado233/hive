"""Clock abstraction. Test profile uses a persistent offset in the DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .timeutil import now_utc


class Clock:
    """System clock; test daemons add a persistent offset (stored by store layer)."""

    def __init__(self, offset_seconds: float = 0.0):
        self.offset = timedelta(seconds=float(offset_seconds))

    def advance(self, seconds: float) -> None:
        self.offset += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return now_utc() + self.offset

    @property
    def offset_seconds(self) -> float:
        return self.offset.total_seconds()
