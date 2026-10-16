"""Transfer metadata with pgloader into an empty PostgreSQL database.

Uses SQLite's backup API, Alembic for the schema, pgloader for every row
transfer, and full row hashes for verification. Does not change app config.
The caller must stop application writers before the final cutover snapshot.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import quote

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import JSON, URL, create_engine, inspect, select, text  # noqa: E402

import database  # noqa: E402
from models import Base  # noqa: E402


def target_url(name, env_file=REPO_ROOT / ".postgres.env"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError("database name must be a lowercase PostgreSQL identifier")
    values = dotenv_values(env_file)
    return URL.create("postgresql+psycopg", username=values["POSTGRES_USER"],
                      password=values["POSTGRES_PASSWORD"], host="127.0.0.1",
                      port=int(values.get("POSTGRES_PORT", 5432)), database=name)


def snapshot(source, destination):
    if not source.is_file():
        raise ValueError("SQLite source does not exist")
    # Backup includes committed WAL content. Never copy just the .db file.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite snapshot integrity check failed")
            if dst.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("SQLite snapshot has broken foreign keys")


def row_fingerprint(engine, table):
    """Hash all columns ordered by PK, preserving SQL NULL vs JSON null."""
    extra = [column.is_(None).label(f"_sql_null_{column.name}")
             for column in table.columns if isinstance(column.type, JSON)]
    statement = select(*table.columns, *extra).order_by(*table.primary_key.columns)
    digest, count = hashlib.sha256(), 0
    with engine.connect() as conn:
        rows = conn.execution_options(stream_results=True).execute(statement)
        for row in rows:
            digest.update(json.dumps(list(row), sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode() + b"\n")
            count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def verify(source_engine, target_engine):
    report = {}
    for table in Base.metadata.sorted_tables:
        original = row_fingerprint(source_engine, table)
        copied = row_fingerprint(target_engine, table)
        if original != copied:
            raise ValueError(f"row verification failed for {table.name}")
        report[table.name] = copied
    return report


def rebuild_search(engine):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO knowledge_chunks_fts(chunk_id, title, path, content)
                             SELECT id, heading, path, content FROM knowledge_chunks"""))
        # COPY keeps FK triggers enabled. Verify every constraint remains active.
        disabled = conn.execute(text("""SELECT count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND t.tgisinternal AND t.tgenabled='D'""")).scalar()
        if disabled:
            raise ValueError("foreign-key enforcement is disabled")


def migrate(source, target, work_dir, network="trading-features_default",
            image="trading-platform-pgloader:local"):
    work_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup = work_dir / "source.sqlite"
    snapshot(source, backup)
    src = create_engine(URL.create("sqlite+pysqlite", database=str(backup)))
    engine = database.make_engine(target.render_as_string(hide_password=False))
    try:
        source_tables = set(inspect(src).get_table_names())
        allowed = set(Base.metadata.tables) | {"alembic_version"}
        unknown = {name for name in source_tables - allowed if not name.startswith("knowledge_chunks_fts")}
        if unknown or set(Base.metadata.tables) - source_tables:
            raise ValueError("source tables do not match current application schema")
        with src.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        if revision != ScriptDirectory.from_config(config).get_current_head():
            raise ValueError("source schema must be at Alembic head before transfer")
        # Refuse any nonempty destination, including a partial prior transfer.
        with engine.connect() as conn:
            for name in inspect(engine).get_table_names():
                if name != "alembic_version":
                    quoted = engine.dialect.identifier_preparer.quote(name)
                    if conn.execute(text(f"SELECT EXISTS(SELECT 1 FROM {quoted})")).scalar():
                        raise ValueError("destination is not empty; use a fresh database")
        database.init_db(engine)
        env_file = work_dir / "pgloader.env"
        env_file.write_text("PGPASSWORD=" + target.password + "\n")
        env_file.chmod(0o600)
        try:
            # One COPY table per pgloader invocation, in SQLAlchemy's FK order.
            # FK checks stay enabled; FTS virtual/shadow tables are rebuilt.
            for table in Base.metadata.sorted_tables:
                name = table.name
                dsn = f"pgsql://{quote(target.username, safe='')}@postgres:5432/{target.database}"
                load = work_dir / f"{name}.load"
                load.write_text(f"""LOAD DATABASE
                    FROM sqlite:///source.sqlite INTO {dsn}
                    WITH data only, include no drop, no truncate, create no tables,
                         create no indexes, reset no sequences
                    INCLUDING ONLY TABLE NAMES LIKE '{name}';
                """)
                log = work_dir / f"{name}.log"
                with log.open("w") as output:
                    process = subprocess.run([
                        "docker", "run", "--rm", "--memory=1g", "--network", network,
                        "--env-file", str(env_file),
                        "--mount", f"type=bind,source={backup},target=/source.sqlite,readonly",
                        "--mount", f"type=bind,source={work_dir},target=/migration,readonly",
                        image, "--on-error-stop", f"/migration/{name}.load",
                    ], stdout=output, stderr=subprocess.STDOUT)
                if process.returncode:
                    raise ValueError(f"pgloader failed for {name}; inspect the private migration log")
                print(f"pgloader copied {name}", flush=True)
        finally:
            env_file.unlink(missing_ok=True)
        report = verify(src, engine)
        rebuild_search(engine)
        with engine.begin() as conn:
            conn.execute(text("ANALYZE"))
        result = {"verified": True, "schemaRevision": revision,
                  "tables": report, "sourceSnapshot": str(backup),
                  "destination": target.render_as_string(hide_password=True)}
        (work_dir / "verification.json").write_text(json.dumps(result, indent=2))
        print(f"Verified all {len(report)} tables and rebuilt PostgreSQL search", flush=True)
        return result
    finally:
        src.dispose()
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data/platform.db")
    parser.add_argument("--database", required=True, help="existing empty PostgreSQL destination")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "data/migrations" /
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    args = parser.parse_args(argv)
    migrate(args.source.resolve(), target_url(args.database), args.work_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
