"""RTH is 09:30–16:00 America/New_York — 13:30 UTC in summer, 14:30 UTC in
winter. The old UTC-window code was only right from March to November."""

from datetime import date, datetime, timezone

from engine import session as s


def _utc_ns(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()) * s.NS


def test_rth_bounds_july_is_edt():
    lo, hi = s.rth_bounds_ns(date(2026, 7, 15))
    assert lo == _utc_ns(2026, 7, 15, 13, 30)
    assert hi == _utc_ns(2026, 7, 15, 20, 0)
    assert s.utc_offset_hours(date(2026, 7, 15)) == -4


def test_rth_bounds_january_is_est():
    lo, hi = s.rth_bounds_ns(date(2026, 1, 15))
    assert lo == _utc_ns(2026, 1, 15, 14, 30)
    assert hi == _utc_ns(2026, 1, 15, 21, 0)
    assert s.utc_offset_hours(date(2026, 1, 15)) == -5


def test_dst_transition_days():
    # 2026-03-08 clocks spring forward; the session after is EDT.
    lo, _ = s.rth_bounds_ns(date(2026, 3, 9))
    assert lo == _utc_ns(2026, 3, 9, 13, 30)
    # 2026-11-01 clocks fall back; the session after is EST.
    lo, _ = s.rth_bounds_ns(date(2026, 11, 2))
    assert lo == _utc_ns(2026, 11, 2, 14, 30)


def test_minutes_since_open_and_flatten():
    ts = _utc_ns(2026, 7, 15, 14, 0)            # 10:00 ET
    assert s.minutes_since_open(ts) == 30
    assert s.minutes_to_close(ts) == 360
    assert s.is_rth(ts)
    assert not s.is_rth(_utc_ns(2026, 7, 15, 13, 0))   # 09:00 ET
    assert s.flatten_ns(date(2026, 7, 15)) == _utc_ns(2026, 7, 15, 19, 58)
    assert s.session_date(ts) == date(2026, 7, 15)
    # 23:00 UTC is still the same ET date (19:00), 03:00 UTC is the day before in ET.
    assert s.session_date(_utc_ns(2026, 7, 15, 23, 0)) == date(2026, 7, 15)
    assert s.session_date(_utc_ns(2026, 7, 16, 3, 0)) == date(2026, 7, 15)


def test_helpers():
    assert s.hhmm_add_minutes("09:30", 15) == "09:45"
    assert s.hhmm_add_minutes("15:58", 2) == "16:00"
    assert s.window_inside_rth("09:45", "15:30")
    assert not s.window_inside_rth("09:00", "15:30")
    assert not s.window_inside_rth("10:00", "16:30")
    assert s.utc_hhmm_to_et("13:30", date(2026, 7, 15)) == "09:30"
    assert s.utc_hhmm_to_et("13:30", date(2026, 1, 15)) == "08:30"
