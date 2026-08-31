"""SpecRules inside the NautilusTrader worker on the synthetic catalog: direction both,
limit entries with timeout, trailing/breakeven bookkeeping, scale-out records."""

from datetime import date

import pytest

import data_store
from engine.backtest_worker import run_backtest
from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from tests import synth
from tests.test_ingest import _chunks

DAYS = [date(2026, 6, 8), date(2026, 6, 9)]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("v2")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    for i, d in enumerate(DAYS):
        cfg = synth.SynthConfig(session_date=d, rth_start="09:30", rth_end="11:00", seed=400 + i, trend_per_hour=(4.0 if i == 0 else -4.0))
        ing.DayIngest(None, schema="mbo", session_date=d, frames=_chunks(synth.generate_mbo(cfg)), paths=p, min_daily_volume=1, book=False).run()
    ing.finalize(p)
    cat.build(p, progress=lambda s: None)
    data_store.reset()
    yield p
    data_store.reset()
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def _spec(**over):
    spec = {
        "schemaVersion": 2, "name": "v2 test", "instrument": {"root": "ES", "symbol": "ES1!"},
        "timeframes": {"primary": "1min", "context": []}, "direction": "both",
        "session": {"entryWindow": {"start": "09:35", "end": "10:40"}, "flattenAt": "10:55"},
        "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "highest", "params": {"n": 3}}]}, "orderType": "market", "timeoutBars": 2},
        "filters": [],
        "exit": {"stop": {"type": "ticks", "value": 12}, "target": {"type": "rr", "value": 2.0}},
        "sizing": {"type": "fixed_contracts", "value": 2, "maxContracts": 5},
        "constraints": {"maxTradesPerDay": 6, "cooldownBars": 2, "stopAfterConsecutiveLosses": 6},
        "execution": {"mode": "ticks"}, "rules": {"kind": "spec_v2"},
    }
    spec.update(over)
    return spec


def test_direction_both_trades_both_ways(store):
    res = run_backtest(_spec(), DAYS[0], DAYS[-1], "ticks")
    dirs = {t["direction"] for t in res["trades"]}
    assert dirs == {"long", "short"}, res["summary"]
    assert all(t["exitReason"] in ("stop", "target", "flatten") for t in res["trades"])


def test_limit_entry_with_timeout_and_bars_mode_market_fallback(store):
    lim = _spec(entry={"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "highest", "params": {"n": 3}}]},
                       "orderType": "limit", "limitOffsetTicks": -2, "timeoutBars": 2})
    res = run_backtest(lim, DAYS[0], DAYS[0], "ticks")
    # Some limits fill, some time out; every filled entry sits at or better than the signal close.
    assert res["trades"], "expected at least one filled limit entry"
    for t in res["trades"]:
        assert t["slippageTicks"] <= 0.5
    bars = run_backtest(lim, DAYS[0], DAYS[0], "bars")
    assert bars["trades"]


def test_breakeven_trailing_and_scale_out(store):
    spec = _spec(direction="long",
                 exit={"stop": {"type": "ticks", "value": 12}, "target": {"type": "rr", "value": 6.0},
                       "breakeven": {"atR": 1.0, "offsetTicks": 1}, "trailing": {"type": "ticks", "value": 6, "activateAtR": 1.5},
                       "scaleOut": [{"atR": 1.0, "fraction": 0.5}]},
                 sizing={"type": "fixed_contracts", "value": 4, "maxContracts": 5})
    res = run_backtest(spec, DAYS[0], DAYS[0], "ticks")
    reasons = [t["exitReason"] for t in res["trades"]]
    assert "scale_out" in reasons, reasons
    # A trailed/breakeven stop never books a full −1R loss after activation: stops after a scale-out are ≥ ~0R.
    scaled_ids = {t["entryTime"] for t in res["trades"] if t["exitReason"] == "scale_out"}
    for t in res["trades"]:
        if t["entryTime"] in scaled_ids and t["exitReason"] == "stop":
            assert t["r"] > -0.6
        if t["exitReason"] == "scale_out":
            assert t["contracts"] == 2 and t["r"] >= 0.8


def test_bars_mode_uses_sidecar_delta(store):
    """Bars mode has no ticks: bar delta must come from the bars_1m sidecar
    (keyed by bar close). A `delta > 0` trigger therefore fires on many bars,
    and the recorded entries sit on bars whose parquet delta is positive."""
    import duckdb

    spec = _spec(direction="long",
                 entry={"trigger": {"op": "gt", "args": [{"field": "delta"}, 0]}, "orderType": "market", "timeoutBars": 1},
                 exit={"stop": {"type": "ticks", "value": 8}, "target": {"type": "ticks", "value": 8}, "timeStop": {"bars": 2}},
                 constraints={"maxTradesPerDay": 50, "cooldownBars": 0, "stopAfterConsecutiveLosses": 50})
    res = run_backtest(spec, DAYS[0], DAYS[0], "bars")
    assert len(res["trades"]) >= 5, res["summary"]
    from market.paths import get_paths

    part = get_paths().partition(get_paths().bars_1m_dir, "ES", DAYS[0].isoformat()) / "part.parquet"
    rows = duckdb.connect().execute("SELECT ts, delta FROM read_parquet(?) WHERE symbol = 'ESM6'", [str(part)]).fetchall()
    delta_by_close = {int(ts) // 1_000_000_000 + 60: float(d) for ts, d in rows}
    # entry happens on the bar after the signal bar closes: signal close = entryTime rounded down to the minute
    for t in res["trades"][:10]:
        signal_close = (t["entryTime"] // 60) * 60
        assert delta_by_close.get(signal_close, 0) > 0, (t["entryTime"], delta_by_close.get(signal_close))
