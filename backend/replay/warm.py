"""Replay-cache warmer (PLATFORM-SPEC.md §4.11).

Decodes one raw day from `market-data/raw` into

    data/replay_cache/root=<ROOT>/date=<DATE>/
        mbo.parquet          # every MBO record of the root's outrights, feed order (= ts_recv order)
        checkpoints.parquet  # serialized order maps, one row group per checkpoint
        meta.json            # symbols, counts, checkpoint index, last_used (LRU)

and evicts least-recently-used days once the cache exceeds
`REPLAY_CACHE_MAX_GB`. This module and `scripts/ingest.py` are the only
readers of raw `.dbn.zst` files.

Checkpoints: the spec asks for one every 60 s. An ES order map holds tens
of thousands of resting orders, so a checkpoint per minute over a 23-hour
day would cost more than the MBO itself. Instead a checkpoint is taken at a
second boundary once *either* `CHECKPOINT_MIN_EVENTS` events have passed
since the last one (busy tape: bounded replay-forward work, which is what a
seek actually pays for) *or* `CHECKPOINT_MAX_SECONDS` have elapsed (quiet
tape: bounded scan). Either way a seek replays at most ~200k events forward,
which is well under a second in Python. Recorded as DECISIONS.md #45.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config.instruments import Instruments, load_instruments
from market.paths import Paths, get_paths
from replay.book import L3Book

NS = 1_000_000_000
CHECKPOINT_MIN_EVENTS = 200_000
CHECKPOINT_MAX_SECONDS = 300
ROW_GROUP = 500_000
MBO_COLUMNS = ("ts_recv", "ts_event", "symbol", "action", "side", "price", "size", "order_id", "sequence", "flags")

ProgressFn = Callable[[int], None]


def day_dir(root: str, d: date | str, paths: Paths | None = None) -> Path:
    p = paths or get_paths()
    return p.partition(p.replay_cache_dir, root, str(d))


def read_meta(root: str, d: date | str, paths: Paths | None = None) -> dict | None:
    m = day_dir(root, d, paths) / "meta.json"
    if not m.exists():
        return None
    try:
        return json.loads(m.read_text())
    except json.JSONDecodeError:
        return None


def is_cached(root: str, d: date | str, paths: Paths | None = None) -> bool:
    dd = day_dir(root, d, paths)
    return (dd / "mbo.parquet").exists() and (dd / "meta.json").exists() and read_meta(root, d, paths) is not None


def touch(root: str, d: date | str, paths: Paths | None = None) -> None:
    meta = read_meta(root, d, paths)
    if meta is None:
        return
    meta["last_used"] = datetime.now(timezone.utc).isoformat()
    (day_dir(root, d, paths) / "meta.json").write_text(json.dumps(meta))


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def list_cached(paths: Paths | None = None) -> list[dict]:
    p = paths or get_paths()
    out = []
    if not p.replay_cache_dir.exists():
        return out
    for rd in sorted(p.replay_cache_dir.glob("root=*")):
        for dd in sorted(rd.glob("date=*")):
            meta_path = dd / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                continue
            out.append({
                "root": rd.name.split("=", 1)[1], "date": dd.name.split("=", 1)[1],
                "bytes": _dir_bytes(dd), "events": meta.get("events"), "symbols": meta.get("symbols"),
                "front": meta.get("front"), "lastUsed": meta.get("last_used"), "createdAt": meta.get("created_at"),
                "checkpoints": {s: len(v) for s, v in (meta.get("checkpoints") or {}).items()},
            })
    return out


def evict(paths: Paths | None = None, *, keep: set[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Delete least-recently-used days until the cache fits its cap."""
    p = paths or get_paths()
    cap = int(p.replay_cache_max_gb * (1 << 30))
    days = list_cached(p)
    total = sum(d["bytes"] for d in days)
    removed: list[tuple[str, str]] = []
    days.sort(key=lambda d: d["lastUsed"] or d["createdAt"] or "")
    for d in days:
        if total <= cap:
            break
        if keep and (d["root"], d["date"]) in keep:
            continue
        shutil.rmtree(day_dir(d["root"], d["date"], p), ignore_errors=True)
        total -= d["bytes"]
        removed.append((d["root"], d["date"]))
    return removed


def raw_file_for(root: str, d: date | str, paths: Paths | None = None) -> Path | None:
    p = paths or get_paths()
    cand = p.raw_dir / root / f"{d}.mbo.dbn.zst"
    if cand.exists():
        return cand
    for f in (p.raw_dir / root).glob(f"{d}.*.dbn.zst") if (p.raw_dir / root).exists() else []:
        if ".mbo." in f.name:
            return f
    return None


def expected_events(root: str, d: date | str, paths: Paths | None = None) -> int | None:
    """Row count recorded by ingest (manifest), used only for progress."""
    try:
        from market.ingest import load_manifest

        for entry in load_manifest(paths or get_paths()).get("files", {}).values():
            if entry.get("root") == root and entry.get("date") == str(d):
                return int(entry.get("events") or 0) or None
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------------

class _Writer:
    """Chunk-wise Parquet writer for `mbo.parquet` (never holds a day in RAM)."""

    schema = pa.schema([
        ("ts_recv", pa.int64()), ("ts_event", pa.int64()), ("symbol", pa.dictionary(pa.int32(), pa.string())),
        ("action", pa.dictionary(pa.int8(), pa.string())), ("side", pa.dictionary(pa.int8(), pa.string())),
        ("price", pa.int64()), ("size", pa.int32()), ("order_id", pa.int64()), ("sequence", pa.int64()),
        ("flags", pa.int16()),
    ])

    def __init__(self, path: Path):
        self.path = path
        self.tmp = path.with_suffix(".parquet.tmp")
        self.w = pq.ParquetWriter(self.tmp, self.schema, compression="zstd")
        self.rows = 0

    def write(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        table = pa.Table.from_pydict({
            "ts_recv": frame["ts_recv"].to_numpy(dtype="int64"),
            "ts_event": frame["ts_event"].to_numpy(dtype="int64"),
            "symbol": pa.array(frame["symbol"].astype(str).to_numpy()).dictionary_encode(),
            "action": pa.array(frame["action"].astype(str).to_numpy()).dictionary_encode(),
            "side": pa.array(frame["side"].astype(str).to_numpy()).dictionary_encode(),
            "price": frame["price"].to_numpy(dtype="int64"),
            "size": frame["size"].to_numpy(dtype="int32"),
            "order_id": frame["order_id"].to_numpy(dtype="int64"),
            "sequence": frame["sequence"].to_numpy(dtype="int64"),
            "flags": frame["flags"].to_numpy(dtype="int16"),
        }, schema=self.schema)
        self.w.write_table(table, row_group_size=ROW_GROUP)
        self.rows += len(frame)

    def close(self) -> None:
        self.w.close()
        os.replace(self.tmp, self.path)


class _CheckpointWriter:
    schema = pa.schema([
        ("ckpt_ts", pa.int64()), ("symbol", pa.string()), ("order_id", pa.int64()),
        ("side", pa.string()), ("price", pa.int64()), ("size", pa.int32()),
    ])

    def __init__(self, path: Path):
        self.path = path
        self.tmp = path.with_suffix(".parquet.tmp")
        self.w = pq.ParquetWriter(self.tmp, self.schema, compression="zstd")
        self.index: dict[str, list[int]] = {}
        self.max_orders: dict[str, int] = {}

    def write(self, symbol: str, ckpt_ts: int, book: L3Book) -> None:
        snap = book.snapshot()
        n = len(snap["order_id"])
        table = pa.Table.from_pydict({
            "ckpt_ts": np.full(n, ckpt_ts, dtype=np.int64), "symbol": pa.array([symbol] * n, pa.string()),
            "order_id": snap["order_id"], "side": pa.array(snap["side"].tolist(), pa.string()),
            "price": snap["price"], "size": snap["size"],
        }, schema=self.schema)
        self.w.write_table(table, row_group_size=max(n, 1))
        self.index.setdefault(symbol, []).append(int(ckpt_ts))
        self.max_orders[symbol] = max(self.max_orders.get(symbol, 0), n)

    def close(self) -> None:
        self.w.close()
        os.replace(self.tmp, self.path)


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """databento `to_df(pretty_ts=False, price_type='fixed')` puts ts_recv in
    the index; tests hand over flat frames. Return a flat frame with every
    column in MBO_COLUMNS."""
    f = frame
    if "ts_recv" not in f.columns:
        f = f.reset_index().rename(columns={"index": "ts_recv"})
    out = pd.DataFrame({
        "ts_recv": f["ts_recv"].to_numpy(dtype="int64"),
        "ts_event": f["ts_event"].to_numpy(dtype="int64") if "ts_event" in f else f["ts_recv"].to_numpy(dtype="int64"),
        "symbol": f["symbol"].astype(str).to_numpy(),
        "action": f["action"].astype(str).to_numpy(),
        "side": f["side"].astype(str).to_numpy(),
        "price": (np.round(f["price"].to_numpy(dtype="float64") * 1e9).astype("int64")
                  if np.issubdtype(f["price"].dtype, np.floating) else f["price"].to_numpy(dtype="int64")),
        "size": f["size"].to_numpy(dtype="int64"),
        "order_id": f["order_id"].to_numpy(dtype="int64"),
        "sequence": f["sequence"].to_numpy(dtype="int64") if "sequence" in f else np.arange(len(f), dtype="int64"),
        "flags": f["flags"].to_numpy(dtype="int64") if "flags" in f else np.zeros(len(f), dtype="int64"),
    })
    return out


def warm_day(root: str, d: date | str, *, paths: Paths | None = None, progress: ProgressFn | None = None,
             frames: Iterator[pd.DataFrame] | None = None, instruments: Instruments | None = None,
             expected: int | None = None, evict_after: bool = True) -> dict:
    """Decode one day into the replay cache. `frames` (tests) replaces the raw
    file. Returns the meta dict. Idempotent: an existing complete day is
    returned untouched."""
    p = paths or get_paths()
    d = str(d)
    if is_cached(root, d, p):
        touch(root, d, p)
        if progress:
            progress(100)
        return read_meta(root, d, p)
    ins = instruments or load_instruments()
    spec = ins.roots.get(root)
    if spec is None:
        raise ValueError(f"unknown root {root}")

    if frames is None:
        raw = raw_file_for(root, d, p)
        if raw is None:
            raise FileNotFoundError(f"no raw MBO file for {root} {d} under {p.raw_dir}")
        from market.ingest import decode_chunks

        frames = decode_chunks(raw)
        expected = expected or expected_events(root, d, p) or max(1, int(raw.stat().st_size / 1024 * 30))
    expected = expected or 1

    dd = day_dir(root, d, p)
    dd.mkdir(parents=True, exist_ok=True)
    for stale in dd.glob("*.tmp"):
        stale.unlink()
    writer = _Writer(dd / "mbo.parquet")
    ck = _CheckpointWriter(dd / "checkpoints.parquet")
    books: dict[str, L3Book] = {}
    last_ckpt_s: dict[str, int] = {}
    events_since: dict[str, int] = {}
    counts: dict[str, int] = {}
    trades: dict[str, int] = {}
    first_ts = None
    last_ts = None
    t0 = time.time()
    done = 0
    last_pct = -1
    symbol_ok: dict[str, bool] = {}

    for raw_frame in frames:
        frame = _normalize(raw_frame)
        syms = frame["symbol"].to_numpy()
        keep = np.fromiter((symbol_ok.setdefault(s, spec.is_outright(s)) for s in syms), dtype=bool, count=len(syms))
        frame = frame[keep]
        done += len(raw_frame)
        if frame.empty:
            continue
        writer.write(frame)
        ts = frame["ts_recv"].to_numpy()
        if first_ts is None:
            first_ts = int(ts[0])
        last_ts = int(ts[-1])
        for sym, g in frame.groupby("symbol", sort=False):
            book = books.get(sym)
            if book is None:
                book = books[sym] = L3Book()
                last_ckpt_s[sym] = -1
                events_since[sym] = 0
            counts[sym] = counts.get(sym, 0) + len(g)
            trades[sym] = trades.get(sym, 0) + int((g["action"].to_numpy() == "T").sum())
            gts = g["ts_recv"].to_numpy()
            ga = g["action"].to_numpy()
            gs = g["side"].to_numpy()
            gp = g["price"].to_numpy()
            gz = g["size"].to_numpy()
            go = g["order_id"].to_numpy()
            apply = book.apply
            cur_s = last_ckpt_s[sym]
            since = events_since[sym]
            prev_second = int(gts[0]) // NS
            for i in range(len(g)):
                sec = int(gts[i]) // NS
                if sec != prev_second:
                    # end of `prev_second`: every event of it applied.
                    if cur_s < 0 or since >= CHECKPOINT_MIN_EVENTS or (prev_second - cur_s >= CHECKPOINT_MAX_SECONDS and since > 0):
                        ck.write(sym, prev_second, book)
                        cur_s = prev_second
                        since = 0
                    prev_second = sec
                apply(ga[i], gs[i], int(gp[i]), int(gz[i]), int(go[i]))
                since += 1
            book.last_ts = int(gts[-1])
            last_ckpt_s[sym] = cur_s
            events_since[sym] = since
        pct = min(99, int(100 * done / expected))
        if progress and pct != last_pct:
            progress(pct)
            last_pct = pct

    for sym, book in books.items():
        if book.last_ts is not None:
            ck.write(sym, book.last_ts // NS, book)
    writer.close()
    ck.close()
    front = max(trades, key=trades.get) if trades else None
    meta = {
        "root": root, "date": d, "symbols": sorted(books), "front": front, "events": writer.rows,
        "trades": trades, "counts": counts, "first_ts": first_ts, "last_ts": last_ts,
        "checkpoints": ck.index, "max_orders": ck.max_orders,
        "checkpoint_policy": {"min_events": CHECKPOINT_MIN_EVENTS, "max_seconds": CHECKPOINT_MAX_SECONDS},
        "seconds": round(time.time() - t0, 1),
        "created_at": datetime.now(timezone.utc).isoformat(), "last_used": datetime.now(timezone.utc).isoformat(),
    }
    (dd / "meta.json").write_text(json.dumps(meta))
    if progress:
        progress(100)
    if evict_after:
        evict(p, keep={(root, d)})
    return meta


def load_checkpoint(root: str, d: date | str, symbol: str, ts_ns: int, paths: Paths | None = None) -> tuple[int | None, dict | None]:
    """Nearest checkpoint at/before `ts_ns` for `symbol`: (checkpoint ts in
    ns, snapshot dict) or (None, None) when the day has none before `ts_ns`."""
    meta = read_meta(root, d, paths)
    if not meta:
        return None, None
    stamps = (meta.get("checkpoints") or {}).get(symbol) or []
    want = ts_ns // NS
    best = None
    for s in stamps:
        if s <= want:
            best = s
        else:
            break
    if best is None:
        return None, None
    table = pq.read_table(day_dir(root, d, paths) / "checkpoints.parquet",
                          filters=[("ckpt_ts", "=", best), ("symbol", "=", symbol)])
    snap = {
        "order_id": table["order_id"].to_numpy(), "side": np.array(table["side"].to_pylist(), dtype="U1"),
        "price": table["price"].to_numpy(), "size": table["size"].to_numpy(),
    }
    # The checkpoint holds every event of second `best` — the replay must
    # resume strictly after it.
    return (best + 1) * NS, snap
