"""`GET /api/desk` — the front page's single read (Phase 7)."""

from fastapi import APIRouter

import desk

router = APIRouter(prefix="/api", tags=["desk"])


@router.get("/desk")
def get_desk():
    return desk.summary()
