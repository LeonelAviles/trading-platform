"""data_store v2 over a synthetic ingested day: bars, CVD, footprint,
profile, session levels, DOM snapshot, coverage — all from Parquet."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

import data_store
from market import ingest as ing
from market import paths as paths_mod
from tests import synth
from tests.test_ingest import _chunks


@pytest.fixture(scope="module")
def ingested(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ds")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    cfg = synth.SynthConfig(rth_start="09:30", rth_end="11:00", seed=11)
    mbo = synth.generate_mbo(cfg)
    ing.DayIngest(None, schema="mbo", session_date=cfg.session_date, frames=_chunks(mbo), paths=p,
                  min_daily_volume=1, name="synthetic").run()
    # A second, earlier day so prior-day levels and the split exist.
    cfg0 = synth.SynthConfig(session_date=date(2026, 6, 11), rth_start="09:30", rth_end="11:00", seed=12)
    ing.DayIngest(None, schema="mbo", session_date=cfg0.session_date, frames=_chunks(synth.generate_mbo(cfg0)),
                  paths=p, min_daily_volume=1, book=False, name="synthetic0").run()
    ing.finalize(p)
    data_store.reset()
    yield {"paths": p, "cfg": cfg, "mbo": mbo, "trades": synth.trades(mbo), "bars": synth.bars_1m(synth.trades(mbo))}
    data_store.reset()
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def test_symbols_and_range(ingested):
    assert data_store.list_symbols() == ["ES1!"]
    lo, hi = data_store.data_range("ES1!")
    assert hi - lo == 2 * 86400
    assert data_store.front_symbol_for("ES1!", ingested["cfg"].session_date) == "ESM6"
    assert data_store.front_month_ranges("ES") == [("ESM6", lo, hi)]
    with pytest.raises(Exception):
        data_store.data_range("XX1!")


def test_bars_match_generator(ingested):
    ref = ingested["bars"]
    ref = ref[ref["symbol"] == "ESM6"].reset_index(drop=True)
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    bars = data_store.get_bars("ES1!", "1min", t0 // synth.NS, t1 // synth.NS)
    assert len(bars) == len(ref)
    assert np.allclose(bars["close"].to_numpy(), ref["close"].to_numpy())
    assert bars["volume"].sum() == ref["volume"].sum()
    assert bars["delta"].sum() == ref["delta"].sum()
    # Direct outright access gives the same thing; the 5-minute rollup is consistent.
    direct = data_store.get_bars("ESM6", "1min", t0 // synth.NS, t1 // synth.NS)
    assert len(direct) == len(bars)
    five = data_store.get_bars("ES1!", "5min", t0 // synth.NS, t1 // synth.NS)
    assert five["volume"].sum() == bars["volume"].sum()
    assert five["high"].max() == bars["high"].max() and five["low"].min() == bars["low"].min()
    recs = data_store.bars_to_records(five)
    assert recs[0]["hasDelta"] and recs[0]["time"] == int(five.index[0].timestamp())


def test_cvd_is_running_delta(ingested):
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    cvd = data_store.get_cvd("ES1!", "1min", t0 // synth.NS, t1 // synth.NS)
    bars = data_store.get_bars("ES1!", "1min")
    assert cvd.iloc[-1] == pytest.approx(bars["delta"].sum())


def test_trades_and_footprint_agree_with_bars(ingested):
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    trades = data_store.get_trades("ES1!", t0 // synth.NS, t1 // synth.NS)
    ref = ingested["trades"]
    ref = ref[ref["symbol"] == "ESM6"]
    assert len(trades) == len(ref)
    assert trades[0]["ts"] <= trades[-1]["ts"]
    big = data_store.get_trades("ES1!", t0 // synth.NS, t1 // synth.NS, min_size=5)
    assert all(t["size"] >= 5 for t in big) and len(big) < len(trades)

    fp = data_store.get_footprint("ES1!", "1min", t0 // synth.NS, t1 // synth.NS)
    bars = data_store.get_bars("ES1!", "1min", t0 // synth.NS, t1 // synth.NS)
    assert len(fp["bars"]) == len(bars)
    for b, (ts, row) in zip(fp["bars"], bars.iterrows()):
        assert b["time"] == int(ts.timestamp())
        assert b["volume"] == row["volume"]
        assert b["delta"] == row["delta"]
        assert sum(l["bid"] + l["ask"] for l in b["levels"]) == row["volume"]
        assert row["low"] <= b["poc"] <= row["high"]


def test_volume_profile_value_area():
    # Hand-built: POC 40%, neighbours 20% each, tails 10% -> 70% needs both neighbours.
    bins = [(100.0, 10), (100.25, 20), (100.5, 40), (100.75, 20), (101.0, 10)]
    poc, vah, val = data_store._value_area(bins)
    assert (poc, vah, val) == (100.5, 100.75, 100.25)
    assert data_store._value_area([]) == (None, None, None)


def test_volume_profile_over_session(ingested):
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    prof = data_store.get_volume_profile("ES1!", t0 // synth.NS, t1 // synth.NS)
    bars = data_store.get_bars("ES1!", "1min", t0 // synth.NS, t1 // synth.NS)
    assert prof["totalVolume"] == bars["volume"].sum()
    assert prof["val"] <= prof["poc"] <= prof["vah"]
    assert prof["binWidth"] == 0.25
    coarse = data_store.get_volume_profile("ES1!", t0 // synth.NS, t1 // synth.NS, tick_bins=4)
    assert coarse["binWidth"] == 1.0 and coarse["totalVolume"] == prof["totalVolume"]


def test_session_levels(ingested):
    d = ingested["cfg"].session_date
    lv = data_store.get_session_levels("ES1!", d)
    assert lv["frontSymbol"] == "ESM6"
    assert lv["openingRange"]["high"] >= lv["openingRange"]["low"]
    assert lv["initialBalance"]["high"] >= lv["openingRange"]["high"]
    assert lv["session"]["high"] >= lv["initialBalance"]["high"]
    assert lv["session"]["vwap"] is not None
    assert lv["priorDay"]["date"] == "2026-06-11"
    assert lv["profile"]["poc"] is not None


def test_dom_snapshot_from_checkpoints(ingested):
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    mid = (t0 + t1) // 2 // synth.NS
    snap = data_store.order_book_snapshot("ES1!", mid, depth=5)
    assert snap["stateSource"].startswith("60-second")
    assert mid - 60 < snap["asOf"] <= mid
    assert snap["bids"][0]["price"] < snap["asks"][0]["price"]
    assert len(snap["bids"]) == 5 and snap["lastPrice"] is not None
    ref_b, ref_a = synth.book_at(ingested["mbo"], "ESM6", snap["asOf"] * synth.NS + synth.NS - 1, depth=1)
    assert snap["bids"][0]["price"] == pytest.approx(ref_b[0][0])
    assert snap["asks"][0]["price"] == pytest.approx(ref_a[0][0])
    # No checkpoints for the day ingested without the book pass.
    empty = data_store.order_book_snapshot("ES1!", int(pd.Timestamp("2026-06-11 15:00", tz="UTC").timestamp()))
    assert empty["bids"] == [] and empty["stateSource"] == "no book checkpoint"


def test_heatmap_has_levels(ingested):
    t0, t1 = synth.session_bounds_ns(ingested["cfg"])
    hm = data_store.get_dom_heatmap("ES1!", t0 // synth.NS, t0 // synth.NS + 600, 10)
    assert hm["bucketSeconds"] == 10 and len(hm["buckets"]) == 61
    assert hm["scaleMax"] >= 1


def test_coverage(ingested):
    cov = data_store.coverage()
    es = cov["roots"]["ES"]
    assert es["sessions"] == 2 and es["first"] == "2026-06-11" and es["last"] == "2026-06-12"
    assert es["inSampleSessions"] + es["outOfSampleSessions"] == 2
    assert cov["sizes"]["trades"] > 0 and cov["sizes"]["bookCheckpoints"] > 0
    assert cov["replayCache"] == []


def test_multi_root_nq_and_es(tmp_path):
    """Roots come from symbols, not folders: an NQ day next to an ES day
    yields both continuous tickers, each resolving to its own front month."""
    p = paths_mod.configure(data_dir=tmp_path / "data", market_data_dir=tmp_path / "market-data")
    p.ensure_dirs()
    data_store.reset()
    try:
        d = date(2026, 6, 15)
        es = synth.SynthConfig(session_date=d, symbols=("ESM6", "ESU6"), rth_end="09:45", seed=31)
        nq = synth.SynthConfig(session_date=d, symbols=("NQM6", "NQU6"), start_price=19000.0, rth_end="09:45", seed=32)
        frames = list(_chunks(synth.generate_mbo(es))) + list(_chunks(synth.generate_mbo(nq)))
        res = ing.DayIngest(None, schema="mbo", session_date=d, frames=iter(frames), paths=p,
                            min_daily_volume=1, book=False, name="mixed").run()
        assert set(res.roots) == {"ES", "NQ"}
        assert res.roots["NQ"]["front"] == "NQM6"
        ing.finalize(p)
        data_store.reset()
        assert data_store.list_symbols() == ["ES1!", "NQ1!"]
        assert data_store.front_symbol_for("NQ1!", d) == "NQM6"
        lo, hi = synth.session_bounds_ns(nq)
        bars = data_store.get_bars("NQ1!", "1min", lo // synth.NS, hi // synth.NS)
        assert bars["close"].between(18900, 19100).all()
        es_bars = data_store.get_bars("ES1!", "1min", lo // synth.NS, hi // synth.NS)
        assert es_bars["close"].between(5200, 5400).all()
        cov = data_store.coverage()
        assert set(cov["roots"]) == {"ES", "NQ"}
        assert data_store.get_volume_profile("NQ1!", lo // synth.NS, hi // synth.NS)["binWidth"] == 0.25
    finally:
        data_store.reset()
        paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")
