"""Research/knowledge routes — `/api/research/*` and `/api/usage` arrive in
Phase 4 (PLATFORM-SPEC.md §4.8, §4.9). Empty router so the include order is
fixed now."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/research", tags=["research"])
