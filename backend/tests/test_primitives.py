import pytest

from engine.primitives.base import all_primitives, get_class
from tests.helpers_bars import bar, feed_closes, make_ctx, trade


DAY_ONE = """open high low close volume delta sma ema vwap rsi atr adx bollinger_upper bollinger_lower highest lowest
swing_high swing_low opening_range_high opening_range_low initial_balance_high initial_balance_low session_high
session_low prior_day_high prior_day_low prior_day_close gap_points consecutive candle_pattern poc vah val
volume_at_price profile_shape bar_delta cvd_session cvd_window cvd_slope rel_delta rel_volume delta_divergence
footprint_imbalance stacked_imbalances absorption exhaustion poc_migration large_print large_resting_size_near
resting_size_at book_imbalance time_of_day day_of_week minutes_to_close bars_since_open""".split()


def test_day_one_set_registered():
    names = set(all_primitives())
    missing = set(DAY_ONE) - names
    assert not missing, missing
    for n in DAY_ONE:
        d = get_class(n).describe()
        assert d["doc"] and d["output"] in ("number", "price", "bool", "level")


def test_param_validation():
    with pytest.raises(ValueError):
        get_class("sma")({"period": "x"})
    with pytest.raises(ValueError):
        get_class("sma")({"bogus": 1})
    with pytest.raises(ValueError):
        get_class("consecutive")({"color": "blue"})
    with pytest.raises(ValueError):
        get_class("resting_size_at")({})          # price required
    assert get_class("ema")({"period": 9}).p["period"] == 9


def test_price_family():
    ctx = feed_closes(make_ctx(), [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    assert ctx.value("close") == 20 and ctx.value("open") == 19 and ctx.value("volume") == 100
    assert ctx.value("sma", {"period": 5}) == pytest.approx(18.0)
    assert ctx.value("highest", {"n": 3}) == 19          # excludes the current bar
    assert ctx.value("highest", {"n": 3, "exclude_current": False}) == 20
    assert ctx.value("lowest", {"n": 3}) == 16          # lows of the previous 3 bars: 16, 17, 18
    ema = ctx.value("ema", {"period": 5})
    assert 18 <= ema <= 20          # straight line: EMA seed == SMA
    assert ctx.value("rsi", {"period": 5}) == 100.0      # only gains
    assert ctx.value("atr", {"period": 5}) == pytest.approx(1.0)
    up, lo = ctx.value("bollinger_upper", {"period": 5, "stdev": 2}), ctx.value("bollinger_lower", {"period": 5, "stdev": 2})
    assert up > 18 > lo and up - 18 == pytest.approx(18 - lo)
    assert ctx.value("vwap") == pytest.approx(sum(((b.high + b.low + b.close) / 3) for b in ctx.bars()) / 11)
    assert ctx.value("sma", {"period": 50}) is None


def test_adx_trending_vs_flat():
    trend = feed_closes(make_ctx(), [100 + i for i in range(40)])
    # Expanding zigzag: +DM and -DM alternate one point each -> DX near 0.
    flat = feed_closes(make_ctx(), [100 + (i // 2 + 1) * (1 if i % 2 else -1) for i in range(40)])
    assert trend.value("adx", {"period": 5}) > 50
    assert flat.value("adx", {"period": 5}) < 30
    assert flat.value("adx", {"period": 5}) < trend.value("adx", {"period": 5})


def test_structure_family():
    ctx = make_ctx()
    ctx.on_bar(bar(0, 100, 103, 99, 101))
    ctx.on_bar(bar(1, 101, 102, 100, 102))
    assert ctx.value("opening_range_high", {"minutes": 3}) is None
    ctx.on_bar(bar(2, 102, 102.5, 101, 102))
    assert ctx.value("opening_range_high", {"minutes": 3}) == 103 and ctx.value("opening_range_low", {"minutes": 3}) == 99
    for i in range(3, 8):
        ctx.on_bar(bar(i, 102, 104 + i * 0.25, 101.5, 103))
    assert ctx.value("session_high") == 104 + 7 * 0.25 and ctx.value("session_low") == 99
    assert ctx.value("bars_since_open") == 7 and ctx.value("time_of_day") == 8
    assert ctx.value("minutes_to_close") == 390 - 8
    assert ctx.value("day_of_week") == 2   # 2026-07-15 is a Wednesday
    ctx2 = make_ctx()
    highs = [10, 11, 15, 12, 11, 13, 12]
    for i, h in enumerate(highs):
        ctx2.on_bar(bar(i, h - 1, h, h - 2, h - 0.5))
    assert ctx2.value("swing_high", {"n": 2}) == 15
    assert ctx2.value("swing_low", {"n": 1}) == 9
    ctx3 = feed_closes(make_ctx(), [10, 11, 12, 13])
    assert ctx3.value("consecutive", {"count": 3, "color": "green"}) is True
    assert ctx3.value("consecutive", {"count": 3, "color": "red"}) is False
    ctx4 = make_ctx()
    ctx4.on_bar(bar(0, 10, 10.5, 9, 9.5))
    ctx4.on_bar(bar(1, 9.25, 11, 9, 10.75))
    assert ctx4.value("candle_pattern", {"pattern": "engulfing"}) is True
    ctx4.on_bar(bar(2, 10.5, 10.75, 10.25, 10.6))
    assert ctx4.value("candle_pattern", {"pattern": "inside"}) is True
    ctx4.on_bar(bar(3, 10.6, 10.65, 9.0, 10.55))
    assert ctx4.value("candle_pattern", {"pattern": "pin"}) is True


def test_prior_day_and_gap():
    from datetime import date
    ctx = make_ctx()
    feed_closes(ctx, [100, 101, 102], d=date(2026, 7, 14))
    assert ctx.value("prior_day_high") is None
    ctx.on_bar(bar(0, 105, 106, 104, 105, d=date(2026, 7, 15)))
    assert ctx.value("prior_day_close") == 102 and ctx.value("prior_day_high") == 102 and ctx.value("prior_day_low") == 100
    assert ctx.value("gap_points") == 3


def test_orderflow_family_bars_mode():
    ctx = feed_closes(make_ctx(), [10, 11, 12, 13, 14, 15], deltas=[5, 10, -3, 8, 6, 20])
    assert ctx.value("bar_delta") == 20 and ctx.value("cvd_session") == 46
    assert ctx.value("cvd_window", {"n": 3}) == 34 and ctx.value("cvd_slope", {"n": 3}) == pytest.approx(34 / 3)
    assert ctx.value("rel_delta", {"n": 3}) == pytest.approx(34 / (34 / 3))
    assert ctx.value("rel_volume", {"n": 3}) == 1.0
    assert ctx.value("delta_divergence", {"n": 4}) == 0.0
    diverge = feed_closes(make_ctx(), [10, 11, 12, 13, 14], deltas=[0, -5, -5, -5, -5])
    assert diverge.value("delta_divergence", {"n": 3}) == -1.0


def test_footprint_from_trades():
    ctx = make_ctx()
    ctx.on_bar(bar(0, 100, 100.5, 99.5, 100, v=1))   # tiny bars-mode profile before the first print
    prints = [(100.0, 2, "A"), (100.25, 30, "B"), (100.25, 3, "A"), (100.5, 40, "B"), (100.5, 2, "A"), (100.75, 35, "B"), (100.75, 1, "A"), (101.0, 60, "B")]
    for k, (p, s, side) in enumerate(prints):
        ctx.on_trade(trade(60 + k, p, s, side))
    ctx.on_bar(bar(1, 100, 101, 100, 101, v=sum(s for _, s, _ in prints), delta=165 - 8))
    fp = ctx.footprints[-1][1]
    assert fp[100.25] == [3.0, 30.0] and fp[101.0] == [0.0, 60.0]
    assert ctx.value("footprint_imbalance", {"side": "ask", "ratio": 3.0, "min_volume": 5}) == 4
    assert ctx.value("stacked_imbalances", {"side": "ask", "count": 3}) is True
    assert ctx.value("stacked_imbalances", {"side": "bid", "count": 2}) is False
    assert ctx.value("large_print", {"min_size": 50, "within_bars": 2}) == 60
    assert ctx.value("poc") == 101.0
    assert ctx.value("volume_at_price", {"price": 100.5, "ticks": 0}) == pytest.approx(42.2)   # + 0.2 from the tiny first bar
    assert ctx.value("absorption", {"side": "ask", "min_volume": 100, "max_range_ticks": 4}) is True
    assert ctx.value("absorption", {"side": "ask", "min_volume": 100, "max_range_ticks": 2}) is False
    ctx.on_trade(trade(130, 101.25, 1, "B"))
    ctx.on_bar(bar(2, 101, 101.25, 101, 101.25, v=1, delta=1))
    assert ctx.value("poc_migration", {"n": 2}) == 1.0


def test_profile_and_book():
    ctx = make_ctx()
    for i in range(5):
        ctx.on_bar(bar(i, 100, 101, 99, 100.5, v=100))
    poc, vah, val = ctx.value("poc"), ctx.value("vah"), ctx.value("val")
    assert val <= poc <= vah
    assert ctx.value("profile_shape") in (-1.0, 0.0, 1.0)
    assert ctx.value("book_imbalance") is None
    ctx.set_book([(100.0, 50), (99.75, 300)], [(100.25, 20), (100.5, 25)])
    assert ctx.value("book_imbalance", {"levels": 2}) == pytest.approx(350 / 45)
    assert ctx.value("resting_size_at", {"price": 99.75, "side": "bid"}) == 300
    assert ctx.value("large_resting_size_near", {"side": "bid", "min_size": 200, "within_ticks": 4}) == 300
    assert ctx.value("large_resting_size_near", {"side": "ask", "min_size": 200, "within_ticks": 4}) == 0.0


def test_context_timeframe_uses_closed_bars_only():
    ctx = make_ctx("1min", ["5min"])
    for i in range(7):
        ctx.on_bar(bar(i, 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i + 0.25))
    five = ctx.bars("5min")
    assert len(five) == 1 and five[0].close == 104.25 and five[0].high == 104.5 and five[0].low == 99.5
    assert ctx.value("close", tf="5min") == 104.25
    assert ctx.value("sma", {"period": 1}, tf="5min") == 104.25
    with pytest.raises(ValueError):
        ctx.value("close", tf="15min")


def test_snapshot_feature_vector():
    ctx = feed_closes(make_ctx(), [100 + i * 0.25 for i in range(30)])
    snap = ctx.snapshot()
    assert "close" in snap and "sma" in snap and "resting_size_at" not in snap
    assert snap["close"] == 100 + 29 * 0.25
