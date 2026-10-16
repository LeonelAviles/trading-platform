"""Reusable, verified contract/day MBO features, queried locally with DuckDB.

Preparation is data-only. Experiments still freeze their own selected rows and
require approval before fitting. Committed partitions are never overwritten.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tempfile

import duckdb
import numpy as np
import pandas as pd

from engine.mbo_features import FEATURES, FEATURE_VERSION
from market.paths import get_paths
from research_ml import core, mbo_source
from research_ml.schema import MBOFeatureRequest

FORMAT_VERSION = "mbo_feature_partition_v1"
MAX_PARQUET_BYTES = 8_000_000
MAX_METADATA_BYTES = 128_000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _extractor():
    backend = Path(__file__).resolve().parents[1]
    files = ["engine/mbo_features.py", "engine/session.py", "replay/book.py",
             "research_ml/mbo_source.py", "research_ml/feature_store.py"]
    return {"code": {p: mbo_source.file_hash(backend / p) for p in files},
            "dependencies": {p: importlib.metadata.version(p) for p in
                             ("numpy", "pandas", "pyarrow", "duckdb", "databento", "databento-dbn")}}


def _identity(request, source, tick_size):
    if not np.isfinite(tick_size) or tick_size <= 0:
        raise ValueError("positive finite tick size required")
    # Exclude model, targets, experiment dates and other source days, so the
    # same day can be reused in different experiments and date ranges.
    settings = MBOFeatureRequest.model_validate({
        k: getattr(request, k) for k in MBOFeatureRequest.model_fields
    }).model_dump(mode="json", exclude={"sources"})
    return {"formatVersion": FORMAT_VERSION, "featureVersion": FEATURE_VERSION,
            "settings": settings, "source": source.model_dump(mode="json"),
            "tickSize": tick_size, "extractor": _extractor()}


def _path(identity):
    return (get_paths().mbo_features_dir / f"contract={identity['settings']['contract']}" /
            f"date={identity['source']['day']}" / _digest(identity))


def _validate_frame(frame, source):
    if list(frame.columns) != core.COLUMNS + FEATURES:
        raise ValueError("prepared MBO feature schema changed")
    frame = core.validate_bars(frame)
    local = frame.index.tz_convert("America/New_York")
    if (len(frame) > 390 or any(local.strftime("%Y-%m-%d") != str(source.day))
            or any(local.strftime("%H:%M") < "09:30") or any(local.strftime("%H:%M") >= "16:00")):
        raise ValueError("prepared MBO rows exceed selected session")
    return frame


def _metadata(path):
    try:
        with (path / "metadata.json").open("rb") as stream:
            content = stream.read(MAX_METADATA_BYTES + 1)
        if len(content) > MAX_METADATA_BYTES:
            raise ValueError("prepared MBO metadata exceeds size limit")
        meta = json.loads(content)
        document = meta["document"]
        if (_digest(document) != meta["digest"] or _path(document["identity"]) != path
                or meta["partitionId"] != _digest(document["identity"])):
            raise ValueError("prepared MBO metadata identity changed")
        return meta
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("prepared MBO metadata missing or corrupt") from exc


def _read(identity):
    path = _path(identity)
    meta = _metadata(path)
    document = meta["document"]
    if document["identity"] != identity:
        raise ValueError("prepared MBO extraction settings changed")
    parquet = path / "rows.parquet"
    try:
        if parquet.stat().st_size > MAX_PARQUET_BYTES:
            raise ValueError("prepared MBO partition exceeds size limit")
        # Read the exact checked bytes, including when another process alters
        # the file during a query. A before/after hash alone has a race.
        with parquet.open("rb") as stream:
            content = stream.read(MAX_PARQUET_BYTES + 1)
        if len(content) > MAX_PARQUET_BYTES:
            raise ValueError("prepared MBO partition exceeds size limit")
        if hashlib.sha256(content).hexdigest() != document["parquetSha256"]:
            raise ValueError("prepared MBO partition checksum changed")
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pq.read_table(pa.BufferReader(content))
        with duckdb.connect() as con:
            con.register("prepared_rows", table)
            frame = con.execute("SELECT * FROM prepared_rows").fetchdf()
        if list(frame.columns) != ["timestamp_ns", *core.COLUMNS, *FEATURES]:
            raise ValueError("prepared MBO feature schema changed")
        if not pd.api.types.is_integer_dtype(frame.timestamp_ns):
            raise ValueError("prepared MBO timestamps must be integer nanoseconds")
        frame.index = pd.to_datetime(frame.pop("timestamp_ns"), unit="ns", utc=True)
        frame.index.name = None
        source = identity["source"]
        from research_ml.schema import MBOSource
        frame = _validate_frame(frame, MBOSource.model_validate(source))
        if len(frame) != document["rows"]:
            raise ValueError("prepared MBO row count changed")
        return frame, meta
    except (OSError, KeyError, TypeError, duckdb.Error) as exc:
        raise ValueError("prepared MBO partition missing or corrupt") from exc


def prepare_day(request, source, tick_size):
    identity = _identity(request, source, tick_size)
    path = _path(identity)
    if path.exists():
        _, meta = _read(identity)
        raw = get_paths().raw_dir / "ES" / f"{source.day}.mbo.dbn.zst"
        if raw.is_file() and mbo_source.file_hash(raw) != meta["document"]["rawSource"]["sha256"]:
            raise ValueError("raw source differs from prepared MBO partition; existing partition was not replaced")
        return {**meta, "reused": True}
    frame, raw_source = mbo_source.extract_day(request, source, tick_size)
    frame = _validate_frame(frame, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".preparing-", dir=path.parent))
    try:
        rows = frame.reset_index(drop=True)
        rows.insert(0, "timestamp_ns", frame.index.as_unit("ns").asi8)
        rows.to_parquet(staging / "rows.parquet", index=False)
        document = {"identity": identity, "rawSource": raw_source, "rows": len(frame),
                    "parquetSha256": mbo_source.file_hash(staging / "rows.parquet")}
        meta = {"partitionId": _digest(identity), "document": document, "digest": _digest(document)}
        (staging / "metadata.json").write_text(json.dumps(meta, sort_keys=True, allow_nan=False))
        try:
            os.rename(staging, path)  # Publish both files atomically; never merge directories.
        except OSError:
            if not path.exists():
                raise
            # A concurrent preparer won. Accept identical content only.
            _, winner = _read(identity)
            if winner["digest"] != meta["digest"]:
                raise ValueError("conflicting concurrent MBO preparation")
            return {**winner, "reused": True}
        return {**meta, "reused": False}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def prepare(request: MBOFeatureRequest, tick_size):
    return {"partitions": [prepare_day(request, source, tick_size) for source in request.sources]}


def inventory(contract=None, start=None, end=None):
    """List committed metadata only; experiment reads verify the actual rows."""
    out = []
    for file in sorted(get_paths().mbo_features_dir.glob("contract=*/date=*/*/metadata.json")):
        if file.parent.name.startswith("."):
            continue
        # Filter by directory names before reading unrelated partitions.
        symbol, day = file.parents[2].name[9:], file.parents[1].name[5:]
        if (contract and symbol != contract) or (start and day < str(start)) or (end and day > str(end)):
            continue
        out.append(_metadata(file.parent))
    return {"partitions": out, "rowChecksumsVerified": False}


def load(plan, sessions, tick_size):
    if [str(s.day) for s in plan.sources] != sessions:
        raise ValueError("explicit daily source identifiers required for every frozen session")
    frames, sources, partitions = [], [], []
    rows = 0
    for source in plan.sources:
        identity = _identity(plan, source, tick_size)
        if not _path(identity).exists():
            raise ValueError(f"prepared MBO features unavailable for {plan.contract} {source.day}; prepare these settings first")
        frame, meta = _read(identity)
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
