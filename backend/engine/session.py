"""Session/timezone math — all in America/New_York, converted per day with
zoneinfo so DST is handled (PLATFORM-SPEC.md §4.2, bug #4 in §2).

Timestamps in and out are UNIX nanoseconds unless a function says otherwise.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NS = 1_000_000_000
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

DEFAULT_RTH_START = "09:30"
DEFAULT_RTH_END = "16:00"


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def et_datetime(d: date, hhmm: str) -> datetime:
    """Aware datetime for wall-clock `hhmm` in New York on date `d`."""
    t = parse_hhmm(hhmm)
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=ET)


def et_to_ns(d: date, hhmm: str) -> int:
    dt = et_datetime(d, hhmm)
    return int(dt.timestamp()) * NS


def ns_to_et(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / NS, tz=ET)


def session_date(ts_ns: int) -> date:
    """The New York calendar date a timestamp falls on."""
    return ns_to_et(ts_ns).date()


def rth_bounds_ns(d: date, start: str = DEFAULT_RTH_START, end: str = DEFAULT_RTH_END) -> tuple[int, int]:
    """[open, close) of the regular session on `d`, as UTC ns. DST-safe."""
    return et_to_ns(d, start), et_to_ns(d, end)


def window_bounds_ns(d: date, start: str, end: str) -> tuple[int, int]:
    return et_to_ns(d, start), et_to_ns(d, end)


def in_window(ts_ns: int, d: date, start: str, end: str) -> bool:
    lo, hi = window_bounds_ns(d, start, end)
    return lo <= ts_ns < hi


def is_rth(ts_ns: int, start: str = DEFAULT_RTH_START, end: str = DEFAULT_RTH_END) -> bool:
    d = session_date(ts_ns)
    return in_window(ts_ns, d, start, end)


def minutes_since_open(ts_ns: int, start: str = DEFAULT_RTH_START) -> float:
    d = session_date(ts_ns)
    return (ts_ns - et_to_ns(d, start)) / (60 * NS)


def minutes_to_close(ts_ns: int, end: str = DEFAULT_RTH_END) -> float:
    d = session_date(ts_ns)
    return (et_to_ns(d, end) - ts_ns) / (60 * NS)


def flatten_ns(d: date, end: str = DEFAULT_RTH_END, minutes_before: int = 2) -> int:
    """Forced-flat instant: `end - minutes_before` on date `d` (ET)."""
    return int((et_datetime(d, end) - timedelta(minutes=minutes_before)).timestamp()) * NS


def utc_offset_hours(d: date) -> float:
    """New York's UTC offset on date `d` (-4 in summer, -5 in winter)."""
    return et_datetime(d, "12:00").utcoffset().total_seconds() / 3600


def hhmm_add_minutes(hhmm: str, minutes: int) -> str:
    t = parse_hhmm(hhmm)
    total = (t.hour * 60 + t.minute + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def window_inside_rth(start: str, end: str, rth_start: str = DEFAULT_RTH_START, rth_end: str = DEFAULT_RTH_END) -> bool:
    return parse_hhmm(rth_start) <= parse_hhmm(start) < parse_hhmm(end) <= parse_hhmm(rth_end)


def utc_hhmm_to_et(hhmm_utc: str, d: date) -> str:
    """Convert a UTC wall-clock time on date `d` to New York wall-clock —
    used by the v1→v2 strategy converter for legacy UTC sessions."""
    t = parse_hhmm(hhmm_utc)
    dt = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=UTC).astimezone(ET)
    return f"{dt.hour:02d}:{dt.minute:02d}"
