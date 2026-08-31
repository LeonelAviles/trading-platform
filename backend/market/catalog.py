"""NautilusTrader ParquetDataCatalog under data/market/catalog (PLATFORM-SPEC.md §4.1, §5 Phase 1).

- `FuturesContract` per observed outright symbol, built from instruments.yaml
  (tick size, multiplier, currency) plus CME quarterly rules: expiry = third
  Friday of the contract month 09:30 ET, activation ≈ 2.5 years earlier.
  Venue is `SIM` (§4.3) so the backtest worker can add the instrument and
  its data to the simulated venue unchanged.
- `TradeTick` per (symbol, day) from the trades partition: `ts_event` =
  exchange time, `ts_init` = `ts_recv` (arrival), aggressor from Databento's
  trade `side` ('B' buyer, 'A' seller), `trade_id` = feed sequence.
- `Bar` 1-MINUTE-LAST-EXTERNAL per (symbol, day) from bars_1m, stamped at
  the bar **close** (bucket start + 60 s) — Nautilus convention.
- `OrderBookDelta` only for replay-cached days (written by the Phase 5 warmer).

`catalog_manifest.json` next to the catalog records what was written so a
rebuild is incremental.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from config.instruments import Instruments, RootSpec, load_instruments
from market.paths import Paths, get_paths

VENUE = "SIM"
NS = 1_000_000_000
MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


# ----------------------------------------------------------------------------
# Contract calendar
# ----------------------------------------------------------------------------

def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d.replace(day=15 + (4 - d.weekday()) % 7)


def parse_outright(symbol: str, spec: RootSpec, as_of: date) -> tuple[int, int]:
    """`ESM6` on 2026-04-01 -> (2026, 6). The single year digit is resolved
    to the nearest year whose expiry is on/after `as_of`."""
    tail = symbol[len(spec.root):]
    if len(tail) < 2 or tail[0] not in MONTH_CODES:
        raise ValueError(f"cannot parse contract month from {symbol}")
    month = MONTH_CODES[tail[0]]
    digit = int(tail[1:])
    base = as_of.year - as_of.year % 10
    for year in range(base - 10 + digit, base + 20, 10):
        if year < 2000:
            continue
        if third_friday(year, month) >= as_of:
            return year, month
    raise ValueError(f"cannot resolve contract year for {symbol} as of {as_of}")


def contract_for(symbol: str, spec: RootSpec, as_of: date):
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AssetClass
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import FuturesContract
    from nautilus_trader.model.objects import Price, Quantity
    from zoneinfo import ZoneInfo

    year, month = parse_outright(symbol, spec, as_of)
    et = ZoneInfo("America/New_York")
    exp = third_friday(year, month)
    expiration = datetime(exp.year, exp.month, exp.day, 9, 30, tzinfo=et)
    # Activation: CME lists quarterlies years ahead; 2.5 years back is
    # generous and matches NautilusTrader's own ES fixture.
    act_year, act_month = (year - 3, month + 6) if month <= 6 else (year - 2, month - 6)
    activation = datetime(act_year, act_month, 1, 18, 0, tzinfo=et)
    increment = f"{spec.tick_size:.2f}"
    precision = len(increment.split(".")[1]) if "." in increment else 0
    ts = int(activation.timestamp()) * NS
    return FuturesContract(
        instrument_id=InstrumentId(Symbol(symbol), Venue(VENUE)),
        raw_symbol=Symbol(symbol),
        asset_class=AssetClass.INDEX,
        currency=USD,
        price_precision=precision,
        price_increment=Price.from_str(increment),
        multiplier=Quantity.from_int(int(spec.multiplier)),
        lot_size=Quantity.from_int(1),
        underlying=spec.root,
        activation_ns=ts,
        expiration_ns=int(expiration.timestamp()) * NS,
        ts_event=ts,
        ts_init=ts,
        exchange="XCME",
        info={"tickValue": spec.tick_value, "commissionPerSide": spec.commission_per_side,
              "initialMargin": spec.initial_margin},
    )


def instrument_id(symbol: str) -> str:
    return f"{symbol}.{VENUE}"


def bar_type_str(symbol: str, minutes: int = 1) -> str:
    return f"{instrument_id(symbol)}-{minutes}-MINUTE-LAST-EXTERNAL"


# ----------------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------------

def open_catalog(paths: Paths | None = None):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    p = (paths or get_paths()).catalog_dir
    p.mkdir(parents=True, exist_ok=True)
    return ParquetDataCatalog(str(p))


def _manifest_path(paths: Paths) -> Path:
    return paths.catalog_dir / "catalog_manifest.json"


def load_catalog_manifest(paths: Paths | None = None) -> dict:
    p = _manifest_path(paths or get_paths())
    return json.loads(p.read_text()) if p.exists() else {"version": 1, "instruments": {}, "days": {}}


def save_catalog_manifest(m: dict, paths: Paths | None = None) -> None:
    p = _manifest_path(paths or get_paths())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, p)


def _duck():
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"SET memory_limit='{os.environ.get('DUCKDB_MEMORY_LIMIT', '2GB')}'")
    return con


def ensure_instrument(catalog, manifest: dict, symbol: str, spec: RootSpec, as_of: date):
    if symbol in manifest["instruments"]:
        return
    inst = contract_for(symbol, spec, as_of)
    catalog.write_data([inst])
    manifest["instruments"][symbol] = {
        "id": str(inst.id), "root": spec.root, "expiry": datetime.fromtimestamp(inst.expiration_ns / NS, tz=timezone.utc).date().isoformat(),
        "multiplier": float(spec.multiplier), "tickSize": spec.tick_size,
    }


def _ticks_frame(con, part: Path, symbol: str) -> pd.DataFrame:
    df = con.execute(
        "SELECT ts_event, ts_recv, price, size, side, sequence FROM read_parquet(?) WHERE symbol = ? ORDER BY ts_recv, sequence",
        [str(part), symbol],
    ).df()
    if df.empty:
        return df
    return pd.DataFrame({
        "ts_event": pd.to_datetime(df["ts_event"].astype("int64"), unit="ns", utc=True),
        "ts_init": pd.to_datetime(df["ts_recv"].astype("int64"), unit="ns", utc=True),
        "price": df["price"].astype(float),
        "size": df["size"].astype(float),
        # Databento trade side is the aggressor's: 'B' buyer, 'A' seller.
        # 'N' (no aggressor) is folded into SELLER — the wrangler's bool
        # mapping has no third state; counts are reported in the manifest.
        "aggressor_side": (df["side"] == "B").to_numpy(),
        "trade_id": df["sequence"].astype(str),
    }), int((df["side"] == "N").sum())


def _bars_frame(con, part: Path, symbol: str) -> pd.DataFrame:
    df = con.execute(
        "SELECT ts, open, high, low, close, volume FROM read_parquet(?) WHERE symbol = ? ORDER BY ts",
        [str(part), symbol],
    ).df()
    if df.empty:
        return df
    return pd.DataFrame({
        "timestamp": pd.to_datetime(df["ts"].astype("int64") + 60 * NS, unit="ns", utc=True),
        "open": df["open"].astype(float), "high": df["high"].astype(float),
        "low": df["low"].astype(float), "close": df["close"].astype(float), "volume": df["volume"].astype(float),
    })


def build_day(catalog, manifest: dict, root: str, d: date, *, paths: Paths | None = None,
              instruments: Instruments | None = None, rebuild: bool = False, symbols: list[str] | None = None) -> dict:
    """Write ticks + 1m bars for every outright in the day's partitions."""
    from nautilus_trader.persistence.wranglers_v2 import BarDataWranglerV2, TradeTickDataWranglerV2

    paths = paths or get_paths()
    ins = instruments or load_instruments()
    spec = ins.roots[root]
    key = f"{root}/{d.isoformat()}"
    tpart = paths.partition(paths.trades_dir, root, d.isoformat()) / "part.parquet"
    bpart = paths.partition(paths.bars_1m_dir, root, d.isoformat()) / "part.parquet"
    if not tpart.exists() and not bpart.exists():
        return {"skipped": "no partitions"}
    stamp = max(p.stat().st_mtime_ns for p in (tpart, bpart) if p.exists())
    prev = manifest["days"].get(key)
    if prev and prev.get("stamp") == stamp and not rebuild:
        return {"skipped": "up to date", **prev}

    con = _duck()
    try:
        syms = symbols or sorted(set(
            con.execute(f"SELECT DISTINCT symbol FROM read_parquet('{tpart if tpart.exists() else bpart}')").df()["symbol"]
        ))
        info = {"ticks": 0, "bars": 0, "noAggressor": 0, "symbols": syms, "stamp": stamp,
                "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for sym in syms:
            ensure_instrument(catalog, manifest, sym, spec, d)
            inst = catalog.instruments(instrument_ids=[instrument_id(sym)])[0]
            if prev or rebuild:
                _delete_day(catalog, sym, d)
            if tpart.exists():
                tdf, n_none = _ticks_frame(con, tpart, sym)
                if not tdf.empty:
                    ticks = TradeTickDataWranglerV2.from_instrument(inst).from_pandas(tdf)
                    catalog.write_data(ticks, skip_disjoint_check=True)
                    info["ticks"] += len(ticks)
                    info["noAggressor"] += n_none
            if bpart.exists():
                bdf = _bars_frame(con, bpart, sym)
                if not bdf.empty:
                    bars = BarDataWranglerV2(bar_type=bar_type_str(sym), price_precision=inst.price_precision,
                                             size_precision=inst.size_precision).from_pandas(bdf)
                    catalog.write_data(bars, skip_disjoint_check=True)
                    info["bars"] += len(bars)
    finally:
        con.close()
    manifest["days"][key] = info
    return info


def _delete_day(catalog, symbol: str, d: date) -> None:
    from nautilus_trader.model.data import Bar, TradeTick

    start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) * NS
    end = start + 86400 * NS + 60 * NS
    for cls, ident in ((TradeTick, instrument_id(symbol)), (Bar, bar_type_str(symbol))):
        try:
            catalog.delete_data_range(data_cls=cls, identifier=ident, start=start, end=end)
        except Exception:
            pass


def build(paths: Paths | None = None, *, roots: set[str] | None = None, dates: tuple[date, date] | None = None,
          rebuild: bool = False, limit: int | None = None, progress=print) -> dict:
    """Build every (root, date) present in bars_1m/ that the manifest lacks."""
    paths = paths or get_paths()
    ins = load_instruments()
    catalog = open_catalog(paths)
    manifest = load_catalog_manifest(paths)
    todo = []
    for rd in sorted(paths.bars_1m_dir.glob("root=*/date=*")):
        root = rd.parent.name.split("=", 1)[1]
        d = date.fromisoformat(rd.name.split("=", 1)[1])
        if root not in ins.roots or (roots and root not in roots):
            continue
        if dates and not (dates[0] <= d <= dates[1]):
            continue
        todo.append((root, d))
    if limit:
        todo = todo[:limit]
    built = skipped = 0
    for i, (root, d) in enumerate(todo, 1):
        info = build_day(catalog, manifest, root, d, paths=paths, instruments=ins, rebuild=rebuild)
        if info.get("skipped"):
            skipped += 1
            continue
        built += 1
        save_catalog_manifest(manifest, paths)
        progress(f"[{i}/{len(todo)}] {root} {d}: {info['ticks']:,} ticks, {info['bars']:,} bars ({', '.join(info['symbols'])})")
    save_catalog_manifest(manifest, paths)
    return {"built": built, "skipped": skipped, "instruments": len(manifest["instruments"])}
