"""Migration guardrails around pgloader's native data transfer."""
import sqlite3

import pytest
from sqlalchemy import create_engine

from models import Setting
from scripts.migrate_to_postgres import row_fingerprint, snapshot


def test_backup_includes_committed_wal_and_never_replaces_existing_file(tmp_path):
    source, backup = tmp_path / "live.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE example (id integer primary key, value text)")
        conn.commit()
        conn.execute("INSERT INTO example VALUES (1, 'committed in WAL')")
        conn.commit()
        assert source.with_suffix(".db-wal").stat().st_size > 0
        snapshot(source, backup)
        with sqlite3.connect(backup) as copied:
            assert copied.execute("SELECT value FROM example").fetchone()[0] == "committed in WAL"
        original = backup.read_bytes()
        with pytest.raises(FileExistsError):
            snapshot(source, backup)
        assert backup.read_bytes() == original


def test_row_verification_distinguishes_sql_null_from_json_null(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'values.db'}")
    Setting.__table__.create(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO settings (key,value_json) VALUES ('example',NULL)")
    sql_null = row_fingerprint(engine, Setting.__table__)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE settings SET value_json='null'")
    json_null = row_fingerprint(engine, Setting.__table__)
    assert sql_null["rows"] == json_null["rows"] == 1
    assert sql_null["sha256"] != json_null["sha256"]
    engine.dispose()


def test_migration_refuses_nonempty_destination(metadata_engine, tmp_path):
    if metadata_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL destination guard")
    import database
    from scripts.migrate_to_postgres import migrate
    from sqlalchemy import URL
    source = tmp_path / "source.db"
    engine = database.make_engine(URL.create("sqlite+pysqlite", database=str(source)).render_as_string())
    database.init_db(engine)
    engine.dispose()
    with metadata_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO settings (key,value_json) VALUES ('existing','{}')")
    with pytest.raises(ValueError, match="destination is not empty"):
        migrate(source, metadata_engine.url, tmp_path / "rejected-transfer")
    with metadata_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT key FROM settings").scalar_one() == "existing"
