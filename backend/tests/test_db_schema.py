"""Alembic head creates every §4.7 table; the ORM round-trips JSON and ids."""

from sqlalchemy import inspect

EXPECTED_TABLES = {"strategies", "backtests", "settings"}
REMOVED_TABLES = {
    "agent_runs", "findings", "research_sources", "research_docs", "research_queue", "primitive_requests", "llm_usage",
    "knowledge_facts", "teaching_sessions", "teaching_trades", "teaching_events", "teaching_questions",
}


def test_tables_exist(db_engine):
    names = set(inspect(db_engine).get_table_names())
    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {sorted(missing)}"
    assert not (REMOVED_TABLES & names), f"removed tables still present: {sorted(REMOVED_TABLES & names)}"
    assert "agent_run_id" not in {c["name"] for c in inspect(db_engine).get_columns("backtests")}
    assert "alembic_version" in names


def test_wal_mode(db_engine):
    with db_engine.connect() as con:
        mode = con.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert mode.lower() == "wal"


def test_roundtrip_strategy_and_backtest(db):
    from models import Backtest, Strategy

    s = Strategy(name="ORB test", spec_json={"schemaVersion": 2, "name": "ORB test"}, risk_json={"accountSize": 100000})
    db.add(s)
    db.commit()
    assert len(s.id) == 12
    assert s.created_at.endswith("+00:00")

    b = Backtest(strategy_id=s.id, mode="ticks", window_kind="is", status="done", metrics_json={"trades": 3})
    db.add(b)
    db.commit()
    db.expire_all()

    got = db.get(Backtest, b.id)
    assert got.metrics_json == {"trades": 3}
    assert db.get(Strategy, s.id).spec_json["schemaVersion"] == 2


def test_settings_upsert(db):
    from models import Setting

    db.add(Setting(key="replay.defaults", value_json={"ES": {"speed": 4, "book": True}}))
    db.commit()
    row = db.get(Setting, "replay.defaults")
    assert row.value_json["ES"]["speed"] == 4
