"""Raw local DBN only: no OHLCV placeholders or identity-stripped warm caches."""
import hashlib

import databento as db
import pandas as pd

from engine.mbo_features_v2 import build_minutes
from engine.mbo_features import REQUIRED
from engine.feature_schema import V2 as FEATURE_VERSION
from engine.session import et_to_ns
from market.paths import get_paths


def raw_records(path):
    store = db.DBNStore.from_file(path)
    if str(store.metadata.schema) != "mbo" or store.metadata.dataset != "GLBX.MDP3":
        raise ValueError("research features require raw GLBX.MDP3 MBO schema")
    for frame in store.to_df(price_type="fixed", pretty_ts=False, count=100_000):
        if "ts_recv" not in frame.columns:
            frame = frame.reset_index()
        if any(c not in frame for c in REQUIRED):
            raise ValueError("raw MBO identifiers/timestamps/flags are missing")
        for values in frame[REQUIRED].itertuples(index=False, name=None):
            yield dict(zip(REQUIRED, values))


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_day(plan, source, tick_size):
    """Extract one selected source without learning or reading future outcomes."""
    if plan.featureVersion != FEATURE_VERSION:
        raise ValueError("v2 extraction requires explicit featureVersion")
    path = get_paths().raw_dir / "ES" / f"{source.day}.mbo.dbn.zst"
    if not path.is_file():
        raise ValueError(f"raw ES MBO unavailable for {source.day}; OHLCV/warm caches cannot substitute")
    before = path.stat()
    digest = file_hash(path)
    frame, quality = build_minutes(raw_records(path), start_ns=et_to_ns(source.day, "09:30"),
        end_ns=et_to_ns(source.day, "16:00"), contract=plan.contract, source=source, depth=plan.depthLevels,
        max_age_ms=plan.maxBookAgeMs, max_lag_ms=plan.maxEventLagMs,
        max_unknown_fraction=plan.maxUnknownTradeFraction, tick_size=tick_size)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("raw MBO changed during snapshot capture")
    return frame, {**source.model_dump(mode="json"), "sha256": digest, "bytes": after.st_size, "quality": quality}


def load(plan, sessions, tick_size):
    if [str(s.day) for s in plan.sources] != sessions:
        raise ValueError("explicit daily source identifiers required for every frozen session")
    frames, sources = [], []
    total_rows = 0
    for s in plan.sources:
        frame, source = extract_day(plan, s, tick_size)
        total_rows += len(frame)
        if total_rows > 50_000:
            raise ValueError("dataset requires 1..50,000 bars; select a smaller date range")
        sources.append(source)
        frames.append(frame)
    return pd.concat(frames), {"source": "raw_DBN_MBO", "contract": plan.contract,
        "sources": sources, "featureVersion": FEATURE_VERSION,
        "identityPolicy": "immutable_MBO_receive_time_snapshot", "rawDataQuality": "checked_record_semantics_not_independent_vendor_audit",
        "availableAt": "minute_close_exclusive_receive_time_complete_events",
        "contractSelection": plan.contractSelection}
