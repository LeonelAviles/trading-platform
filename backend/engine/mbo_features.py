"""Causal receive-time MBO features; full order state, no heatmap filtering.

Complete F_LAST events become visible atomically. A minute is [open, close):
records at close belong to the next minute. T/F do not mutate resting orders.
C reduction includes execution-related removals, not just voluntary cancels.
"""
from __future__ import annotations

import pandas as pd
from replay.book import L3Book, INT64_NULL_PRICE

FEATURES = ["spread_ticks", "depth_imbalance", "aggressive_buy_rate", "aggressive_sell_rate",
            "add_rate", "cancel_reduction_rate"]
FEATURE_VERSION = "mbo_receive_minute_v1"
NS = 1_000_000_000
MINUTE = 60 * NS
MAX_BOOK_ORDERS = 500_000
REQUIRED = ["ts_recv", "ts_event", "publisher_id", "instrument_id", "channel_id", "symbol",
            "action", "side", "price", "size", "order_id", "sequence", "flags"]


def build_minutes(records, *, start_ns, end_ns, contract, source, depth, max_age_ms,
                  max_lag_ms, max_unknown_fraction, tick_size):
    """Stream a single source day from its opening snapshot through end_ns.

    Fail closed on corrupt/unsupported records, gaps flagged by the vendor,
    regressing receive times/sequences, missing snapshots or incomplete events.
    Sequence continuity cannot be proved from an instrument-filtered feed.
    Missing/stale/crossed books and no-trade minutes are omitted, never filled;
    chronological label construction purges those gaps downstream.
    """
    book = L3Book()
    pending = []
    initialized = False
    last_recv = last_seq = last_book = None
    boundary = start_ns + MINUTE
    stats = dict(buy=0, sell=0, unknown=0, adds=0, cancels=0, open=None, high=None, low=None, close=None)
    rows, times = [], []
    audit = dict(records=0, snapshots=0, lateRecords=0, unknownTradeVolume=0,
                 omittedMinutes=0, completeEvents=0,
                 sequenceCheck="monotonic_per_instrument_not_full_channel_gap_audit")

    def emit():
        bids, asks = book.top_nanos(depth)
        total = stats["buy"] + stats["sell"] + stats["unknown"]
        good = (initialized and bids and asks and bids[0][0] < asks[0][0]
                and last_book is not None and boundary - last_book <= max_age_ms * 1_000_000
                and total > 0)
        if total and stats["unknown"] / total > max_unknown_fraction:
            raise ValueError("unknown aggressor volume exceeds approved quality limit")
        if good:
            b, a = sum(z for _, z in bids), sum(z for _, z in asks)
            times.append(boundary - MINUTE)
            rows.append([stats["open"], stats["high"], stats["low"], stats["close"], total,
                         (asks[0][0] - bids[0][0]) / (tick_size * NS), (b-a)/(b+a),
                         stats["buy"]/60, stats["sell"]/60, stats["adds"]/60, stats["cancels"]/60])
        else:
            audit["omittedMinutes"] += 1
        stats.update(buy=0, sell=0, unknown=0, adds=0, cancels=0, open=None, high=None, low=None, close=None)

    for r in records:
        # Do not inspect records outside this source or beyond the cutoff.
        if (int(r["instrument_id"]), int(r["publisher_id"]), int(r["channel_id"])) != (
                source.instrumentId, source.publisherId, source.channelId):
            continue
        t = int(r["ts_recv"])
        if t >= end_ns:
            break
        if r["symbol"] != contract:
            raise ValueError("raw symbol does not match approved source identifiers")
        if last_recv is not None and t < last_recv:
            raise ValueError("MBO receive timestamps regress")
        last_recv = t
        if audit["records"] + len(pending) >= 50_000_000:
            raise ValueError("source day exceeds 50 million MBO record budget")
        flags, action, side = int(r["flags"]), str(r["action"]), str(r["side"])
        snap = bool(flags & 32)
        # BAD_TS_RECV is normal for synthetic vendor snapshots, never trades.
        if flags & (64 | 16 | 4) or (flags & 8 and not snap):
            raise ValueError("unsupported or bad MBO book/timestamp flags")
        if action not in "AMCTFNR" or len(action) != 1 or side not in ("A", "B", "N"):
            raise ValueError("unsupported MBO action/side")
        seq = int(r["sequence"])
        if not snap:
            if last_seq is not None and seq < last_seq:
                raise ValueError("MBO sequence regressed without a snapshot")
            last_seq = seq
            lag = t - int(r["ts_event"])
            if abs(lag) > max_lag_ms * 1_000_000:
                raise ValueError("event/receive clock difference exceeds approved quality limit")
            audit["lateRecords"] += int(lag >= MINUTE)
        pending.append(r)
        if len(pending) > 100_000:
            raise ValueError("MBO event exceeds bounded record budget")
        if not flags & 128:
            continue
        # An incomplete event crossing a boundary only affects the later row.
        while boundary <= t and boundary <= end_ns:
            emit()
            boundary += MINUTE
        if any(bool(int(x["flags"]) & 32) != snap for x in pending):
            raise ValueError("mixed snapshot and ordinary records in one event")
        if snap:
            if pending[0]["action"] != "R" or any(x["action"] != "A" for x in pending[1:]):
                raise ValueError("snapshot must start with reset and contain only adds")
            if t >= start_ns:
                raise ValueError("intraday snapshot/reset requires a separate reviewed dataset")
            audit["snapshots"] += 1
            initialized = True
            last_seq = None
        elif not initialized:
            raise ValueError("complete initial MBO snapshot is required")
        for x in pending:
            a, s = str(x["action"]), str(x["side"])
            p, z, oid = int(x["price"]), int(x["size"]), int(x["order_id"])
            if z < 0 or (a in "AMTF" and (p <= 0 or p == INT64_NULL_PRICE or z <= 0)):
                raise ValueError("invalid MBO price/size")
            if a in "AMTF" and p % round(tick_size * NS):
                raise ValueError("MBO price is off the configured ES tick grid")
            if a in "AMC" and s not in ("A", "B"):
                raise ValueError("book mutation requires a resting side")
            if a == "R" and not snap:
                raise ValueError("unsnapshotted book reset")
            old = book.orders.get(oid)
            if a == "A" and old is not None:
                raise ValueError("duplicate resting order")
            if a in "MC" and (old is None or (a == "C" and (z <= 0 or z > old[2] or s != old[0] or p != old[1]))):
                raise ValueError("unknown or inconsistent order modification/reduction")
            if start_ns <= t < end_ns and not snap:
                if a == "T":
                    key = "buy" if s == "B" else "sell" if s == "A" else "unknown"
                    stats[key] += z
                    price = p / NS
                    if stats["open"] is None:
                        stats.update(open=price, high=price, low=price)
                    stats.update(high=max(stats["high"], price), low=min(stats["low"], price), close=price)
                    audit["unknownTradeVolume"] += z if s == "N" else 0
                elif a == "A":
                    stats["adds"] += 1
                elif a == "C":
                    stats["cancels"] += z
            if book.apply(a, s, p, z, oid) or a == "R":
                last_book = int(x["ts_recv"])
            if len(book) > MAX_BOOK_ORDERS:
                raise ValueError("resting order book exceeds memory budget")
        audit["records"] += len(pending)
        audit["completeEvents"] += 1
        pending = []
    if pending:
        raise ValueError("incomplete terminal MBO event")
    while boundary <= end_ns:
        emit()
        boundary += MINUTE
    if not initialized:
        raise ValueError("no approved source snapshot found")
    return pd.DataFrame(rows, index=pd.to_datetime(times, unit="ns", utc=True),
                        columns=["open", "high", "low", "close", "volume", *FEATURES]), audit
