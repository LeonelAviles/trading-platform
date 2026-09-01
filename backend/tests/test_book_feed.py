"""Backtest book view from the one-second liquidity store (engine/book_feed.py)."""

from datetime import date

import pytest

import liquidity_store
from engine import book_feed
from engine.session import NS

DAY = date(2026, 4, 14)
T0 = 1_776_124_800            # 2026-04-14 00:00:00 UTC, epoch seconds


@pytest.fixture()
def liq_db(tmp_path, monkeypatch):
    path = tmp_path / "liq.duckdb"
    monkeypatch.setattr(liquidity_store, "default_db_path", lambda: path)
    con = liquidity_store.get_connection(read_only=False, path=path)
    rows = [
        # ts, price_nanos, side, size — a bid of 300 at 100.00 appears in second 10, grows in 11, is cleared in 20
        (T0 + 10, 100_000_000_000, "B", 300),
        (T0 + 10, 100_250_000_000, "A", 80),
        (T0 + 11, 100_000_000_000, "B", 450),
        (T0 + 11, 99_500_000_000, "B", 60),
        (T0 + 20, 100_000_000_000, "B", 0),
    ]
    con.executemany("INSERT INTO liquidity_changes_1s VALUES (?, ?, ?, ?, ?, ?)", [(DAY, "ESM6", *r) for r in rows])
    con.execute("INSERT INTO liquidity_files VALUES ('f.dbn.zst', ?, 'ESM6', 50, ?, now())", [DAY, len(rows)])
    con.close()
    yield path


def test_feed_replays_levels_without_lookahead(liq_db):
    feed = book_feed.LiquidityBookFeed.load("ESM6", DAY)
    assert feed is not None and feed.n == 5
    # A close in second 10 sees only rows from seconds < 10: nothing yet.
    feed.advance((T0 + 10) * NS)
    assert feed.view(100.0) == ([], [])
    # Second 11 close: the end-of-second-10 state is known.
    feed.advance((T0 + 11) * NS)
    bids, asks = feed.view(100.0)
    assert bids == [(100.0, 300.0)] and asks == [(100.25, 80.0)]
    # Second 12: the level grew and a second bid appeared; bids come high→low.
    feed.advance((T0 + 12) * NS)
    bids, _ = feed.view(100.0)
    assert bids == [(100.0, 450.0), (99.5, 60.0)]
    # A zero row closes the level.
    feed.advance((T0 + 21) * NS)
    bids, _ = feed.view(100.0)
    assert bids == [(99.5, 60.0)]
    # advance is monotone: going back in time changes nothing.
    feed.advance((T0 + 5) * NS)
    assert feed.view(100.0)[0] == [(99.5, 60.0)]


def test_view_keeps_the_levels_nearest_the_last_price(liq_db):
    feed = book_feed.LiquidityBookFeed.load("ESM6", DAY)
    feed.advance((T0 + 12) * NS)
    bids, _ = feed.view(99.5, depth=1)
    assert bids == [(99.5, 60.0)]


def test_feed_drives_the_book_primitive(liq_db):
    from engine.features import FeatureContext

    ctx = FeatureContext("1min", tick_size=0.25)
    feed = book_feed.LiquidityBookFeed.load("ESM6", DAY)
    feed.advance((T0 + 12) * NS)
    ctx.set_book(*feed.view(100.0))
    ctx.last_price = 100.0
    assert ctx.value("large_resting_size_near", {"side": "bid", "min_size": 200, "within_ticks": 5}) == 450.0
    assert ctx.value("large_resting_size_near", {"side": "bid", "min_size": 500, "within_ticks": 5}) == 0.0


def test_covered_days_and_missing_store(liq_db, monkeypatch):
    assert book_feed.covered_days("ESM6", [DAY, date(2026, 4, 15)]) == {DAY}
    assert book_feed.covered_days("NQM6", [DAY]) == set()
    monkeypatch.setattr(liquidity_store, "default_db_path", lambda: liq_db.parent / "absent.duckdb")
    assert book_feed.covered_days("ESM6", [DAY]) == set()
    assert book_feed.LiquidityBookFeed.load("ESM6", DAY) is None


def test_book_primitives_in_spec():
    spec = {"entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"field": "open"}]},
                      "sequence": [{"when": {"op": "gte", "args": [{"ind": "absorption", "params": {}}, 1]}}]},
            "filters": [{"op": "gt", "args": [{"ind": "large_resting_size_near", "params": {"side": "bid"}}, 0]}]}
    assert book_feed.book_primitives_in(spec) == ["large_resting_size_near"]
    spec["filters"] = []
    assert book_feed.book_primitives_in(spec) == []
