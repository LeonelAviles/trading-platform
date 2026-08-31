"""Shared fixtures (PLATFORM-SPEC.md §5 Phase 0, task 6).

- `synth_cfg` / `synth_mbo` / `synth_trades` / `synth_bars`: one synthetic
  RTH session (see tests/synth.py) so engine tests never need real data.
- `db_engine` / `db`: a throwaway SQLite file with the full §4.7 schema.
- `client`: FastAPI TestClient over the real app with DB init pointed at the
  throwaway file. Market routes still read whatever DuckDB store is on disk
  (or 404 cleanly when none is), so route tests stick to metadata endpoints.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests import synth  # noqa: E402


@pytest.fixture(scope="session")
def synth_cfg() -> synth.SynthConfig:
    # Short session keeps the suite fast; tests that need a full RTH day
    # build their own SynthConfig.
    return synth.SynthConfig(rth_start="09:30", rth_end="10:00", seed=7)


@pytest.fixture(scope="session")
def synth_mbo(synth_cfg):
    return synth.generate_mbo(synth_cfg)


@pytest.fixture(scope="session")
def synth_trades(synth_mbo):
    return synth.trades(synth_mbo)


@pytest.fixture(scope="session")
def synth_bars(synth_trades):
    return synth.bars_1m(synth_trades)


@pytest.fixture()
def db_engine(tmp_path):
    import database

    eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    database.init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(db_engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient over the app, with the metadata DB redirected to tmp."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    monkeypatch.setenv("PLATFORM_SKIP_DB_INIT", "1")
    import database

    eng = database.make_engine(os.environ["DATABASE_URL"])
    database.init_db(eng)
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))

    from fastapi.testclient import TestClient

    import app as app_module

    with TestClient(app_module.app) as c:
        yield c
    eng.dispose()
