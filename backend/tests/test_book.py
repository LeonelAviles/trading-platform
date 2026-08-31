"""L3 book vs the brute-force reference; warm-cache checkpoints round-trip."""

import numpy as np
import pytest

from replay import warm
from replay.book import L3Book
from replay.sources import FrameSource
from tests import synth

NS = 1_000_000_000


def _feed(book: L3Book, mbo, symbol, upto_ns):
    sub = mbo[(mbo["symbol"] == symbol) & (mbo["ts_recv"] <= upto_ns)]
    prices = np.round(sub["price"].to_numpy() * 1e9).astype("int64")
    book.apply_arrays(sub["ts_recv"].to_numpy(), sub["action"].tolist(), sub["side"].tolist(), prices,
                      sub["size"].to_numpy(), sub["order_id"].to_numpy())


def _as_float(levels):
    return [(round(p / 1e9, 4), v) for p, v in levels]


@pytest.mark.parametrize("minute", [1, 7, 19, 29])
def test_book_matches_brute_force(synth_mbo, minute):
    start = int(synth_mbo["ts_recv"].min())
    at = start + minute * 60 * NS
    book = L3Book()
    _feed(book, synth_mbo, "ESM6", at)
    ref_bids, ref_asks = synth.book_at(synth_mbo, "ESM6", at, depth=10)
    bids, asks = book.top_nanos(10)
    assert _as_float(bids) == [(round(p, 4), v) for p, v in ref_bids]
    assert _as_float(asks) == [(round(p, 4), v) for p, v in ref_asks]


def test_snapshot_restore_roundtrip(synth_mbo):
    at = int(synth_mbo["ts_recv"].min()) + 10 * 60 * NS
    book = L3Book()
    _feed(book, synth_mbo, "ESM6", at)
    snap = book.snapshot()
    other = L3Book()
    other.restore(snap)
    assert other.levels == book.levels
    assert other.orders == book.orders
    assert len(other) == len(book)


def test_actions_modify_cancel_clear():
    b = L3Book()
    b.apply("A", "B", 100_000_000_000, 5, 1)
    b.apply("A", "B", 100_000_000_000, 3, 2)
    b.apply("A", "A", 100_250_000_000, 7, 3)
    assert b.levels[("B", 100_000_000_000)] == 8
    b.apply("M", "B", 99_750_000_000, 4, 1)         # moved + resized
    assert b.levels[("B", 100_000_000_000)] == 3
    assert b.levels[("B", 99_750_000_000)] == 4
    b.apply("C", "B", 100_000_000_000, 1, 2)        # partial cancel
    assert b.levels[("B", 100_000_000_000)] == 2
    b.apply("C", "B", 100_000_000_000, 5, 2)        # over-cancel removes the order
    assert ("B", 100_000_000_000) not in b.levels
    b.apply("T", "A", 100_250_000_000, 2, 0)        # trades never touch resting size here
    b.apply("F", "A", 100_250_000_000, 2, 3)
    assert b.levels[("A", 100_250_000_000)] == 7
    assert b.best() == (99.75, 100.25)
    b.apply("R", "N", 0, 0, 0)
    assert len(b) == 0 and not b.levels


def test_warm_checkpoints_seek_matches_reference(tmp_path, synth_mbo):
    from market import paths as mpaths

    p = mpaths.Paths(data_dir=tmp_path / "data", market_data_dir=tmp_path / "md", replay_cache_max_gb=1.0)
    p.ensure_dirs()
    warm.CHECKPOINT_MIN_EVENTS, saved = 2000, warm.CHECKPOINT_MIN_EVENTS
    try:
        frames = (synth_mbo.iloc[i:i + 5000] for i in range(0, len(synth_mbo), 5000))
        meta = warm.warm_day("ES", synth_mbo["ts_recv"].min() // NS and str(FrameSource(synth_mbo, "ESM6").day),
                             paths=p, frames=frames, expected=len(synth_mbo))
    finally:
        warm.CHECKPOINT_MIN_EVENTS = saved
    assert meta["events"] == len(synth_mbo)
    assert meta["front"] == "ESM6"
    assert len(meta["checkpoints"]["ESM6"]) >= 3
    assert warm.is_cached("ES", meta["date"], p)

    at = int(synth_mbo["ts_recv"].min()) + 17 * 60 * NS + 123_456_789
    start, snap = warm.load_checkpoint("ES", meta["date"], "ESM6", at, p)
    assert start is not None and start <= at
    book = L3Book()
    book.restore(snap)
    sub = synth_mbo[(synth_mbo["symbol"] == "ESM6") & (synth_mbo["ts_recv"] >= start) & (synth_mbo["ts_recv"] <= at)]
    book.apply_arrays(sub["ts_recv"].to_numpy(), sub["action"].tolist(), sub["side"].tolist(),
                      np.round(sub["price"].to_numpy() * 1e9).astype("int64"), sub["size"].to_numpy(),
                      sub["order_id"].to_numpy())
    ref_bids, ref_asks = synth.book_at(synth_mbo, "ESM6", at, depth=10)
    assert _as_float(book.top_nanos(10)[0]) == [(round(p_, 4), v) for p_, v in ref_bids]
    assert _as_float(book.top_nanos(10)[1]) == [(round(p_, 4), v) for p_, v in ref_asks]

    listed = warm.list_cached(p)
    assert [(d["root"], d["date"]) for d in listed] == [("ES", meta["date"])]
    # LRU eviction with a zero cap removes it
    p2 = mpaths.Paths(data_dir=p.data_dir, market_data_dir=p.market_data_dir, replay_cache_max_gb=0.0)
    assert warm.evict(p2) == [("ES", meta["date"])]
    assert not warm.is_cached("ES", meta["date"], p)
