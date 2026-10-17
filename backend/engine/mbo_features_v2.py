"""Receive-time v2 observations layered on the unchanged v1 quality engine.

T is counted once; F is not another trade. Replenishment means displayed A
following a known-side T at the same price in an earlier complete event within
one second. This excludes same-event matching, M updates and hidden-liquidity
claims. Matching budgets reset each minute; all session accumulators reset per
invocation. No events at/after cutoff are observed.
"""
from collections import deque

import pandas as pd
from engine import mbo_features as v1
from engine.feature_schema import RICH_FEATURES

MAX_MATCH_PRICES = 100_000
MAX_MATCH_LOTS = 100_000


def build_minutes(records, **settings):
    start, end, source = settings["start_ns"], settings["end_ns"], settings["source"]
    tick = settings["tick_size"]
    if tick <= 0:
        raise ValueError("positive tick size required")
    boundary = start + v1.MINUTE
    numerator = volume = 0
    previous_vwap = None
    observed = {}
    matches = {}
    matching_lots = 0
    state = dict(buy=0, sell=0, ask=0, bid=0, repeated=0, open=None, close=None)

    def emit():
        nonlocal boundary, previous_vwap, matching_lots
        vwap = numerator / volume / v1.NS if volume else None
        if state["open"] is not None and vwap is not None:
            response = (state["close"] - state["open"]) / tick
            buy_denom, sell_denom = 1 + max(response, 0), 1 + max(-response, 0)
            known = state["buy"] + state["sell"]
            observed[boundary - v1.MINUTE] = [vwap, (state["close"]-vwap)/tick,
                0 if previous_vwap is None else (vwap-previous_vwap)/tick,
                (state["buy"]-state["sell"])/60,
                (state["buy"]-state["sell"])/known if known else 0,
                state["buy"]/buy_denom, state["sell"]/sell_denom,
                state["ask"]/60, state["bid"]/60,
                state["ask"]/buy_denom, state["bid"]/sell_denom,
                state["repeated"]/known if known else 0]
        previous_vwap = vwap
        state.update(buy=0, sell=0, ask=0, bid=0, repeated=0, open=None, close=None)
        matches.clear()
        matching_lots = 0
        boundary += v1.MINUTE

    def expire(item, t):
        nonlocal matching_lots
        while item["lots"] and item["lots"][0][0] < t-v1.NS:
            item["lots"].popleft()
            matching_lots -= 1

    def stream():
        nonlocal numerator, volume, matching_lots
        pending = []
        for record in records:
            if tuple(int(record[k]) for k in ("instrument_id", "publisher_id", "channel_id")) != (
                    source.instrumentId, source.publisherId, source.channelId):
                continue
            t = int(record["ts_recv"])
            if t >= end:
                break
            pending.append(record)
            if len(pending) > 100_000:
                raise ValueError("MBO event exceeds bounded record budget")
            if not int(record["flags"]) & 128:
                continue
            while boundary <= t and boundary <= end:
                emit()
            if t >= start and not int(record["flags"]) & 32:
                # Adds match only earlier events, never trades from this event.
                for r in pending:
                    if r["action"] != "A":
                        continue
                    key = (str(r["side"]), int(r["price"]))
                    item = matches.get(key)
                    if item is None:
                        continue
                    expire(item, t)
                    remaining, matched = int(r["size"]), 0
                    while remaining > 0 and item["lots"] and item["lots"][0][0] < t:
                        lot = item["lots"][0]
                        quantity = min(remaining, lot[1])
                        remaining -= quantity
                        matched += quantity
                        lot[1] -= quantity
                        if lot[1] == 0:
                            item["lots"].popleft()
                            matching_lots -= 1
                    if matched:
                        # Count an observed A once, even when it consumes
                        # several eligible trade lots.
                        item["adds"] += 1
                        state["ask" if key[0] == "A" else "bid"] += matched
                        if item["adds"] >= 2:
                            state["repeated"] += matched
                for r in pending:
                    if r["action"] != "T":
                        continue
                    p, z, side = int(r["price"]), int(r["size"]), str(r["side"])
                    numerator += p*z
                    volume += z
                    price = p/v1.NS
                    if state["open"] is None:
                        state["open"] = price
                    state["close"] = price
                    if side in ("A", "B"):
                        state["buy" if side == "B" else "sell"] += z
                        key = ("A" if side == "B" else "B", p)
                        item = matches.get(key)
                        if item is None or t-item["last"] > v1.NS:
                            if item is not None:
                                matching_lots -= len(item["lots"])
                            item = {"last": t, "lots": deque(), "adds": 0}
                            matches[key] = item
                        else:
                            expire(item, t)
                            item["last"] = t
                        if item["lots"] and item["lots"][-1][0] == t:
                            item["lots"][-1][1] += z
                        else:
                            item["lots"].append([t, z])
                            matching_lots += 1
                        if matching_lots > MAX_MATCH_LOTS:
                            raise ValueError("replenishment credit-lot budget exceeded")
                        if len(matches) > MAX_MATCH_PRICES:
                            raise ValueError("replenishment price budget exceeded")
            yield from pending
            pending = []
        # Let v1 see the incomplete event and reject it, rather than discard it.
        yield from pending

    frame, audit = v1.build_minutes(stream(), **settings)
    while boundary <= end:
        emit()
    extras = pd.DataFrame.from_dict(observed, orient="index", columns=RICH_FEATURES[len(v1.FEATURES):]).astype(float)
    extras.index = pd.to_datetime(extras.index, unit="ns", utc=True)
    if not frame.index.isin(extras.index).all():
        raise ValueError("v2 observations missing a validated minute")
    result = frame.join(extras, how="left")
    audit = {**audit, "replenishmentPolicy": "earlier_complete_event_same_price_1s_capped_A_only_proxy",
             "sessionVWAPPolicy": "RTH_T_volume_including_unknown_and_omitted_minutes",
             "maxReplenishmentPrices": MAX_MATCH_PRICES,
             "maxReplenishmentCreditLots": MAX_MATCH_LOTS}
    return result, audit
