"""Time helpers: RFC3339 (UTC only), durations, per DESIGN §2.2."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .errors import HiveError

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"([Zz]|[+-]\d{2}:\d{2})$"
)
_DURATION = re.compile(r"^(\d+)\s*(s|m|h|d)$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 timestamp; a timezone offset is mandatory."""
    if not isinstance(value, str) or not _RFC3339.match(value):
        raise HiveError("DEADLINE_INVALID", f"时间必须为带时区的 RFC3339: {value!r}")
    text = value.replace("z", "Z").replace("t", "T")
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise HiveError("DEADLINE_INVALID", f"无法解析时间: {value!r}")
    return dt.astimezone(timezone.utc)


def format_rfc3339(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_duration(value, *, allow_none: bool = False, positive: bool = True):
    """Parse '30s'/'5m'/'2h'/'1d' into seconds. 'none' -> None when allowed."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise HiveError("INVALID_ARGUMENT", f"时长格式错误: {value!r}")
    if allow_none and isinstance(value, str) and value.strip().lower() == "none":
        return None
    m = _DURATION.match(str(value).strip()) if isinstance(value, str) else None
    if not m:
        raise HiveError("DEADLINE_INVALID", f"时长格式必须为 30s/5m/2h/1d: {value!r}")
    seconds = int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    if positive and seconds <= 0:
        raise HiveError("DEADLINE_INVALID", f"时长必须为正数: {value!r}")
    return seconds


def timedelta_seconds(td: timedelta) -> float:
    return td.total_seconds()
