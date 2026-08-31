"""Time primitives (New York session clock)."""

from __future__ import annotations

from engine.primitives.base import Primitive, register
from engine.session import NS, ns_to_et


@register
class TimeOfDay(Primitive):
    """Minutes since the 09:30 ET open (negative before the open)."""
    name = "time_of_day"

    def value(self, ctx):
        return ctx.minutes_since_open()


@register
class DayOfWeek(Primitive):
    """Day of week of the session, Monday = 0 … Friday = 4."""
    name = "day_of_week"

    def value(self, ctx):
        return float(ns_to_et(ctx.now_ns).weekday()) if ctx.now_ns else None


@register
class MinutesToClose(Primitive):
    """Minutes until the 16:00 ET close."""
    name = "minutes_to_close"

    def value(self, ctx):
        if ctx.session is None:
            return None
        return (ctx.session.close_ns - ctx.now_ns) / (60 * NS)


@register
class BarsSinceOpen(Primitive):
    """Closed primary bars since the RTH open (0 for the first bar of the session)."""
    name = "bars_since_open"

    def value(self, ctx):
        return float(len(ctx.session.bars) - 1) if ctx.session and ctx.session.bars else None
