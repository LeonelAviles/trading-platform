"""Snapshot at every fill / exit / mark (PLATFORM-SPEC.md Phase 6.2):
bars, session levels, profile, CVD, footprints, book, last trades, the full
primitive feature vector from `FeatureContext`, and regime tags. Stored as
gzip JSON under `data/teaching/<session>/<key>.json.gz`."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

from market.paths import get_paths

NS = 1_000_000_000
BARS_PER_TF = 200
CVD_POINTS = 200
FOOTPRINT_BARS = 10
BOOK_DEPTH = 20
LAST_TRADES = 200


def regime_tags(root: str, d: date | str) -> dict:
    """Row of regimes.parquet for the root/date (empty dict when absent)."""
    p = get_paths().regimes
    if not p.exists():
        return {}
    try:
        import duckdb

        con = duckdb.connect()
        row = con.execute(f"SELECT * FROM read_parquet('{p}') WHERE root = ? AND date = ?", [root, str(d)]).df()
        con.close()
        if row.empty:
            return {}
        r = row.iloc[0].to_dict()
        return {k: (v.item() if hasattr(v, "item") else v) for k, v in r.items() if k not in ("root", "symbol")}
    except Exception:
        return {}


def levels_from_context(ctx) -> dict:
    """Session levels the primitives already track (OR, IB, VWAP, prior day, session)."""
    def val(name, params=None):
        try:
            v = ctx.value(name, params or {})
            return None if v is None else float(v)
        except Exception:
            return None

    sess = ctx.session
    return {
        "openingRange": {"high": val("opening_range_high", {"minutes": 15}), "low": val("opening_range_low", {"minutes": 15})},
        "initialBalance": {"high": val("opening_range_high", {"minutes": 60}), "low": val("opening_range_low", {"minutes": 60})},
        "vwap": val("vwap"),
        "priorDay": {"high": val("prior_day_high"), "low": val("prior_day_low"), "close": val("prior_day_close")},
        "session": {"high": sess.high if sess else None, "low": sess.low if sess else None, "open": sess.open if sess else None},
        "profile": {"poc": val("poc"), "vah": val("vah"), "val": val("val")},
    }


def build(*, ts: int, kind: str, symbol: str, root: str, bars: dict, footprints: dict, live_footprint: dict | None,
          book: tuple | None, trades: list[dict], vap: dict, cvd_series: list, ctx, position: dict | None = None,
          trade: dict | None = None, extra: dict | None = None) -> dict:
    fp_items = sorted(((int(t), lv) for t, lv in footprints.items()), key=lambda kv: kv[0])[-FOOTPRINT_BARS:]
    fps = [{"time": t, "levels": lv} for t, lv in fp_items]
    if live_footprint and live_footprint.get("time") is not None:
        fps.append({"time": live_footprint["time"], "levels": live_footprint.get("levels") or [], "partial": True})
    bids, asks = book if book else ([], [])
    d = str(date.fromtimestamp(ts / NS)) if ts else None
    snap = {
        "version": 1, "kind": kind, "ts": int(ts), "time": int(ts // NS), "symbol": symbol, "root": root,
        "bars": {tf: list(lst)[-BARS_PER_TF:] for tf, lst in bars.items()},
        "levels": levels_from_context(ctx) if ctx is not None else {},
        "volumeProfile": sorted([[float(p), int(v)] for p, v in vap.items()]),
        "cvd": list(cvd_series)[-CVD_POINTS:],
        "footprints": fps,
        "book": {"bids": bids[:BOOK_DEPTH], "asks": asks[:BOOK_DEPTH]},
        "lastTrades": list(trades)[-LAST_TRADES:],
        "features": ctx.snapshot() if ctx is not None else {},
        "regime": regime_tags(root, d) if d else {},
        "position": position, "trade": trade,
    }
    if extra:
        snap.update(extra)
    return snap


def write(session_id: str, key: str, snap: dict) -> str:
    base = get_paths().teaching_dir / session_id
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{key}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(snap, f, default=_default)
    return str(path.relative_to(get_paths().data_dir))


def read(rel_path: str) -> dict:
    p = Path(rel_path)
    if not p.is_absolute():
        p = get_paths().data_dir / rel_path
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def compact_for_prompt(snap: dict) -> dict:
    """The part of a snapshot the models see: levels, regime, the feature
    vector (rounded), last bar, book top 5, and a few flow numbers."""
    feats = {k: (round(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
             for k, v in (snap.get("features") or {}).items() if v is not None}
    bars = snap.get("bars", {}).get("1min") or []
    return {
        "time": snap.get("time"), "levels": snap.get("levels"), "regime": snap.get("regime"),
        "lastBars1m": bars[-3:], "bookTop": {"bids": snap["book"]["bids"][:5], "asks": snap["book"]["asks"][:5]},
        "features": feats, "position": snap.get("position"),
    }
