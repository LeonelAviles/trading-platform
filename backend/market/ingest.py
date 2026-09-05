"""Ingest raw Databento files into the tiered layout (PLATFORM-SPEC.md §4.1).

One decode pass per file (streamed in chunks so peak memory does not scale
with file size) produces, per root found in the file:

    data/market/trades/root=<R>/date=<D>/part.parquet
    data/market/bars_1m/root=<R>/date=<D>/part.parquet
    data/market/book_checkpoints/root=<R>/date=<D>/part.parquet   (MBO, front month)
    data/market/liquidity_1s.duckdb                                 (MBO, front month)

and updates market-data/manifest.json. `finalize()` then recomputes
front_month.parquet, splits.json and regimes.parquet over everything on disk.

`date` is the UTC date of the file (Databento splits batch jobs by UTC day),
which always contains that day's full RTH session, so partition date ==
RTH session date. Timestamps are stored as int64 UNIX nanoseconds
throughout (Databento's and NautilusTrader's native unit) — no timezone
ambiguity in Parquet, converted at the edge in `data_store`.

Only outright contracts (per-root `outright_regex` in instruments.yaml) with
at least `min_daily_volume` traded that day are kept — spreads price a
difference, not a level, and far months carry no usable signal. The
runner-up month survives so the front-month-by-volume choice can see rolls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import duckdb
import numpy as np
import pandas as pd

from config.instruments import Instruments, load_instruments
from engine import regimes as regimes_mod
from market.book_materializer import BookMaterializer
from market.paths import Paths, get_paths

CHUNK_ROWS = 2_000_000
PRICE_SCALE = 1e-9
INT64_NULL_PRICE = 9_223_372_036_854_775_807
NS_PER_MIN = 60_000_000_000
SUPPORTED_SCHEMAS = ("mbo", "trades", "ohlcv-1m")
# Everything is in-sample for now: ~4 months of ES is too little to hold out a
# 30% OOS set and still validate on the rest. Restore 0.7 (and run
# `scripts/ingest.py --recompute-splits`) once more history is on disk.
IS_FRACTION = 1.0

TRADE_COLUMNS = ["ts_event", "ts_recv", "symbol", "price", "size", "side", "sequence"]
BAR_COLUMNS = ["symbol", "ts", "open", "high", "low", "close", "volume", "delta", "buy_vol", "sell_vol", "trades"]

_SCHEMA_RE = re.compile(r"\.(mbo|trades|ohlcv-1m|mbp-10|mbp-1|tbbo|definition)\.dbn(\.zst)?$")
_DATE_RE = re.compile(r"(20\d{2})-?(\d{2})-?(\d{2})")


def log(msg: str) -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------------------
# DuckDB helper (in-process, UTC, memory-capped)
# ----------------------------------------------------------------------------

def duck(memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"SET memory_limit='{memory_limit or os.environ.get('DUCKDB_MEMORY_LIMIT', '2GB')}'")
    con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    return con


def write_parquet(con: duckdb.DuckDBPyConnection, select_sql: str, out: Path, params: list | None = None) -> None:
    """Atomic parquet write (tmp + rename)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    con.execute(f"COPY ({select_sql}) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)", params or [])
    os.replace(tmp, out)


# ----------------------------------------------------------------------------
# Raw files: naming, schema, manifest
# ----------------------------------------------------------------------------

def schema_of(path: Path) -> str | None:
    m = _SCHEMA_RE.search(path.name)
    return m.group(1) if m else None


def date_of(path: Path) -> date | None:
    m = _DATE_RE.search(path.name)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def sha256_of(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_manifest(paths: Paths | None = None) -> dict:
    p = (paths or get_paths()).manifest
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "files": {}}


def save_manifest(manifest: dict, paths: Paths | None = None) -> None:
    p = (paths or get_paths()).manifest
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def manifest_key(path: Path, paths: Paths) -> str:
    try:
        return str(path.resolve().relative_to(paths.raw_dir.resolve()))
    except ValueError:
        return path.name


def _root_from_metadata(meta_path: Path) -> str | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        symbols = meta.get("query", {}).get("symbols") or []
        roots = {s.split(".")[0] for s in symbols if isinstance(s, str)}
        if len(roots) == 1:
            return roots.pop()
    except Exception:
        return None
    return None


def organize_raw(paths: Paths | None = None, *, dry_run: bool = False) -> list[tuple[Path, Path]]:
    """Move every `*.dbn.zst` under market-data/ (outside raw/) to
    raw/<ROOT>/<YYYY-MM-DD>.<schema>.dbn.zst. ROOT comes from the batch
    job's metadata.json next to the file (`ES.FUT` -> ES), falling back to
    `UNKNOWN` — ingest never trusts the folder anyway, it derives roots from
    the symbols inside the file."""
    paths = paths or get_paths()
    moves: list[tuple[Path, Path]] = []
    raw = paths.raw_dir.resolve()
    for src in sorted(paths.market_data_dir.rglob("*.dbn*")):
        if raw in src.resolve().parents or not src.is_file():
            continue
        schema, d = schema_of(src), date_of(src)
        if schema is None or d is None:
            log(f"organize: skip {src.name} (cannot parse schema/date)")
            continue
        root = None
        for cand in (src.parent / "metadata.json", src.parent.parent / "metadata.json"):
            if cand.exists():
                root = _root_from_metadata(cand)
                break
        root = root or "UNKNOWN"
        dst = raw / root / f"{d.isoformat()}.{schema}.dbn.zst"
        if dst.exists():
            log(f"organize: {dst.name} already present, leaving {src}")
            continue
        moves.append((src, dst))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    return moves


def list_raw_files(paths: Paths | None = None, schemas: tuple[str, ...] = SUPPORTED_SCHEMAS,
                   roots: set[str] | None = None) -> list[Path]:
    paths = paths or get_paths()
    out = []
    if not paths.raw_dir.exists():
        return out
    for p in sorted(paths.raw_dir.rglob("*.dbn*")):
        if schema_of(p) in schemas and (roots is None or p.parent.name in roots or p.parent.name == "UNKNOWN"):
            out.append(p)
    return out


# ----------------------------------------------------------------------------
# Decoding
# ----------------------------------------------------------------------------

def decode_chunks(path: Path, chunk_rows: int = CHUNK_ROWS) -> Iterator[pd.DataFrame]:
    """Yield the file's records as DataFrames (index = ts_recv ns, fixed-point
    prices) — only ingest and the replay warmer may call this."""
    import databento as db

    store = db.DBNStore.from_file(path)
    for frame in store.to_df(price_type="fixed", pretty_ts=False, count=chunk_rows):
        yield frame


def file_date(path: Path) -> date:
    d = date_of(path)
    if d is not None:
        return d
    import databento as db

    store = db.DBNStore.from_file(path)
    return datetime.fromtimestamp(store.metadata.start / 1e9, tz=timezone.utc).date()


# ----------------------------------------------------------------------------
# One day, one file
# ----------------------------------------------------------------------------

@dataclass
class IngestResult:
    file: str
    date: str
    schema: str
    roots: dict[str, dict] = field(default_factory=dict)   # root -> {symbols, front, trades, bars, liquidity, checkpoints}
    events: int = 0
    seconds: float = 0.0
    skipped_reason: str | None = None


class DayIngest:
    """Ingest one raw file. `frames` may be injected (tests) instead of a path."""

    def __init__(self, path: Path | None, *, schema: str | None = None, session_date: date | None = None,
                 frames: Iterator[pd.DataFrame] | None = None, paths: Paths | None = None,
                 instruments: Instruments | None = None, roots: set[str] | None = None,
                 book: bool = True, min_daily_volume: int | None = None, liquidity_con=None,
                 liquidity_done: set[date] | None = None, name: str | None = None):
        self.path = path
        self.name = name or (path.name if path else "frames")
        self.schema = schema or (schema_of(path) if path else "mbo")
        self.date = session_date or (file_date(path) if path else date.today())
        self.frames = frames
        self.paths = paths or get_paths()
        self.ins = instruments or load_instruments()
        self.roots_filter = roots
        self.book = book and self.schema == "mbo"
        self.min_daily_volume = int(min_daily_volume if min_daily_volume is not None
                                    else self.ins.defaults.get("min_daily_volume", 1000))
        # Either an open write connection (tests) or None -> open one only for
        # the commit, so the DuckDB exclusive lock is held for seconds, not hours.
        self.liquidity_con = liquidity_con
        self.liquidity_done = liquidity_done if liquidity_done is not None else liquidity_done_dates()
        self._symbol_root: dict[str, str | None] = {}
        self._machines: dict[str, BookMaterializer] = {}

    # -- helpers ---------------------------------------------------------------

    def _root_of(self, symbol: str) -> str | None:
        r = self._symbol_root.get(symbol, "?")
        if r == "?":
            spec = self.ins.root_for_symbol(symbol)
            r = spec.root if spec and spec.is_outright(symbol) else None
            if r is not None and self.roots_filter is not None and r not in self.roots_filter:
                r = None
            self._symbol_root[symbol] = r
        return r

    def _chunks(self) -> Iterator[pd.DataFrame]:
        if self.frames is not None:
            yield from self.frames
        else:
            yield from decode_chunks(self.path)

    def _book_already_done(self) -> bool:
        return self.date in self.liquidity_done

    def _commit_liquidity(self, m: BookMaterializer) -> int:
        import liquidity_store

        if self.liquidity_con is not None:
            return m.commit_liquidity(self.liquidity_con)
        con = liquidity_store.get_connection(read_only=False)
        try:
            return m.commit_liquidity(con)
        finally:
            con.close()

    # -- main ------------------------------------------------------------------

    def run(self) -> IngestResult:
        t0 = time.time()
        res = IngestResult(file=self.name, date=self.date.isoformat(), schema=self.schema)
        if self.schema == "ohlcv-1m":
            return self._run_ohlcv(res, t0)

        run_book = self.book and not self._book_already_done()
        trade_parts: list[pd.DataFrame] = []
        for frame in self._chunks():
            res.events += len(frame)
            symbols = frame["symbol"].to_numpy()
            roots = np.array([self._root_of(s) for s in pd.unique(symbols)], dtype=object)
            root_map = dict(zip(pd.unique(symbols), roots))
            keep = np.array([root_map[s] is not None for s in symbols])
            if not keep.any():
                continue
            sub = frame.loc[keep]
            is_trade = (sub["action"].to_numpy() == "T")
            if is_trade.any():
                t = sub.loc[is_trade]
                trade_parts.append(pd.DataFrame({
                    "ts_event": t["ts_event"].to_numpy(dtype="int64"),
                    "ts_recv": t.index.to_numpy(dtype="int64"),
                    "symbol": t["symbol"].to_numpy(),
                    "price": t["price"].to_numpy(dtype="int64"),
                    "size": t["size"].to_numpy(dtype="int64"),
                    "side": t["side"].to_numpy(),
                    "sequence": t["sequence"].to_numpy(dtype="int64"),
                }))
            if run_book:
                for sym in pd.unique(sub["symbol"].to_numpy()):
                    m = self._machines.get(sym)
                    if m is None:
                        m = self._machines[sym] = BookMaterializer(sym, self.date)
                    m.feed(sub.loc[sub["symbol"].to_numpy() == sym])

        if not trade_parts:
            res.skipped_reason = "no outright trades"
            res.seconds = time.time() - t0
            return res
        trades = pd.concat(trade_parts, ignore_index=True)
        trades = trades[trades["price"] != INT64_NULL_PRICE]
        trades["price"] = trades["price"] * PRICE_SCALE
        trades["root"] = trades["symbol"].map(self._root_of)

        con = duck()
        try:
            for root, tdf in trades.groupby("root", sort=True):
                vol = tdf.groupby("symbol")["size"].sum().sort_values(ascending=False)
                keep = vol[vol >= self.min_daily_volume]
                if keep.empty:
                    continue
                tdf = tdf[tdf["symbol"].isin(keep.index)].sort_values(["ts_event", "sequence"], kind="stable")
                front = str(keep.index[0])
                info = {"symbols": {str(k): int(v) for k, v in keep.items()}, "front": front,
                        "trades": int(len(tdf)), "bars": 0, "liquidity": 0, "checkpoints": 0}
                self._write_trades(con, root, tdf)
                info["bars"] = self._write_bars(con, root, tdf)
                if run_book:
                    m = self._machines.get(front)
                    if m is not None:
                        m.finish()
                        info["checkpoints"] = self._write_checkpoints(con, root, m)
                        info["liquidity"] = self._commit_liquidity(m)
                        self.liquidity_done.add(self.date)
                res.roots[root] = info
        finally:
            con.close()
        self._machines.clear()
        res.seconds = time.time() - t0
        return res

    def _run_ohlcv(self, res: IngestResult, t0: float) -> IngestResult:
        parts = []
        for frame in self._chunks():
            res.events += len(frame)
            f = frame.reset_index()
            f["root"] = f["symbol"].map(self._root_of)
            parts.append(f[f["root"].notna()])
        if not parts:
            res.skipped_reason = "no outright bars"
            return res
        bars = pd.concat(parts, ignore_index=True)
        for col in ("open", "high", "low", "close"):
            bars[col] = bars[col].astype("int64") * PRICE_SCALE
        con = duck()
        try:
            for root, bdf in bars.groupby("root"):
                vol = bdf.groupby("symbol")["volume"].sum().sort_values(ascending=False)
                keep = vol[vol >= self.min_daily_volume]
                if keep.empty:
                    continue
                bdf = bdf[bdf["symbol"].isin(keep.index)]
                out = pd.DataFrame({
                    "symbol": bdf["symbol"].to_numpy(), "ts": bdf["ts_event"].to_numpy(dtype="int64"),
                    "open": bdf["open"], "high": bdf["high"], "low": bdf["low"], "close": bdf["close"],
                    "volume": bdf["volume"].astype("int64"), "delta": 0, "buy_vol": 0, "sell_vol": 0, "trades": 0,
                }).sort_values(["symbol", "ts"])
                con.register("bars_df", out)
                write_parquet(con, "SELECT * FROM bars_df ORDER BY symbol, ts",
                              self.paths.partition(self.paths.bars_1m_dir, root, self.date.isoformat()) / "part.parquet")
                con.unregister("bars_df")
                res.roots[root] = {"symbols": {str(k): int(v) for k, v in keep.items()}, "front": str(keep.index[0]),
                                   "trades": 0, "bars": int(len(out)), "liquidity": 0, "checkpoints": 0}
        finally:
            con.close()
        res.seconds = time.time() - t0
        return res

    # -- writers ---------------------------------------------------------------

    def _write_trades(self, con, root: str, tdf: pd.DataFrame) -> None:
        out = self.paths.partition(self.paths.trades_dir, root, self.date.isoformat()) / "part.parquet"
        con.register("trades_df", tdf[TRADE_COLUMNS])
        write_parquet(con, """
            SELECT ts_event::BIGINT AS ts_event, ts_recv::BIGINT AS ts_recv, symbol::VARCHAR AS symbol,
                   price::DOUBLE AS price, size::INTEGER AS size, side::VARCHAR AS side, sequence::BIGINT AS sequence
            FROM trades_df ORDER BY ts_event, sequence
        """, out)
        con.unregister("trades_df")

    def _write_bars(self, con, root: str, tdf: pd.DataFrame) -> int:
        out = self.paths.partition(self.paths.bars_1m_dir, root, self.date.isoformat()) / "part.parquet"
        con.register("trades_df", tdf[TRADE_COLUMNS])
        write_parquet(con, BARS_1M_SQL.format(source="trades_df"), out)
        con.unregister("trades_df")
        return int(con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0])

    def _write_checkpoints(self, con, root: str, m: BookMaterializer) -> int:
        df = m.checkpoint_frame()
        if df.empty:
            return 0
        out = self.paths.partition(self.paths.checkpoints_dir, root, self.date.isoformat()) / "part.parquet"
        con.register("cp_df", df)
        write_parquet(con, "SELECT ts::BIGINT AS ts, symbol, side, price::DOUBLE AS price, size::INTEGER AS size, "
                           "n_orders::INTEGER AS n_orders FROM cp_df ORDER BY ts, side, price", out)
        con.unregister("cp_df")
        return int(len(df))


# The per-minute bar aggregate. Same tie-break as the legacy duckdb_store
# (sweep direction: a sell aggressor walks *down* the book, a buyer walks up),
# so a rebuild reproduces byte-identical bars and verify_ingest can compare.
BARS_1M_SQL = """
    SELECT symbol,
           (ts_event // 60000000000) * 60000000000                    AS ts,
           first(price ORDER BY ts_event, sequence, _sweep)            AS open,
           max(price)                                                  AS high,
           min(price)                                                  AS low,
           last(price ORDER BY ts_event, sequence, _sweep)             AS close,
           sum(size)::BIGINT                                           AS volume,
           sum(CASE WHEN side = 'B' THEN size WHEN side = 'A' THEN -size ELSE 0 END)::BIGINT AS delta,
           sum(CASE WHEN side = 'B' THEN size ELSE 0 END)::BIGINT      AS buy_vol,
           sum(CASE WHEN side = 'A' THEN size ELSE 0 END)::BIGINT      AS sell_vol,
           count(*)::INTEGER                                           AS trades
    FROM (SELECT *, CASE WHEN side = 'A' THEN -price ELSE price END AS _sweep FROM {source})
    GROUP BY 1, 2 ORDER BY 1, 2
"""


# ----------------------------------------------------------------------------
# Finalize: front month, splits, regimes
# ----------------------------------------------------------------------------

def _glob(base: Path) -> str:
    return str(base / "root=*" / "date=*" / "*.parquet")


def recompute_front_month(paths: Paths | None = None) -> pd.DataFrame:
    paths = paths or get_paths()
    if not any(paths.bars_1m_dir.rglob("*.parquet")):
        df = pd.DataFrame(columns=["root", "date", "symbol", "volume", "roll"])
    else:
        con = duck()
        try:
            df = con.execute(f"""
                WITH v AS (
                    SELECT root::VARCHAR AS root, date::DATE AS date, symbol, sum(volume)::BIGINT AS volume
                    FROM read_parquet('{_glob(paths.bars_1m_dir)}', hive_partitioning=true)
                    GROUP BY 1, 2, 3
                ), ranked AS (
                    SELECT *, row_number() OVER (PARTITION BY root, date ORDER BY volume DESC, symbol) AS rn FROM v
                )
                SELECT root, date, symbol, volume,
                       coalesce(symbol <> lag(symbol) OVER (PARTITION BY root ORDER BY date), false) AS roll
                FROM ranked WHERE rn = 1 ORDER BY root, date
            """).df()
        finally:
            con.close()
    con = duck()
    try:
        con.register("fm", df)
        write_parquet(con, "SELECT root, date::DATE AS date, symbol, volume::BIGINT AS volume, roll::BOOLEAN AS roll FROM fm ORDER BY root, date",
                      paths.front_month)
    finally:
        con.close()
    return df


def recompute_splits(front: pd.DataFrame, paths: Paths | None = None, *, force: bool = False) -> dict:
    """IS/OOS by session count per root at `IS_FRACTION` (1.0 today: no
    holdout). An existing split is kept (new sessions go to OOS so the
    in-sample set never silently grows); `force=True` re-freezes at the
    current fraction — the DSR trial counts of strategies validated before
    that no longer describe the new split."""
    paths = paths or get_paths()
    existing = json.loads(paths.splits.read_text()) if paths.splits.exists() and not force else {"roots": {}}
    out = {"version": 1, "ratio": IS_FRACTION, "computedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "roots": {}}
    for root, g in front.groupby("root"):
        dates = sorted(pd.Timestamp(d).date().isoformat() for d in g["date"])
        prev = existing.get("roots", {}).get(root)
        if prev and prev.get("inSample"):
            is_dates = [d for d in prev["inSample"] if d in dates]
            oos_dates = [d for d in dates if d not in set(is_dates)]
            frozen_at = prev.get("frozenAt")
        else:
            n_is = int(round(len(dates) * IS_FRACTION))
            is_dates, oos_dates = dates[:n_is], dates[n_is:]
            frozen_at = out["computedAt"]
        out["roots"][root] = {
            "sessions": len(dates), "inSample": is_dates, "outOfSample": oos_dates,
            "inSampleRange": [is_dates[0], is_dates[-1]] if is_dates else None,
            "outOfSampleRange": [oos_dates[0], oos_dates[-1]] if oos_dates else None,
            "frozenAt": frozen_at,
        }
    paths.splits.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.splits.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    os.replace(tmp, paths.splits)
    return out


def recompute_regimes(front: pd.DataFrame, paths: Paths | None = None, instruments: Instruments | None = None) -> pd.DataFrame:
    paths = paths or get_paths()
    ins = instruments or load_instruments()
    sessions = []
    if not front.empty:
        con = duck()
        try:
            for row in front.itertuples(index=False):
                d = pd.Timestamp(row.date).date()
                part = paths.partition(paths.bars_1m_dir, row.root, d.isoformat()) / "part.parquet"
                if not part.exists():
                    continue
                bars = con.execute(
                    "SELECT ts, open, high, low, close, volume FROM read_parquet(?) WHERE symbol = ? ORDER BY ts",
                    [str(part), row.symbol],
                ).df()
                sessions.append((row.root, row.symbol, d, bars))
        finally:
            con.close()
    tags = regimes_mod.tag_all(sessions, ins.session.rth_start, ins.session.rth_end)
    df = pd.DataFrame([t.to_dict() for t in tags], columns=["root", "date", "symbol", "trend", "er", "vol", "range_pct", "day_type", "bars"])
    con = duck()
    try:
        con.register("rg", df)
        write_parquet(con, "SELECT root, date::DATE AS date, symbol, trend, er::DOUBLE AS er, vol, range_pct::DOUBLE AS range_pct, "
                           "day_type, bars::INTEGER AS bars FROM rg ORDER BY root, date", paths.regimes)
    finally:
        con.close()
    return df


def finalize(paths: Paths | None = None, *, force_splits: bool = False) -> dict:
    paths = paths or get_paths()
    front = recompute_front_month(paths)
    splits = recompute_splits(front, paths, force=force_splits)
    reg = recompute_regimes(front, paths)
    return {"frontMonthRows": int(len(front)), "splits": {r: v["sessions"] for r, v in splits["roots"].items()},
            "regimeRows": int(len(reg))}


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def liquidity_done_dates(path: Path | None = None) -> set[date]:
    """Session dates already materialised in liquidity_1s.duckdb (read once,
    read-only, connection closed immediately)."""
    import liquidity_store

    con = liquidity_store.get_connection(read_only=True, path=path)
    if con is None:
        return set()
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT session_date FROM liquidity_files").fetchall()}
    except Exception:
        return set()
    finally:
        con.close()


def outputs_exist(entry: dict, paths: Paths) -> bool:
    for out in entry.get("outputs", []):
        if not (paths.data_dir / out).exists():
            return False
    return bool(entry.get("outputs"))


def ingest_files(files: list[Path], *, paths: Paths | None = None, roots: set[str] | None = None, book: bool = True,
                 rebuild: bool = False, rebuild_liquidity: bool = False, progress: Callable[[str], None] = log) -> list[IngestResult]:
    paths = paths or get_paths()
    paths.ensure_dirs()
    manifest = load_manifest(paths)
    done_dates: set[date] = set() if (rebuild_liquidity or not book) else liquidity_done_dates()
    results = []
    if True:
        for i, path in enumerate(files, 1):
            key = manifest_key(path, paths)
            entry = manifest["files"].get(key, {})
            stat = path.stat()
            same = entry.get("size") == stat.st_size and entry.get("mtime") == int(stat.st_mtime)
            if same and not rebuild and outputs_exist(entry, paths):
                progress(f"[{i}/{len(files)}] {key}: up to date, skipped")
                continue
            sha = entry.get("sha256") if same else None
            if sha is None:
                sha = sha256_of(path)
            progress(f"[{i}/{len(files)}] {key}: ingesting ({stat.st_size / 1e6:.0f} MB)")
            job = DayIngest(path, paths=paths, roots=roots, book=book, liquidity_done=done_dates)
            res = job.run()
            results.append(res)
            outputs = []
            for root, info in res.roots.items():
                for sub, flag in (("trades", info["trades"]), ("bars_1m", info["bars"]), ("book_checkpoints", info["checkpoints"])):
                    if flag:
                        outputs.append(str((paths.partition(getattr(paths, {"trades": "trades_dir", "bars_1m": "bars_1m_dir", "book_checkpoints": "checkpoints_dir"}[sub]), root, res.date) / "part.parquet").relative_to(paths.data_dir)))
            manifest["files"][key] = {
                "root": path.parent.name, "date": res.date, "schema": res.schema, "size": stat.st_size,
                "mtime": int(stat.st_mtime), "sha256": sha,
                "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "events": res.events, "roots": res.roots, "outputs": outputs,
                "skipped": res.skipped_reason,
                "archived": entry.get("archived", False), "archive_uri": entry.get("archive_uri"),
            }
            save_manifest(manifest, paths)
            summary = ", ".join(f"{r}: {v['front']} {v['trades']:,} trades/{v['bars']:,} bars" for r, v in res.roots.items()) or res.skipped_reason
            progress(f"[{i}/{len(files)}] {key}: done in {res.seconds:.0f}s — {res.events:,} events; {summary}")
    return results
