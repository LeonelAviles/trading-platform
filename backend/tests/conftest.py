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
import uuid
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
def metadata_engine(tmp_path):
    """Opt-in PostgreSQL tests get a unique schema, never shared app rows."""
    import database
    from sqlalchemy import text

    postgres = os.environ.get("TEST_POSTGRES_URL")
    admin = None
    if postgres:
        admin = database.make_engine(postgres)
        assert admin.dialect.name == "postgresql"
        schema = "test_" + uuid.uuid4().hex
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        url = admin.url.update_query_dict({"options": f"-csearch_path={schema}"})
        eng = database.make_engine(url.render_as_string(hide_password=False))
    else:
        eng = database.make_engine(f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    try:
        database.init_db(eng)
        yield eng
    finally:
        eng.dispose()
        if admin:
            with admin.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


@pytest.fixture()
def db_engine(metadata_engine):
    return metadata_engine


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
def client(metadata_engine, monkeypatch):
    """TestClient over the app, with the metadata DB redirected to tmp."""
    eng = metadata_engine
    url = eng.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PLATFORM_SKIP_DB_INIT", "1")
    import database

    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(database, "DATABASE_URL", url)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))

    from fastapi.testclient import TestClient

    import app as app_module

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def agent_db(tmp_path, monkeypatch, metadata_engine):
    import database
    from sqlalchemy.orm import sessionmaker
    from research_agent import knowledge

    eng = metadata_engine
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=eng, autoflush=False, future=True))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# Reliable Research\n\nUse walk-forward validation to preserve time order and estimate stability.\n\n"
        "## Costs\n\nInclude commission and slippage before accepting a trading strategy.\n",
        encoding="utf-8",
    )
    (repo / "example.py").write_text("# this is a Python comment\ndef score():\n    return 'deflated sharpe'\n", encoding="utf-8")
    knowledge.ingest_repository(root=repo, name="test-research", url="https://example.test/repo", revision="abc123", license_name="MIT")
    yield repo


@pytest.fixture()
def feature_database(monkeypatch):
    """Real PostgreSQL only, isolated schema; never touches live feature data."""
    import psycopg
    from psycopg.conninfo import make_conninfo
    from research_ml import feature_store
    url = os.environ.get("TEST_FEATURE_POSTGRES_URL")
    if not url:
        pytest.skip("set TEST_FEATURE_POSTGRES_URL for real prepared-feature integration")
    schema = "features_test_" + uuid.uuid4().hex
    with psycopg.connect(url) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    monkeypatch.setenv("FEATURE_DATABASE_URL", make_conninfo(url, options=f"-csearch_path={schema}"))
    try:
        feature_store.initialize()
        yield
    finally:
        with psycopg.connect(url) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
