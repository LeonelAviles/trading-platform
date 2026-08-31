"""The execution layer on a synthetic 3-day catalog: hand-computed PnL for
"long at open, flat at close", bars vs ticks agreement, and constraints."""

from datetime import date

import pytest

import data_store
from config.instruments import get_root
from engine import pnl as P
from engine.backtest_worker import run_backtest
from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from tests import synth
from tests.test_ingest import _chunks

DAYS = [date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)]
ES = P.ContractSpec.from_root(get_root("ES"))


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bt")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    tapes = {}
    for i, d in enumerate(DAYS):
        cfg = synth.SynthConfig(session_date=d, rth_start="09:30", rth_end="10:30", seed=200 + i,
                                trend_per_hour=(3.0 if i == 0 else -3.0 if i == 1 else 0.0))
        mbo = synth.generate_mbo(cfg)
        tapes[d] = synth.trades(mbo)
        ing.DayIngest(None, schema="mbo", session_date=d, frames=_chunks(mbo), paths=p, min_daily_volume=1, book=False).run()
    ing.finalize(p)
    cat.build(p, progress=lambda s: None)
    data_store.reset()
    yield {"paths": p, "tapes": tapes}
    data_store.reset()
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def _spec(**over):
    spec = {
        "instrument": {"symbol": "ES1!"}, "timeframes": {"primary": "1min"},
        "session": {"entryWindow": {"start": "09:31", "end": "10:20"}, "flattenAt": "10:25"},
        "rules": {"kind": "test_open_close"},
        "exit": {"stop": {"type": "ticks", "value": 2000}, "target": {"type": "ticks", "value": 2000}},
        "sizing": {"type": "fixed_contracts", "value": 1, "maxContracts": 5},
        "risk": {"accountSize": 100000},
    }
    spec.update(over)
    return spec


def _hand_pnl(tape, d, contracts=1):
    """Entry: the signal bar closes at 09:31:00 and the market order fills at
    the last print known to the venue (+1 tick slippage) — NautilusTrader's
    L1 model fills against the current last price, i.e. the bar close.
    Exit: the flatten fires on the first print at/after 10:25 (-1 tick)."""
    front = tape[tape["symbol"] == "ESM6"].sort_values(["ts_event", "sequence"])
    from engine.session import et_to_ns
    t_entry = et_to_ns(d, "09:31")
    t_exit = et_to_ns(d, "10:25")
    entry = float(front[front["ts_event"] < t_entry]["price"].iloc[-1]) + ES.tick_size
    exit_ = float(front[front["ts_event"] >= t_exit]["price"].iloc[0]) - ES.tick_size
    return entry, exit_, P.net_pnl_usd(entry, exit_, "long", contracts, ES)


def test_ticks_mode_matches_hand_computed_pnl(store):
    res = run_backtest(_spec(), DAYS[0], DAYS[-1], "ticks")
    trades = res["trades"]
    assert len(trades) == 3 and res["meta"]["sessions"] == 3
    for t, d in zip(trades, DAYS):
        entry, exit_, pnl = _hand_pnl(store["tapes"][d], d)
        assert t["direction"] == "long" and t["contracts"] == 1 and t["sessionDate"] == d.isoformat()
        assert t["exitReason"] == "flatten"
        assert t["entryPrice"] == pytest.approx(entry)
        assert t["exitPrice"] == pytest.approx(exit_)
        assert t["commissionUsd"] == pytest.approx(P.commission_usd(1, ES))
        assert t["pnlUsd"] == pytest.approx(pnl, abs=0.01)
        assert t["pnlUsd"] == t["pnl"] and t["reason"] == "flatten" and t["qty"] == 1
        assert t["slippageTicks"] >= 0
        assert t["mfe"] >= 0 and t["mae"] >= 0 and t["barsHeld"] > 0
    assert res["summary"]["trades"] == 3
    daily = res["dailyReturns"]
    assert [d_["date"] for d_ in daily] == [d.isoformat() for d in DAYS]
    assert sum(d_["pnlUsd"] for d_ in daily) == pytest.approx(res["summary"]["totalPnl"], abs=0.01)


def test_bars_mode_agrees_within_slippage(store):
    ticks = run_backtest(_spec(), DAYS[0], DAYS[-1], "ticks")
    bars = run_backtest(_spec(), DAYS[0], DAYS[-1], "bars")
    assert len(bars["trades"]) == 3
    assert bars["meta"]["bars"] > 0 and bars["meta"]["ticks"] == 0
    for a, b in zip(ticks["trades"], bars["trades"]):
        # Same session, same direction; prices within a couple of ticks (bar close vs next print).
        assert a["sessionDate"] == b["sessionDate"] and b["exitReason"] == "flatten"
        assert abs(a["entryPrice"] - b["entryPrice"]) <= 3 * ES.tick_size
        assert abs(a["exitPrice"] - b["exitPrice"]) <= 3 * ES.tick_size
        assert abs(a["pnlUsd"] - b["pnlUsd"]) <= 6 * ES.tick_value


def test_stop_and_target_exits(store):
    tight = _spec(exit={"stop": {"type": "ticks", "value": 16}, "target": {"type": "ticks", "value": 16}})
    for mode in ("ticks", "bars"):
        res = run_backtest(tight, DAYS[0], DAYS[-1], mode)
        reasons = {t["exitReason"] for t in res["trades"]}
        assert reasons <= {"stop", "target", "flatten"} and reasons & {"stop", "target"}, (mode, reasons)
        for t in res["trades"]:
            if t["exitReason"] == "stop":
                assert t["r"] <= -0.9   # stop at level (+ slippage) -> about -1R
            if t["exitReason"] == "target":
                assert t["r"] >= 0.85   # 16-tick target filled one tick worse
            assert t["stopPrice"] is not None and t["targetPrice"] is not None


def test_fixed_risk_sizing_and_daily_loss_halt(store):
    spec = _spec(exit={"stop": {"type": "ticks", "value": 8}, "target": {"type": "rr", "value": 2.0}},
                 sizing={"type": "fixed_risk", "value": 0.5, "maxContracts": 10},
                 risk={"accountSize": 100000, "riskPerTradePct": 0.5, "dailyLossLimitPct": 0.01})
    res = run_backtest(spec, DAYS[0], DAYS[0], "ticks")
    assert res["trades"], "expected at least one trade"
    assert res["trades"][0]["contracts"] == 5      # $500 / (8 ticks × $12.50)
    assert len(res["trades"]) == 1                   # one loss of $10 already breaches a 0.01% daily limit


def test_v1_rules_run_on_new_engine(store):
    v1 = {"name": "v1 sma", "symbol": "ES1!", "direction": "long", "interval": "1min",
          "conditions": [{"type": "consecutive", "count": 2, "color": "green"}],
          "stop": {"type": "fixed_points", "value": 2.0}, "target": {"type": "rr", "value": 1.5},
          "session": {"start": "13:31", "end": "14:20"}, "sizing": {"type": "fixed_qty", "value": 1}}
    res = run_backtest(v1, DAYS[0], DAYS[-1], "bars")
    assert res["trades"], "v1 consecutive-green rule should fire on a trending synthetic day"
    for t in res["trades"]:
        assert t["direction"] == "long" and t["contracts"] == 1
        assert t["stopPrice"] == pytest.approx(t["entryPrice"] - 2.0, abs=0.26)
        # Entries only inside 09:31–10:20 ET (13:31–14:20 UTC in June).
        from engine.session import ns_to_et
        et = ns_to_et(t["entryTime"] * synth.NS)
        assert (9, 31) <= (et.hour, et.minute) <= (10, 20)
