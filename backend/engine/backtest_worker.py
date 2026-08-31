"""Backtest worker — subprocess (PLATFORM-SPEC.md §4.3, §5 Phase 2 task 2).

    python -m engine.backtest_worker <spec.json> <date_from> <date_to> <mode> <out.json>

A pure function: spec + date window → trades. Builds a NautilusTrader
`BacktestEngine` (venue SIM, margin account, netting, `PerContractFeeModel`,
`FillModel`), streams the catalog one session at a time (memory stays flat
on the 8.6 GB machine), runs `ExecStrategy` — the execution layer: sessions
in ET, entry window / no-trade windows / forced flatten, stop & target,
time stop, sizing, daily loss limit, cooldown, consecutive-loss halt, max
trades per day — fed by a `RuleSource` (engine/rules.py), and writes the v2
trade records plus per-session returns.

Modes
  bars   1-minute catalog bars (higher timeframes aggregated in-engine);
         entries at the signal bar's close ± 1 tick; stop/target evaluated
         intrabar against the next bars' high/low, worst case first (stop wins).
  ticks  TradeTick stream with in-engine bar aggregation; market orders fill
         at the venue's current last price (the print that closed the signal
         bar) ± 1 tick, stops on the first print at/through the trigger,
         targets are resting limits (filled one tick worse by the same
         FillModel, which approximates the conservative trade-through rule).
  l3     falls back to `ticks` until Phase 5 writes OrderBookDelta for
         replay-cached days (recorded in meta.note).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.instruments import load_instruments  # noqa: E402
from engine import instruments as ins_mod  # noqa: E402
from engine import pnl as P  # noqa: E402
from engine.ledger import Ledger, OpenTrade, daily_returns  # noqa: E402
from engine.rules import Bar, build_rules  # noqa: E402
from engine.session import NS, et_to_ns, hhmm_add_minutes, session_date  # noqa: E402
from market import catalog as cat  # noqa: E402
from market.paths import get_paths  # noqa: E402

TF_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60, "4h": 240, "1D": 1440}


# ----------------------------------------------------------------------------
# Spec normalisation (Phase 2 subset of the v2 schema; Phase 3 validates fully)
# ----------------------------------------------------------------------------

def exec_params(spec: dict, as_of: date | None = None) -> dict:
    ins = load_instruments()
    s = spec.get("session") or {}
    rth_start, rth_end = ins.session.rth_start, ins.session.rth_end
    flatten_default = hhmm_add_minutes(rth_end, -ins.session.flatten_before_close_minutes)
    ew = s.get("entryWindow")
    if ew is None and "start" in s and "end" in s:
        # Legacy v1 session: UTC wall clock. Convert on the run's first date
        # (v1 strategies were built on Apr–Jul data, i.e. EDT) and clamp to RTH.
        from engine.session import utc_hhmm_to_et, parse_hhmm

        d = as_of or date.today()
        a, b = utc_hhmm_to_et(s["start"], d), utc_hhmm_to_et(s["end"], d)
        a = max(a, rth_start, key=parse_hhmm)
        b = min(b, flatten_default, key=parse_hhmm)
        ew = {"start": a, "end": b}
    ew = ew or {"start": rth_start, "end": s.get("flattenAt", flatten_default)}
    risk = spec.get("risk") or {}
    sizing = spec.get("sizing") or {"type": "fixed_contracts", "value": 1, "maxContracts": 5}
    exit_ = spec.get("exit") or {}
    if not exit_ and "stop" in spec:
        # Legacy v1: {"stop": {"type": percent|fixed_points|atr, "value"|"mult", "period"}, "target": {...}}
        st = dict(spec["stop"])
        if st.get("type") == "atr":
            st = {"type": "atr", "value": float(st.get("mult", st.get("value", 1.5))), "period": int(st.get("period", 14))}
        exit_ = {"stop": st, "target": dict(spec.get("target") or {"type": "rr", "value": 2.0})}
    return {
        "symbol": (spec.get("instrument") or {}).get("symbol") or spec.get("symbol"),
        "primary": (spec.get("timeframes") or {}).get("primary") or spec.get("interval") or "1min",
        "entry_start": ew["start"], "entry_end": ew["end"],
        "no_trade": [(w["start"], w["end"]) for w in (s.get("noTradeWindows") or [])],
        "flatten_at": s.get("flattenAt") or flatten_default,
        "stop": exit_.get("stop") or {"type": "ticks", "value": 20},
        "target": exit_.get("target") or {"type": "rr", "value": 2.0},
        "time_stop_bars": (exit_.get("timeStop") or {}).get("bars"),
        "sizing": sizing,
        "account_size": float(risk.get("accountSize", 100_000)),
        "risk_pct": float(risk.get("riskPerTradePct", sizing.get("value", 0.5) if sizing.get("type") == "fixed_risk" else 0.5)),
        "max_contracts": int(sizing.get("maxContracts", risk.get("maxContracts", 5))),
        "daily_loss_pct": risk.get("dailyLossLimitPct"),
        "max_trades_per_day": (spec.get("constraints") or {}).get("maxTradesPerDay", risk.get("maxTradesPerDay")),
        "cooldown_bars": int((spec.get("constraints") or {}).get("cooldownBars", 0) or 0),
        "stop_after_losses": (spec.get("constraints") or {}).get("stopAfterConsecutiveLosses", risk.get("stopAfterConsecutiveLosses")),
        "order_type": (spec.get("entry") or {}).get("orderType", "market"),
        "limit_offset_ticks": int((spec.get("entry") or {}).get("limitOffsetTicks", 0) or 0),
        "stop_offset_ticks": int((spec.get("entry") or {}).get("stopOffsetTicks", 1) or 1),
        "entry_timeout_bars": int((spec.get("entry") or {}).get("timeoutBars", 3) or 3),
        "trailing": exit_.get("trailing"),
        "breakeven": exit_.get("breakeven"),
        "scale_out": list(exit_.get("scaleOut") or []),
        "slippage_ticks": (spec.get("execution") or {}).get("slippageTicksOverride"),
    }


# ----------------------------------------------------------------------------
# The execution strategy
# ----------------------------------------------------------------------------

def _make_strategy_class():
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import OrderSide, PositionSide
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    class ExecStrategy(Strategy):
        def configure(self, *, params: dict, rules, instrument, mode: str, cspec: P.ContractSpec,
                      flow_sidecar: dict | None, ledger: Ledger, slippage_ticks: int):
            self.p = params
            self.rules = rules
            self.inst = instrument
            self.mode = mode
            self.cspec = cspec
            self.flow = flow_sidecar or {}
            self.ledger = ledger
            self.slip = slippage_ticks
            self.primary_min = TF_MINUTES[params["primary"]]
            self.bar_index = -1
            self.open: OpenTrade | None = None
            self.pending_entry: dict | None = None
            self.exit_ids: dict[str, str] = {}      # client_order_id -> reason
            self.scale_cids: set[str] = set()
            self.pending_exit_reason: str | None = None
            self.pending_exit_ref: float | None = None
            self.session: date | None = None
            self.session_pnl = 0.0
            self.session_trades = 0
            self.consec_losses = 0
            self.last_exit_bar = -10**9
            self.halted_for_day = False
            self.tick_buy = 0
            self.tick_sell = 0
            self.tick_high = None
            self.tick_low = None
            self.last_price = None
            self.bar_type = None
            self.stats = {"signals": 0, "blocked": 0}

        # -- lifecycle ------------------------------------------------------
        def on_start(self):
            iid = str(self.inst.id)
            if self.mode == "bars":
                bt = f"{iid}-1-MINUTE-LAST-EXTERNAL" if self.primary_min == 1 else \
                     f"{iid}-{self.primary_min}-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
            else:
                bt = f"{iid}-{self.primary_min}-MINUTE-LAST-INTERNAL"
                self.subscribe_trade_ticks(self.inst.id)
            self.bar_type = BarType.from_str(bt)
            self.subscribe_bars(self.bar_type)

        # -- helpers --------------------------------------------------------
        def _et_ns(self, d: date, hhmm: str) -> int:
            return et_to_ns(d, hhmm)

        def _new_session(self, d: date):
            self.session = d
            self.session_pnl = 0.0
            self.session_trades = 0
            self.consec_losses = 0
            self.halted_for_day = False

        def _entry_allowed(self, ts: int, d: date) -> bool:
            if not (self._et_ns(d, self.p["entry_start"]) <= ts < self._et_ns(d, self.p["entry_end"])):
                return False
            if ts >= self._et_ns(d, self.p["flatten_at"]):
                return False
            for a, b in self.p["no_trade"]:
                if self._et_ns(d, a) <= ts < self._et_ns(d, b):
                    return False
            if self.halted_for_day:
                return False
            if self.p["max_trades_per_day"] and self.session_trades >= int(self.p["max_trades_per_day"]):
                return False
            if self.bar_index - self.last_exit_bar < self.p["cooldown_bars"]:
                return False
            return True

        def _levels(self, direction: str, ref: float, bar: Bar) -> tuple[float | None, float | None]:
            """Stop/target for an entry referenced at `ref`. Distance-based
            types are re-anchored to the actual fill price once it is known
            (`_reanchor`); rule-provided structure levels are absolute."""
            override = self.rules.stop_target(direction, ref, bar)
            if override is not None:
                self._levels_absolute = True
                return override
            self._levels_absolute = False
            st, tg = self.p["stop"], self.p["target"]
            sign = P.direction_sign(direction)
            t = st.get("type")
            if t == "ticks":
                dist = float(st["value"]) * self.cspec.tick_size
            elif t in ("points", "fixed_points"):
                dist = float(st["value"])
            elif t == "percent":
                dist = ref * float(st["value"]) / 100.0
            elif t == "atr":
                atr = self.rules.atr(int(st.get("period", 14)))
                if atr is None:
                    return None, None
                dist = atr * float(st.get("value", st.get("mult", 1.5)))
            else:
                dist = 20 * self.cspec.tick_size
            stop = P.round_to_tick(ref - sign * dist, self.cspec)
            risk = abs(ref - stop)
            tt = tg.get("type")
            if tt == "rr":
                tdist = risk * float(tg["value"])
            elif tt == "ticks":
                tdist = float(tg["value"]) * self.cspec.tick_size
            elif tt in ("points", "fixed_points"):
                tdist = float(tg["value"])
            elif tt == "percent":
                tdist = ref * float(tg["value"]) / 100.0
            else:
                tdist = risk * 2.0
            target = P.round_to_tick(ref + sign * tdist, self.cspec)
            return stop, target

        def _size(self, ref: float, stop: float | None) -> int:
            sz = self.p["sizing"]
            if sz.get("type") in ("fixed_contracts", "fixed_qty"):
                return max(1, min(int(sz.get("value", 1)), self.p["max_contracts"]))
            stop_ticks = abs(ref - stop) / self.cspec.tick_size if stop is not None else 0
            if sz.get("type") == "percent_equity":   # legacy v1: treat as fixed risk at 0.5%
                return P.contracts_fixed_risk(self.p["account_size"], 0.5, stop_ticks, self.cspec, self.p["max_contracts"])
            return P.contracts_fixed_risk(self.p["account_size"], self.p["risk_pct"], stop_ticks, self.cspec, self.p["max_contracts"])

        def _bar_flow(self, bar) -> tuple[float, float, float, float]:
            """(volume, delta, buy, sell) for a closed primary bar."""
            if self.mode != "bars":
                v, b, s = self.tick_buy + self.tick_sell, self.tick_buy, self.tick_sell
                self.tick_buy = self.tick_sell = 0
                return float(v), float(b - s), float(b), float(s)
            close_ns = int(bar.ts_event)
            total = [0.0, 0.0, 0.0, 0.0]
            for k in range(self.primary_min):
                row = self.flow.get(close_ns - (self.primary_min - k) * 60 * NS)
                if row:
                    total[0] += row[0]
                    total[1] += row[1]
                    total[2] += row[2]
                    total[3] += row[3]
            if not any(total):
                total[0] = float(bar.volume)
            return tuple(total)

        # -- ticks ----------------------------------------------------------
        def on_trade_tick(self, tick):
            px = float(tick.price)
            self.last_price = px
            side = tick.aggressor_side.name
            if side == "BUYER":
                self.tick_buy += int(tick.size)
            else:
                self.tick_sell += int(tick.size)
            self.tick_high = px if self.tick_high is None else max(self.tick_high, px)
            self.tick_low = px if self.tick_low is None else min(self.tick_low, px)
            if hasattr(self.rules, "on_trade"):
                self.rules.on_trade(int(tick.ts_event), px, int(tick.size), "B" if side == "BUYER" else "A" if side == "SELLER" else "N")
            if self.open is not None:
                self.open.observe(px, px)
                d = self.session or session_date(int(tick.ts_event))
                if int(tick.ts_event) >= self._et_ns(d, self.p["flatten_at"]) and self.pending_exit_reason is None:
                    self._flatten("flatten", px)
                elif self.pending_exit_reason is None:
                    self._manage_stops(px, px, tick_mode=True)
            if self.pending_entry is not None and self.pending_entry.get("resting") and self.pending_exit_reason is None:
                self._check_entry_timeout(int(tick.ts_event))

        # -- bars -----------------------------------------------------------
        def on_bar(self, bar):
            if bar.bar_type != self.bar_type:
                return
            self.bar_index += 1
            ts = int(bar.ts_event)
            d = session_date(ts)
            if d != self.session:
                self._new_session(d)
            vol, delta, buy, sell = self._bar_flow(bar)
            b = Bar(float(bar.open), float(bar.high), float(bar.low), float(bar.close), vol, delta, buy, sell,
                    ts, ts - self.primary_min * 60 * NS, self.bar_index)
            self.tick_high = self.tick_low = None
            self.rules.on_bar(b)

            if self.open is not None:
                self._manage_open(b, d)
            if self.pending_entry is not None and self.pending_entry.get("resting"):
                self._check_entry_timeout(b.ts_close, bar_index=b.index)
            if self.open is None and self.pending_entry is None and self.pending_exit_reason is None:
                self._maybe_enter(b, d)

        def _manage_open(self, b: Bar, d: date):
            t = self.open
            if self.mode == "bars":
                t.observe(b.high, b.low)
                if self.pending_exit_reason is not None:
                    return
                sign = P.direction_sign(t.direction)
                hit_stop = t.stop_price is not None and ((b.low <= t.stop_price) if sign > 0 else (b.high >= t.stop_price))
                hit_target = t.target_price is not None and ((b.high >= t.target_price) if sign > 0 else (b.low <= t.target_price))
                if hit_stop:      # worst case first: stop wins when both are touched
                    self._synthetic_exit(b, P.apply_slippage(t.stop_price, t.direction, self.slip, self.cspec, entering=False), "stop", t.stop_price)
                    return
                if hit_target:
                    self._synthetic_exit(b, t.target_price, "target", t.target_price)
                    return
            if self.pending_exit_reason is not None:
                return
            if self.mode == "bars":
                self._manage_stops(b.high, b.low, tick_mode=False)
            if self.p["time_stop_bars"] and b.index - t.entry_bar_index >= int(self.p["time_stop_bars"]):
                self._flatten("time_stop", b.close, b)
                return
            if b.ts_close >= self._et_ns(d, self.p["flatten_at"]) or b.ts_close >= self._et_ns(d, "16:00"):
                self._flatten("flatten", b.close, b)

        def _maybe_enter(self, b: Bar, d: date):
            direction = self.rules.signal(b)
            if direction is None:
                return
            self.stats["signals"] += 1
            if not self._entry_allowed(b.ts_close, d):
                self.stats["blocked"] += 1
                return
            stop, target = self._levels(direction, b.close, b)
            if stop is None and target is None and self.p["stop"].get("type") == "structure":
                self.stats["blocked"] += 1      # structure level unavailable or on the wrong side
                return
            qty = self._size(b.close, stop)
            side = OrderSide.BUY if direction == "long" else OrderSide.SELL
            sign = 1 if direction == "long" else -1
            otype = self.p["order_type"] if self.mode != "bars" else "market"
            if otype == "limit":
                px = P.round_to_tick(b.close + sign * self.p["limit_offset_ticks"] * self.cspec.tick_size, self.cspec)
                order = self.order_factory.limit(self.inst.id, side, Quantity.from_int(qty), price=Price.from_str(f"{px:.2f}"), tags=["entry"])
            elif otype == "stop":
                px = P.round_to_tick(b.close + sign * self.p["stop_offset_ticks"] * self.cspec.tick_size, self.cspec)
                order = self.order_factory.stop_market(self.inst.id, side, Quantity.from_int(qty), trigger_price=Price.from_str(f"{px:.2f}"), tags=["entry"])
            else:
                order = self.order_factory.market(self.inst.id, side, Quantity.from_int(qty), tags=["entry"])
            self.pending_entry = {"direction": direction, "ref": b.close, "stop": stop, "target": target,
                                  "bar_index": b.index, "qty": qty, "cid": str(order.client_order_id),
                                  "absolute": self._levels_absolute, "bar": b, "resting": otype != "market",
                                  "expires_bar": b.index + self.p["entry_timeout_bars"]}
            self.submit_order(order)

        def _check_entry_timeout(self, ts: int, bar_index: int | None = None):
            pe = self.pending_entry
            if pe is None or not pe.get("resting"):
                return
            idx = bar_index if bar_index is not None else self.bar_index
            if idx >= pe["expires_bar"] and self.open is None:
                o = self.cache.order(self._cid(pe["cid"]))
                if o is not None and o.is_open:
                    self.cancel_order(o)
                self.pending_entry = None
                self.stats["blocked"] += 1

        def _manage_stops(self, high: float, low: float, tick_mode: bool):
            """Breakeven, trailing stop and scale-outs on the open trade."""
            t = self.open
            if t is None or t.stop_price is None:
                return
            sign = P.direction_sign(t.direction)
            risk = abs(t.entry_price - t.stop_price) if t.initial_risk is None else t.initial_risk
            if t.initial_risk is None:
                t.initial_risk = risk
            if risk <= 0:
                return
            fav = (high - t.entry_price) * sign if sign > 0 else (t.entry_price - low)
            r_now = fav / risk
            new_stop = None
            be = self.p["breakeven"]
            if be and not t.breakeven_done and r_now >= float(be.get("atR", 1.0)):
                new_stop = t.entry_price + sign * int(be.get("offsetTicks", 1)) * self.cspec.tick_size
                t.breakeven_done = True
            tr = self.p["trailing"]
            if tr and r_now >= float(tr.get("activateAtR", 1.0)):
                if tr.get("type") == "atr":
                    a = self.rules.atr(int(tr.get("period", 14)))
                    dist = (a or 0) * float(tr.get("value", 2.0))
                else:
                    dist = float(tr.get("value", 8)) * self.cspec.tick_size
                extreme = t.mfe_price
                cand = extreme - sign * dist if dist > 0 else None
                if cand is not None and (new_stop is None or (cand - new_stop) * sign > 0):
                    new_stop = cand
            if new_stop is not None and (new_stop - t.stop_price) * sign > 0:
                t.stop_price = P.round_to_tick(new_stop, self.cspec)
                self._replace_stop_order(t)
            for i, so in enumerate(self.p["scale_out"]):
                if i in t.scaled and t.contracts > 1:
                    continue
                if i not in t.scaled and r_now >= float(so.get("atR", 1.0)) and t.contracts > 1:
                    n = max(1, int(round(t.contracts * float(so.get("fraction", 0.5)))))
                    if n < t.contracts:
                        t.scaled.add(i)
                        self._scale_out(t, n, high if sign > 0 else low)

        def _replace_stop_order(self, t: OpenTrade):
            if self.mode == "bars":
                return
            for cid, reason in list(self.exit_ids.items()):
                if reason == "stop":
                    o = self.cache.order(self._cid(cid))
                    if o is not None and o.is_open:
                        self.cancel_order(o)
                    self.exit_ids.pop(cid, None)
            side = OrderSide.SELL if t.direction == "long" else OrderSide.BUY
            o = self.order_factory.stop_market(self.inst.id, side, Quantity.from_int(t.contracts), trigger_price=Price.from_str(f"{t.stop_price:.2f}"),
                                               reduce_only=True, tags=["stop"])
            self.exit_ids[str(o.client_order_id)] = "stop"
            self.submit_order(o)

        def _scale_out(self, t: OpenTrade, n: int, ref_price: float):
            """Book a partial exit as its own trade record; reduce the open trade."""
            exit_px = ref_price
            if self.mode == "bars":
                exit_px = P.apply_slippage(ref_price, t.direction, self.slip, self.cspec, entering=False)
            part = OpenTrade(direction=t.direction, contracts=n, entry_ts=t.entry_ts, entry_price=t.entry_price, ref_price=t.ref_price,
                             stop_price=t.stop_price, target_price=t.target_price, entry_bar_index=t.entry_bar_index)
            part.mae_price, part.mfe_price = t.mae_price, t.mfe_price
            part.commission = P.commission_usd(n, self.cspec)
            rec = self.ledger.close(part, self.clock.timestamp_ns(), exit_px, "scale_out", self.bar_index, exit_ref_price=ref_price)
            self.session_pnl += rec["pnlUsd"]
            t.contracts -= n
            t.commission = max(0.0, t.commission - self.cspec.commission_per_side * n)
            side = OrderSide.SELL if t.direction == "long" else OrderSide.BUY
            so = self.order_factory.market(self.inst.id, side, Quantity.from_int(n), reduce_only=True, tags=["scale"])
            self.scale_cids.add(str(so.client_order_id))
            self.submit_order(so)
            if self.mode != "bars":
                # resize the brackets
                for cid in list(self.exit_ids):
                    o = self.cache.order(self._cid(cid))
                    if o is not None and o.is_open:
                        self.cancel_order(o)
                self.exit_ids.clear()
                self._place_brackets(t)

        # -- exits ----------------------------------------------------------
        def _synthetic_exit(self, b: Bar, price: float, reason: str, ref: float):
            """Bars mode: book the exit at the level (worst case), flatten Nautilus at market."""
            self.pending_exit_reason = reason
            self.pending_exit_ref = ref
            self._synthetic_price = price
            self._synthetic_ts = b.ts_close
            self.close_all_positions(self.inst.id)

        def _flatten(self, reason: str, ref_price: float, b: Bar | None = None):
            self.pending_exit_reason = reason
            self.pending_exit_ref = ref_price
            self._synthetic_price = ref_price if self.mode == "bars" else None
            self._synthetic_ts = b.ts_close if b is not None else None
            for cid in list(self.exit_ids):
                o = self.cache.order(self._cid(cid))
                if o is not None and o.is_open:
                    self.cancel_order(o)
            self.close_all_positions(self.inst.id)

        def _cid(self, s: str):
            from nautilus_trader.model.identifiers import ClientOrderId

            return ClientOrderId(s)

        def _place_brackets(self, t: OpenTrade):
            side = OrderSide.SELL if t.direction == "long" else OrderSide.BUY
            qty = Quantity.from_int(t.contracts)
            if t.stop_price is not None:
                o = self.order_factory.stop_market(self.inst.id, side, qty, trigger_price=Price.from_str(f"{t.stop_price:.2f}"),
                                                   reduce_only=True, tags=["stop"])
                self.exit_ids[str(o.client_order_id)] = "stop"
                self.submit_order(o)
            if t.target_price is not None:
                o = self.order_factory.limit(self.inst.id, side, qty, price=Price.from_str(f"{t.target_price:.2f}"),
                                             reduce_only=True, tags=["target"])
                self.exit_ids[str(o.client_order_id)] = "target"
                self.submit_order(o)

        # -- events ---------------------------------------------------------
        def on_order_filled(self, ev):
            cid = str(ev.client_order_id)
            comm = float(ev.commission.as_double()) if ev.commission is not None else 0.0
            if self.pending_entry is not None and cid == self.pending_entry["cid"]:
                pe = self.pending_entry
                if self.open is None:
                    self.open = OpenTrade(direction=pe["direction"], contracts=int(ev.last_qty), entry_ts=int(ev.ts_event),
                                          entry_price=float(ev.last_px), ref_price=pe["ref"], stop_price=pe["stop"],
                                          target_price=pe["target"], entry_bar_index=pe["bar_index"])
                    self.open.commission += comm
                    self.session_trades += 1
                    if self.mode == "bars":
                        self.open.observe(float(ev.last_px), float(ev.last_px))
                else:  # partial fills accumulate
                    o = self.open
                    tot = o.contracts + int(ev.last_qty)
                    o.entry_price = (o.entry_price * o.contracts + float(ev.last_px) * int(ev.last_qty)) / tot
                    o.contracts = tot
                    o.commission += comm
                # Brackets only once the whole entry is in: a market order can fill
                # across several prints, and a stop sized on the first partial fill
                # would leave the rest of the position unprotected.
                order = self.cache.order(ev.client_order_id)
                if order is not None and int(order.leaves_qty) == 0:
                    if not pe.get("absolute"):
                        stop, target = self._levels(self.open.direction, self.open.entry_price, pe["bar"])
                        self.open.stop_price, self.open.target_price = stop, target
                    self.pending_entry = None
                    if self.mode != "bars":
                        self._place_brackets(self.open)
                return
            if cid in self.exit_ids and self.open is not None:
                self.open.commission += comm
                if self.pending_exit_reason is None:
                    self.pending_exit_reason = self.exit_ids[cid]
                    self.pending_exit_ref = self.open.stop_price if self.exit_ids[cid] == "stop" else self.open.target_price
                    # cancel the sibling
                    for other, _ in list(self.exit_ids.items()):
                        if other != cid:
                            o = self.cache.order(self._cid(other))
                            if o is not None and o.is_open:
                                self.cancel_order(o)
                return
            if cid in self.scale_cids:
                return                      # partial exit: commission already booked on its own record
            if self.open is not None:       # flatten / synthetic market close
                self.open.commission += comm

        def on_position_opened(self, ev):
            pass

        def on_position_changed(self, ev):
            pass

        def on_position_closed(self, ev):
            t = self.open
            if t is None:
                return
            reason = self.pending_exit_reason or "unknown"
            if self.mode == "bars" and getattr(self, "_synthetic_price", None) is not None:
                exit_px, exit_ts, ref = self._synthetic_price, self._synthetic_ts or int(ev.ts_closed), self.pending_exit_ref
            else:
                exit_px, exit_ts, ref = float(ev.avg_px_close), int(ev.ts_closed), self.pending_exit_ref
            t.entry_price = float(ev.avg_px_open)
            rec = self.ledger.close(t, exit_ts, exit_px, reason, self.bar_index, exit_ref_price=ref,
                                    commission=t.commission if t.commission else None)
            self.session_pnl += rec["pnlUsd"]
            self.last_exit_bar = self.bar_index
            self.consec_losses = self.consec_losses + 1 if rec["pnlUsd"] < 0 else 0
            if self.p["stop_after_losses"] and self.consec_losses >= int(self.p["stop_after_losses"]):
                self.halted_for_day = True
            if self.p["daily_loss_pct"] and self.session_pnl <= -float(self.p["daily_loss_pct"]) / 100 * self.p["account_size"]:
                self.halted_for_day = True
            self.open = None
            self.pending_entry = None
            self.pending_exit_reason = None
            self.pending_exit_ref = None
            self._synthetic_price = None
            self.exit_ids.clear()

        def on_stop(self):
            if self.open is not None and self.last_price is not None:
                # end of data with an open position: book at the last price
                t = self.open
                self.ledger.close(t, int(self.clock.timestamp_ns()), self.last_price, "end_of_data", self.bar_index)
                self.open = None

    return ExecStrategy


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

def _flow_sidecar(root: str, d: date, symbol: str) -> dict:
    """{bar close ns: (volume, delta, buy, sell)} from the bars_1m partition."""
    import duckdb

    part = get_paths().partition(get_paths().bars_1m_dir, root, d.isoformat()) / "part.parquet"
    if not part.exists():
        return {}
    con = duckdb.connect()
    try:
        rows = con.execute("SELECT ts, volume, delta, buy_vol, sell_vol FROM read_parquet(?) WHERE symbol = ?",
                           [str(part), symbol]).fetchall()
    finally:
        con.close()
    return {int(ts) + 60 * NS: (float(v), float(dl), float(b), float(s)) for ts, v, dl, b, s in rows}


def _regime_tags(root: str) -> dict:
    import duckdb

    p = get_paths().regimes
    if not p.exists():
        return {}
    con = duckdb.connect()
    try:
        rows = con.execute("SELECT date, trend, vol, day_type FROM read_parquet(?) WHERE root = ?", [str(p), root]).fetchall()
    finally:
        con.close()
    return {(r[0] if isinstance(r[0], date) else r[0].date()).isoformat(): [r[1], f"vol_{r[2]}", r[3]] for r in rows}


def run_backtest(spec: dict, date_from: date, date_to: date, mode: str = "ticks", progress=None) -> dict:
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.backtest.models import FillModel, PerContractFeeModel
    from nautilus_trader.config import DataEngineConfig, LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money

    t0 = time.time()
    params = exec_params(spec, date_from)
    ins = load_instruments()
    root = ins_mod.root_spec(params["symbol"])
    cspec = P.ContractSpec.from_root(root)
    note = None
    if mode == "l3":
        note = "l3 mode falls back to ticks until OrderBookDelta data exists (Phase 5)"
        mode = "ticks"
    slippage = ins.costs.slippage_ticks_market if params["slippage_ticks"] is None else int(params["slippage_ticks"])
    ranges = ins_mod.resolve_ranges(params["symbol"], date_from, date_to)
    session_dates = [d.isoformat() for r in ranges for d in r.dates]
    ledger = Ledger(cspec, _regime_tags(root.root))
    catalog = cat.open_catalog()
    rules = build_rules(spec)
    ExecStrategy = _make_strategy_class()

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="BACKTEST-001", logging=LoggingConfig(log_level="ERROR"),
        data_engine=DataEngineConfig(time_bars_build_with_no_updates=False),
    ))
    engine.add_venue(
        venue=Venue(cat.VENUE), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
        base_currency=USD, starting_balances=[Money(params["account_size"], USD)],
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0 if slippage >= 1 else 0.0, random_seed=1),
        fee_model=PerContractFeeModel(commission=Money(root.commission_per_side, USD)),
    )
    bars_total = ticks_total = 0
    try:
        for rng in ranges:
            inst = catalog.instruments(instrument_ids=[cat.instrument_id(rng.symbol)])
            if not inst:
                raise RuntimeError(f"instrument {rng.symbol} not in catalog — run scripts/build_catalog.py")
            inst = inst[0]
            engine.add_instrument(inst)
            strat = ExecStrategy()
            strat.configure(params=params, rules=rules, instrument=inst, mode=mode, cspec=cspec,
                            flow_sidecar=None, ledger=ledger, slippage_ticks=slippage)
            engine.add_strategy(strat)
            for d in rng.dates:
                start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
                end = start.replace(hour=23, minute=59, second=59)
                if mode == "bars":
                    strat.flow.update(_flow_sidecar(root.root, d, rng.symbol))
                    data = catalog.bars(bar_types=[cat.bar_type_str(rng.symbol)], start=start, end=end)
                    bars_total += len(data)
                else:
                    data = catalog.trade_ticks(instrument_ids=[str(inst.id)], start=start, end=end)
                    ticks_total += len(data)
                if not data:
                    continue
                engine.add_data(data)
                engine.run(streaming=True)
                engine.clear_data()
                if progress:
                    progress(d.isoformat())
            engine.end()
            # A new contract range needs a fresh strategy instance (Nautilus binds a
            # strategy to its subscriptions); the rules object carries over so
            # indicators stay warm across a roll.
            engine.reset()
    finally:
        engine.dispose()

    trades = ledger.trades
    daily = daily_returns(trades, session_dates, params["account_size"])
    wins = [t for t in trades if t["pnlUsd"] > 0]
    return {
        "trades": trades,
        "dailyReturns": daily,
        "summary": {
            "trades": len(trades), "wins": len(wins),
            "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "totalPnl": round(sum(t["pnlUsd"] for t in trades), 2),
            "commission": round(sum(t["commissionUsd"] for t in trades), 2),
        },
        "meta": {
            "symbol": params["symbol"], "mode": mode, "dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(),
            "sessions": len(session_dates), "contracts": [r.symbol for r in ranges], "bars": bars_total, "ticks": ticks_total,
            "slippageTicks": slippage, "commissionPerSide": root.commission_per_side, "accountSize": params["account_size"],
            "seconds": round(time.time() - t0, 1), "note": note, "signals": None,
        },
    }


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 5:
        print(__doc__)
        return 2
    spec_path, date_from, date_to, mode, out_path = argv
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    result = run_backtest(spec, date.fromisoformat(date_from), date.fromisoformat(date_to), mode,
                          progress=lambda d: print(f"session {d} done", flush=True))
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")
    print(f"done: {result['summary']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
