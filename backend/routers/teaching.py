"""Teaching-mode routes — `/api/teaching/*` arrives in Phase 6
(PLATFORM-SPEC.md §5). Empty router so the include order is fixed now."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/teaching", tags=["teaching"])
