"""Replay routes — `/ws/replay` and the replay-cache warmer arrive in Phase 5
(PLATFORM-SPEC.md §4.11). Empty router so the include order is fixed now."""

from fastapi import APIRouter

router = APIRouter(tags=["replay"])
