"""Tiny ET formatting helper shared by teaching prompts and reports."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def format_et(unix_s: int | float, seconds: bool = False) -> str:
    d = datetime.fromtimestamp(float(unix_s), tz=timezone.utc).astimezone(ET)
    return d.strftime("%H:%M:%S" if seconds else "%H:%M")
