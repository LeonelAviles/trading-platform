"""Synthetic MBO session -> tiered Parquet layout, checked against the
generator's own trades/bars/book (no real data)."""

import json
from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from market import ingest as ing
from market import paths as paths_mod
from tests import synth


def _chunks(mbo: pd.DataFrame, rows: int = 5000):
    """Reshape the synthetic frame into databento `to_df` chunks: index =
    ts_recv, fixed-point int prices, plus the extra columns a DBN row has."""
    df = mbo.copy()
    df["price"] = (df["price"] * 1e9).round().astype("int64")
    df.loc[df["action"] == "R", "price"] = ing.INT64_NULL_PRICE
    df["rtype"] = 160
    df["publisher_id"] = 1
    df["instrument_id"] = 1
    df["channel_id"] = 0
    df["ts_in_delta"] = 0
    df = df.set_index("ts_recv")
    for i in range(0, len(df), rows):
        yield df.iloc[i:i + rows]


@pytest.fixture()
def data_paths(tmp_path):
    p = paths_mod.configure(data_dir=tmp_path / "data", market_data_dir=tmp_path / "market-data")
    p.ensure_dirs()
    yield p
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def _run(data_paths, cfg, mbo, liquidity_con=None, **kw):
    job = ing.DayIngest(None, schema="mbo", session_date=cfg.session_date, frames=_chunks(mbo),
                        paths=data_paths, liquidity_con=liquidity_con, name="synthetic", **kw)
    return job.run()


def test_partitions_match_generator(data_paths, synth_cfg, synth_mbo, synth_trades, synth_bars):
    res = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=1)
    assert res.skipped_reason is None
    assert set(res.roots) == {"ES"}
    info = res.roots["ES"]
    assert info["front"] == "ESM6"
    assert set(info["symbols"]) == {"ESM6", "ESU6"}

    tpart = data_paths.partition(data_paths.trades_dir, "ES", synth_cfg.session_date.isoformat()) / "part.parquet"
    bpart = data_paths.partition(data_paths.bars_1m_dir, "ES", synth_cfg.session_date.isoformat()) / "part.parquet"
    assert tpart.exists() and bpart.exists()

    con = duckdb.connect()
    t = con.execute(f"SELECT * FROM read_parquet('{tpart}') ORDER BY ts_event, sequence").df()
    assert len(t) == len(synth_trades)
    assert t["size"].sum() == synth_trades["size"].sum()
    assert (t["ts_event"].diff().fillna(0) >= 0).all()
    assert set(t.columns) >= set(ing.TRADE_COLUMNS)   # + hive root/date columns

    b = con.execute(f"SELECT * FROM read_parquet('{bpart}') ORDER BY symbol, ts").df()
    ref = synth_bars.sort_values(["symbol", "ts"]).reset_index(drop=True)
    assert len(b) == len(ref)
    for col in ("open", "high", "low", "close"):
        assert np.allclose(b[col].to_numpy(), ref[col].to_numpy())
    for col in ("volume", "delta", "buy_vol", "sell_vol", "trades"):
        assert (b[col].to_numpy() == ref[col].to_numpy()).all(), col
    assert (b["ts"].to_numpy() == ref["ts"].to_numpy()).all()


def test_min_volume_filter_drops_thin_months(data_paths, synth_cfg, synth_mbo, synth_trades):
    front_vol = synth_trades[synth_trades["symbol"] == "ESM6"]["size"].sum()
    res = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=int(front_vol))
    assert list(res.roots["ES"]["symbols"]) == ["ESM6"]


def test_book_checkpoints_match_reference(data_paths, synth_cfg, synth_mbo):
    res = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=1)
    assert res.roots["ES"]["checkpoints"] > 0
    cpart = data_paths.partition(data_paths.checkpoints_dir, "ES", synth_cfg.session_date.isoformat()) / "part.parquet"
    cp = duckdb.connect().execute(f"SELECT * FROM read_parquet('{cpart}')").df()
    assert set(cp["symbol"]) == {"ESM6"}
    # Every checkpoint's best bid/ask must equal a brute-force replay of the
    # tape up to the end of that second.
    for ts in sorted(cp["ts"].unique())[1:6]:
        snap = cp[cp["ts"] == ts]
        bids, asks = synth.book_at(synth_mbo, "ESM6", int(ts) * synth.NS + synth.NS - 1, depth=3)
        best_bid = snap[snap["side"] == "B"]["price"].max()
        best_ask = snap[snap["side"] == "A"]["price"].min()
        assert best_bid == pytest.approx(bids[0][0])
        assert best_ask == pytest.approx(asks[0][0])
        assert best_bid < best_ask


def test_liquidity_rows_written(data_paths, synth_cfg, synth_mbo):
    import liquidity_store

    assert ing.liquidity_done_dates() == set()
    res = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=1)
    con = liquidity_store.get_connection(read_only=True)
    assert con.execute("SELECT count(*) FROM liquidity_files").fetchone()[0] == 1
    assert res.roots["ES"]["liquidity"] == con.execute("SELECT count(*) FROM liquidity_changes_1s").fetchone()[0] > 0
    con.close()
    assert ing.liquidity_done_dates() == {synth_cfg.session_date}
    # Second run for the same day: the book pass is skipped, everything else redone.
    res2 = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=1)
    assert res2.roots["ES"]["liquidity"] == 0 and res2.roots["ES"]["bars"] == res.roots["ES"]["bars"]
    # Forced rebuild re-runs the pass.
    res3 = _run(data_paths, synth_cfg, synth_mbo, min_daily_volume=1, liquidity_done=set())
    assert res3.roots["ES"]["liquidity"] == res.roots["ES"]["liquidity"]


def test_finalize_front_month_splits_regimes(data_paths, synth_cfg, synth_mbo):
    # Ten sessions; IS_FRACTION is 1.0, so every session is in-sample and OOS is empty.
    days = [date(2026, 6, 1 + i) for i in range(10)]
    for i, d in enumerate(days):
        cfg = synth.SynthConfig(session_date=d, rth_end="10:00", seed=100 + i)
        mbo = synth.generate_mbo(cfg)
        _run(data_paths, cfg, mbo, min_daily_volume=1, book=False)
    summary = ing.finalize(data_paths)
    assert summary["frontMonthRows"] == 10 and summary["regimeRows"] == 10

    fm = duckdb.connect().execute(f"SELECT * FROM read_parquet('{data_paths.front_month}') ORDER BY date").df()
    assert list(fm["symbol"].unique()) == ["ESM6"]
    assert not fm["roll"].any()

    splits = json.loads(data_paths.splits.read_text())
    es = splits["roots"]["ES"]
    assert es["sessions"] == 10 and len(es["inSample"]) == 10 and es["outOfSample"] == []
    assert es["inSampleRange"] == [es["inSample"][0], es["inSample"][-1]] and es["outOfSampleRange"] is None

    # Adding a session keeps the frozen IS set and grows OOS.
    cfg = synth.SynthConfig(session_date=date(2026, 6, 12), rth_end="10:00", seed=999)
    _run(data_paths, cfg, synth.generate_mbo(cfg), min_daily_volume=1, book=False)
    splits2 = ing.recompute_splits(ing.recompute_front_month(data_paths), data_paths)
    assert splits2["roots"]["ES"]["inSample"] == es["inSample"]
    assert len(splits2["roots"]["ES"]["outOfSample"]) == 1
    forced = ing.recompute_splits(ing.recompute_front_month(data_paths), data_paths, force=True)
    assert len(forced["roots"]["ES"]["inSample"]) == 11 and forced["roots"]["ES"]["outOfSample"] == []

    rg = duckdb.connect().execute(f"SELECT * FROM read_parquet('{data_paths.regimes}')").df()
    assert set(rg["trend"]) <= {"trend", "range"}
    assert set(rg["vol"]) <= {"low", "mid", "high"}
    assert set(rg["day_type"]) <= {"open_drive", "trend_day", "rotational"}


def test_organize_raw_and_manifest(data_paths, tmp_path):
    src_dir = data_paths.market_data_dir / "batch-x"
    src_dir.mkdir(parents=True)
    (src_dir / "metadata.json").write_text(json.dumps({"query": {"symbols": ["NQ.FUT"]}}))
    f = src_dir / "glbx-mdp3-20260605.mbo.dbn.zst"
    f.write_bytes(b"not really dbn")
    moves = ing.organize_raw(data_paths)
    assert len(moves) == 1
    dst = data_paths.raw_dir / "NQ" / "2026-06-05.mbo.dbn.zst"
    assert dst.exists() and not f.exists()
    assert ing.schema_of(dst) == "mbo" and ing.date_of(dst) == date(2026, 6, 5)
    assert ing.list_raw_files(data_paths) == [dst]
    assert ing.list_raw_files(data_paths, roots={"ES"}) == []
    assert ing.manifest_key(dst, data_paths) == "NQ/2026-06-05.mbo.dbn.zst"
