"""Immutable Postgres prepared MBO batches; legacy Parquet is import-only.

A batch is one contract/day (at most 390 rows). Experiments still freeze their
own approved snapshots; database availability is unnecessary for old runs.
"""
import hashlib
import io
import json
import os
import shutil

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from engine.mbo_features import FEATURES, FEATURE_VERSION
from market.paths import get_paths
from research_ml import core, mbo_source, legacy_feature_store as legacy
from research_ml.schema import MBOSource

# Storage is deliberately excluded from extraction semantics. This preserves
# PR4 identities, including its pinned storage-module hash, for imported days.
def _identity(request, source, tick_size):
    identity = legacy._identity(request, source, tick_size)
    identity["featureVersion"] = FEATURE_VERSION
    return identity
_digest = legacy._digest
_validate_frame = legacy._validate_frame
COLUMNS = core.COLUMNS + FEATURES


def connect():
    url = os.environ.get("FEATURE_DATABASE_URL")
    if not url:
        raise ValueError("FEATURE_DATABASE_URL must point to PostgreSQL; import prepared Parquet first")
    return psycopg.connect(url, connect_timeout=10)


def initialize():
    """Explicit additive schema setup; never invoked by reads or extraction."""
    from pathlib import Path
    with connect() as conn:
        _timeouts(conn)
        conn.execute((Path(__file__).with_name("feature_store.sql")).read_text())


def _timeouts(conn):
    conn.execute("SET LOCAL lock_timeout = '10s'")
    conn.execute("SET LOCAL statement_timeout = '60s'")


def _rows(frame):
    return [(int(ts), *(float(v) for v in values)) for ts, values in
            zip(frame.index.as_unit("ns").asi8, frame[COLUMNS].itertuples(index=False, name=None))]


def _checksum(rows):
    return _digest({"schema": ["timestamp_ns", *COLUMNS], "rows": rows})


def _read_conn(conn, identity):
    _timeouts(conn)
    pid = _digest(identity)
    record = conn.execute("SELECT metadata, rows_sha256 FROM prepared_mbo_batches WHERE partition_id=%s", (pid,)).fetchone()
    if record is None:
        raise ValueError("prepared MBO features unavailable; prepare these settings first")
    meta, checksum = record
    document = meta["document"]
    if (meta["partitionId"] != pid or document["identity"] != identity
            or _digest(document) != meta["digest"]):
        raise ValueError("prepared MBO metadata identity changed")
    # LIMIT bounds memory even if an administrator has corrupted the table.
    rows = conn.execute("SELECT timestamp_ns," + ",".join(COLUMNS) +
        " FROM prepared_mbo_rows WHERE partition_id=%s ORDER BY timestamp_ns LIMIT 391", (pid,)).fetchall()
    if len(rows) != document["rows"] or _checksum(rows) != checksum:
        raise ValueError("prepared MBO row count or checksum changed")
    frame = pd.DataFrame(rows, columns=["timestamp_ns", *COLUMNS])
    frame.index = pd.to_datetime(frame.pop("timestamp_ns"), unit="ns", utc=True)
    frame.index.name = None
    return _validate_frame(frame, MBOSource.model_validate(identity["source"])), meta


def _read(identity):
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return _read_conn(conn, identity)


def exists(identity):
    with connect() as conn:
        return conn.execute("SELECT 1 FROM prepared_mbo_batches WHERE partition_id=%s", (_digest(identity),)).fetchone() is not None


def _publish(frame, meta):
    identity = meta["document"]["identity"]
    frame = _validate_frame(frame, MBOSource.model_validate(identity["source"]))
    rows = _rows(frame)
    if (len(rows) != meta["document"]["rows"] or meta["partitionId"] != _digest(identity)
            or meta["digest"] != _digest(meta["document"])):
        raise ValueError("prepared MBO import metadata mismatch")
    with connect() as conn:
        _timeouts(conn)
        # Serialize publishers of the same identity without blocking other days.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(meta["partitionId"][:16], 16) - 2**63,))
        if conn.execute("SELECT 1 FROM prepared_mbo_batches WHERE partition_id=%s", (meta["partitionId"],)).fetchone():
            actual, winner = _read_conn(conn, identity)
            if winner != meta or _checksum(_rows(actual)) != _checksum(rows):
                raise ValueError("conflicting concurrent MBO preparation")
            return {**winner, "reused": True}
        # Operator sets a finite byte budget. Include indexes/TOAST; leave WAL,
        # snapshots, backups and server free space outside this relation budget.
        budget = int(os.environ.get("FEATURE_STORE_MAX_BYTES", "1073741824"))
        conn.execute("SELECT pg_advisory_xact_lock(1706198701)")
        size = conn.execute("SELECT pg_total_relation_size('prepared_mbo_batches') + pg_total_relation_size('prepared_mbo_rows')").fetchone()[0]
        if budget <= 0 or size + 262144 > budget:
            raise ValueError("prepared feature database capacity guard; increase verified capacity or stop")
        conn.execute("INSERT INTO prepared_mbo_batches VALUES (%s,%s,%s,%s,%s)",
                     (meta["partitionId"], identity["settings"]["contract"], identity["source"]["day"], Jsonb(meta), _checksum(rows)))
        with conn.cursor() as cur:
            with cur.copy("COPY prepared_mbo_rows (partition_id,timestamp_ns," + ",".join(COLUMNS) + ") FROM STDIN") as copy:
                for row in rows:
                    copy.write_row((meta["partitionId"], *row))
        actual_size = conn.execute("SELECT pg_total_relation_size('prepared_mbo_batches') + pg_total_relation_size('prepared_mbo_rows')").fetchone()[0]
        if actual_size > budget:
            raise ValueError("prepared feature database capacity guard after insert; transaction rolled back")
        actual, _ = _read_conn(conn, identity)
        pd.testing.assert_frame_equal(frame, actual, check_freq=False)
    return {**meta, "reused": False}


def prepare_day(request, source, tick_size):
    identity = _identity(request, source, tick_size)
    if exists(identity):
        _, meta = _read(identity)
        raw = get_paths().raw_dir / "ES" / f"{source.day}.mbo.dbn.zst"
        if raw.is_file() and mbo_source.file_hash(raw) != meta["document"]["rawSource"]["sha256"]:
            raise ValueError("raw source differs from prepared MBO partition; existing partition was not replaced")
        return {**meta, "reused": True}
    parent = get_paths().data_dir
    while not parent.exists():
        parent = parent.parent
    floor = float(os.environ.get("FEATURE_MIN_FREE_GIB", "2"))
    if floor < 1 or shutil.disk_usage(parent).free < floor * 2**30:
        raise ValueError("local free-disk floor reached before feature extraction")
    frame, raw_source = mbo_source.extract_day(request, source, tick_size)
    frame = _validate_frame(frame, source)
    # Preserve the PR4 content document for compatibility; no persistent Parquet
    # is written. Logical row checksums independently verify database content.
    rows = frame.reset_index(drop=True)
    rows.insert(0, "timestamp_ns", frame.index.as_unit("ns").asi8)
    content = io.BytesIO()
    rows.to_parquet(content, index=False)
    document = {"identity": identity, "rawSource": raw_source, "rows": len(frame),
                "parquetSha256": hashlib.sha256(content.getvalue()).hexdigest()}
    return _publish(frame, {"partitionId": _digest(identity), "document": document, "digest": _digest(document)})


def prepare(request, tick_size):
    return {"partitions": [prepare_day(request, source, tick_size) for source in request.sources]}


def inventory(contract=None, start=None, end=None):
    out = []
    with connect() as conn:
        _timeouts(conn)
        with conn.cursor(name="feature_inventory") as cur:
            cur.execute("SELECT metadata FROM prepared_mbo_batches WHERE (%s::text IS NULL OR contract=%s) AND (%s::date IS NULL OR source_day>=%s) AND (%s::date IS NULL OR source_day<=%s) ORDER BY contract,source_day,partition_id", (contract,contract,start,start,end,end))
            for (meta,) in cur:
                if len(out) >= 10000:
                    raise ValueError("inventory exceeds 10000 batches; narrow filters")
                if _digest(meta["document"]) != meta["digest"]:
                    raise ValueError("prepared MBO metadata checksum changed")
                out.append(meta)
    return {"partitions": out, "rowChecksumsVerified": False}


def import_partition(path):
    """Verify exact original bytes, commit one day, verify parity; retain source."""
    meta = legacy._metadata(path)
    frame, verified = legacy._read(meta["document"]["identity"])
    if verified != meta:
        raise ValueError("legacy metadata changed during import")
    return _publish(frame, meta)


def load(plan, sessions, tick_size):
    if [str(s.day) for s in plan.sources] != sessions:
        raise ValueError("explicit daily source identifiers required for every frozen session")
    frames, sources, partitions = [], [], []
    rows = 0
    for source in plan.sources:
        frame, meta = _read(_identity(plan, source, tick_size))
        rows += len(frame)
        if rows > core.MAX_BARS:
            raise ValueError("dataset requires 1..50,000 bars; select a smaller date range")
        frames.append(frame)
        sources.append(meta["document"]["rawSource"])
        partitions.append({"id": meta["partitionId"], "digest": meta["digest"],
                           "parquetSha256": meta["document"]["parquetSha256"]})
    return pd.concat(frames), {"source": "prepared_MBO_features", "contract": plan.contract,
        "sources": sources, "featurePartitions": partitions, "featureVersion": FEATURE_VERSION,
        "identityPolicy": "immutable_MBO_receive_time_snapshot",
        "rawDataQuality": "checked_record_semantics_not_independent_vendor_audit",
        "availableAt": "minute_close_exclusive_receive_time_complete_events",
        "contractSelection": plan.contractSelection}
