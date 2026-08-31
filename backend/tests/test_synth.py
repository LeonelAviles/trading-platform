"""The synthetic session must be internally consistent: tape, bars and
book all derive from the same event stream."""

from tests import synth


def test_session_shape(synth_cfg, synth_mbo):
    t0, t1 = synth.session_bounds_ns(synth_cfg)
    assert set(synth_mbo["symbol"]) == set(synth_cfg.symbols)
    assert set(synth_mbo["action"]) <= {"A", "C", "F", "T"}
    in_session = synth_mbo[synth_mbo["action"] == "T"]
    assert in_session["ts_event"].between(t0, t1).all()
    # Sorted by (ts_recv, sequence) and sequence strictly increasing.
    assert synth_mbo["sequence"].is_monotonic_increasing
    assert (synth_mbo["ts_recv"].diff().fillna(0) >= 0).all()


def test_determinism(synth_cfg, synth_mbo):
    again = synth.generate_mbo(synth_cfg)
    assert len(again) == len(synth_mbo)
    assert (again["price"].values == synth_mbo["price"].values).all()


def test_prices_on_tick_grid(synth_cfg, synth_trades):
    ticks = synth_trades["price"] / synth_cfg.tick_size
    assert ((ticks - ticks.round()).abs() < 1e-9).all()


def test_front_month_by_volume(synth_cfg, synth_trades):
    vol = synth_trades.groupby("symbol")["size"].sum()
    assert vol.idxmax() == synth_cfg.symbols[0]


def test_bars_agree_with_trades(synth_trades, synth_bars):
    front = synth_trades[synth_trades["symbol"] == "ESM6"]
    bars = synth_bars[synth_bars["symbol"] == "ESM6"]
    assert bars["volume"].sum() == front["size"].sum()
    assert (bars["buy_vol"] + bars["sell_vol"] == bars["volume"]).all()
    assert (bars["delta"] == bars["buy_vol"] - bars["sell_vol"]).all()
    assert (bars["high"] >= bars["low"]).all()
    assert (bars["high"] >= bars[["open", "close"]].max(axis=1)).all()
    # 30-minute session, at least 25 of the 30 minutes should print.
    assert len(bars) >= 25


def test_book_reference_is_plausible(synth_cfg, synth_mbo):
    t0, t1 = synth.session_bounds_ns(synth_cfg)
    bids, asks = synth.book_at(synth_mbo, "ESM6", (t0 + t1) // 2, depth=5)
    assert bids and asks
    assert bids[0][0] < asks[0][0], "best bid must sit below best ask"
    assert all(v > 0 for _, v in bids + asks)
    # Fills only ever reduce or remove resting orders; never negative sizes.
    fills = synth_mbo[synth_mbo["action"] == "F"]
    assert (fills["size"] > 0).all()
