"""Receive-time v2 observations layered on the unchanged v1 quality engine.

T is counted once; F is not another trade. Replenishment means displayed A
following a known-side T at the same price in an earlier complete event within
one second. This excludes same-event matching, M updates and hidden-liquidity
claims. Matching budgets reset each minute; all session accumulators reset per
invocation. No events at/after cutoff are observed.
"""
import pandas as pd
from engine import mbo_features as v1
from engine.feature_schema import RICH_FEATURES

MAX_MATCH_PRICES = 100_000


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
    state = dict(buy=0, sell=0, ask=0, bid=0, repeated=0, open=None, close=None)

    def emit():
        nonlocal boundary, previous_vwap
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
        boundary += v1.MINUTE

    def stream():
        nonlocal numerator, volume
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
                    if item is not None and 0 < t-item[0] <= v1.NS and item[1] > 0:
                        matched = min(int(r["size"]), item[1])
                        item[1] -= matched
                        item[2] += 1
                        state["ask" if key[0] == "A" else "bid"] += matched
                        if item[2] >= 2:
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
                        if item is None or t-item[0] > v1.NS:
                            matches[key] = [t, z, 0]
                        else:
                            item[0], item[1] = t, item[1]+z
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
             "maxReplenishmentPrices": MAX_MATCH_PRICES}
    return result, audit
