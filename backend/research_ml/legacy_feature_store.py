"""Read-only PR4 Parquet importer. Never used as a runtime storage fallback.

Pin the PR4 storage-module hash to preserve original extraction identities;
actual extraction code and dependency versions continue to be hashed.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from engine.mbo_features import FEATURES, FEATURE_VERSION
from market.paths import get_paths
from research_ml import core, mbo_source
from research_ml.schema import MBOFeatureRequest

LEGACY_STORE_SHA256 = "d185e7be55a0715a15be9c7e32c2b9d2c81e6456268c5caa4c191d277ad295c9"

FORMAT_VERSION = "mbo_feature_partition_v1"
MAX_PARQUET_BYTES = 8_000_000
MAX_METADATA_BYTES = 128_000
MAX_UNCOMPRESSED_BYTES = 8_000_000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _extractor():
    backend = Path(__file__).resolve().parents[1]
    files = ["engine/mbo_features.py", "engine/session.py", "replay/book.py",
             "research_ml/mbo_source.py", "research_ml/feature_store.py"]
    return {"code": {p: (LEGACY_STORE_SHA256 if p == "research_ml/feature_store.py" else mbo_source.file_hash(backend / p)) for p in files},
            "dependencies": {p: importlib.metadata.version(p) for p in
                             ("numpy", "pandas", "pyarrow", "duckdb", "databento", "databento-dbn")}}


def _identity(request, source, tick_size):
    if not np.isfinite(tick_size) or tick_size <= 0:
        raise ValueError("positive finite tick size required")
    # Exclude model, targets, experiment dates and other source days, so the
    # same day can be reused in different experiments and date ranges.
    settings = MBOFeatureRequest.model_validate({
        k: getattr(request, k) for k in MBOFeatureRequest.model_fields
    }).model_dump(mode="json", exclude={"sources", "featureVersion"})
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
        reader = pq.ParquetFile(pa.BufferReader(content),
            thrift_string_size_limit=MAX_METADATA_BYTES, thrift_container_size_limit=100_000)
        expected = ["timestamp_ns", *core.COLUMNS, *FEATURES]
        if (reader.metadata.num_rows > 390 or reader.schema_arrow.names != expected
                or not pa.types.is_int64(reader.schema_arrow.field("timestamp_ns").type)
                or any(not (pa.types.is_integer(f.type) or pa.types.is_floating(f.type))
                       for f in reader.schema_arrow)):
            raise ValueError("prepared MBO feature schema or row bound changed")
        # Bound declared decompression/metadata before asking the native
        # decoder to allocate pages. Hashes protect identity, not memory use.
        metadata = reader.metadata
        if (metadata.num_row_groups > 390 or metadata.serialized_size > MAX_METADATA_BYTES
                or sum(metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups)) > MAX_UNCOMPRESSED_BYTES
                or sum(metadata.row_group(i).column(j).total_uncompressed_size
                       for i in range(metadata.num_row_groups)
                       for j in range(metadata.num_columns)) > MAX_UNCOMPRESSED_BYTES):
            raise ValueError("prepared MBO uncompressed size or footer bound exceeded")
        table = reader.read()
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

