"""Compatibility shim — the application moved to `app.py` (Phase 0).

`uvicorn main:app` keeps working for existing scripts, docs and the Hermes
plugin's hints; new code should import `app` from `app`.
"""

from app import app  # noqa: F401
