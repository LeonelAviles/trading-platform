"""Expression-tree evaluator (PLATFORM-SPEC.md §4.4).

    { "op": "and", "args": [ {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]}, ... ] }

Leaves: numbers, `{"ind": name, "params": {...}, "tf"?}`, `{"field": open|high|low|close|volume|delta, "tf"?}`.
Operators: and, or, not, gt, gte, lt, lte, eq, between(x, lo, hi), cross_above(a, b), cross_below(a, b),
rising(x, bars), falling(x, bars), within_ticks(a, b, n), touched(level, toleranceTicks, withinBars),
held_above(level, bars), held_below(level, bars), bars_since(expr) <cmp> n, retest(level, toleranceTicks,
withinBars) — `retest` = price broke `level` in the trade direction, came back to within `toleranceTicks`
of it, and the current bar closed back on the breakout side.

Stateful nodes keep their own history; `Evaluator.on_bar()` advances every
node once per primary bar close, `Evaluator.eval()` reads the current value
(cheap, so trade-updated filters can be re-read between bars). Every node's
state is per expression instance and per direction — a `direction: both`
strategy compiles the mirrored tree separately (`mirror()`).
"""

from __future__ import annotations

from collections import deque
from typing import Any

from engine.primitives.base import get_class

FIELDS = ("open", "high", "low", "close", "volume", "delta")
COMPARE_OPS = {"gt", "gte", "lt", "lte", "eq"}
LOGIC_OPS = {"and", "or", "not"}
STATEFUL_OPS = {"cross_above", "cross_below", "rising", "falling", "touched", "held_above", "held_below", "bars_since", "retest"}
OTHER_OPS = {"between", "within_ticks"}
OPS = COMPARE_OPS | LOGIC_OPS | STATEFUL_OPS | OTHER_OPS
ARITY = {"and": (2, None), "or": (2, None), "not": (1, 1), "gt": (2, 2), "gte": (2, 2), "lt": (2, 2), "lte": (2, 2),
         "eq": (2, 2), "between": (3, 3), "cross_above": (2, 2), "cross_below": (2, 2), "rising": (2, 2),
         "falling": (2, 2), "within_ticks": (3, 3), "touched": (3, 3), "held_above": (2, 2), "held_below": (2, 2),
         "bars_since": (1, 1), "retest": (3, 3)}
_MIRROR_OP = {"gt": "lt", "lt": "gt", "gte": "lte", "lte": "gte", "cross_above": "cross_below", "cross_below": "cross_above",
              "rising": "falling", "falling": "rising", "held_above": "held_below", "held_below": "held_above"}
_MIRROR_FIELD = {"high": "low", "low": "high"}


class ExprError(ValueError):
    pass


# ----------------------------------------------------------------------------
# Static analysis
# ----------------------------------------------------------------------------

def is_leaf(e) -> bool:
    return isinstance(e, (int, float, bool)) or (isinstance(e, dict) and ("ind" in e or "field" in e))


def walk(e, out: list | None = None) -> list:
    """Every node in evaluation order."""
    out = [] if out is None else out
    out.append(e)
    if isinstance(e, dict) and "op" in e:
        for a in e.get("args", []):
            walk(a, out)
    return out


def referenced_primitives(e) -> list[tuple[str, dict, str | None]]:
    out = []
    for n in walk(e):
        if isinstance(n, dict) and "ind" in n:
            params = dict(n.get("params") or {})
            tf = n.get("tf") or params.pop("tf", None)
            out.append((n["ind"], params, tf))
    return out


def check(e, timeframes: list[str], path: str = "expr") -> list[str]:
    """Human-readable problems with an expression tree (no evaluation)."""
    errs: list[str] = []
    if isinstance(e, bool) or isinstance(e, (int, float)):
        return errs
    if not isinstance(e, dict):
        return [f"{path}: expected a number, a leaf or an operator node, got {type(e).__name__}"]
    if "ind" in e:
        name = e["ind"]
        try:
            cls = get_class(name)
        except KeyError:
            return [f"{path}: unknown primitive '{name}'"]
        params = dict(e.get("params") or {})
        tf = e.get("tf") or params.pop("tf", None)
        try:
            cls(params, tf)
        except ValueError as ex:
            errs.append(f"{path}: {ex}")
        if tf is not None:
            if tf not in timeframes:
                errs.append(f"{path}: tf '{tf}' is not in timeframes {timeframes}")
            elif not cls.tf_capable and tf != timeframes[0]:
                errs.append(f"{path}: primitive '{name}' cannot run on a context timeframe")
        return errs
    if "field" in e:
        if e["field"] not in FIELDS:
            errs.append(f"{path}: unknown field '{e['field']}' (use {'|'.join(FIELDS)})")
        if e.get("tf") and e["tf"] not in timeframes:
            errs.append(f"{path}: tf '{e['tf']}' is not in timeframes {timeframes}")
        return errs
    if "op" not in e:
        return [f"{path}: node needs 'op', 'ind' or 'field'"]
    op = e["op"]
    if op not in OPS:
        return [f"{path}: unknown operator '{op}'"]
    args = e.get("args")
    if not isinstance(args, list):
        return [f"{path}: '{op}' needs an args list"]
    lo, hi = ARITY[op]
    if len(args) < lo or (hi is not None and len(args) > hi):
        errs.append(f"{path}: '{op}' takes {lo}{'+' if hi is None else '' if lo == hi else f'–{hi}'} argument(s), got {len(args)}")
    for i, a in enumerate(args):
        sub = f"{path}.{op}[{i}]"
        # Count-like arguments must be plain numbers.
        if (op in ("rising", "falling", "held_above", "held_below") and i == 1) or \
           (op == "within_ticks" and i == 2) or (op in ("touched", "retest") and i >= 1):
            if not isinstance(a, (int, float)) or isinstance(a, bool):
                errs.append(f"{sub}: must be a number")
            continue
        if op in LOGIC_OPS or op == "bars_since":
            if is_leaf(a) and not isinstance(a, bool):
                errs.append(f"{sub}: '{op}' expects a boolean sub-expression, not a value")
        errs.extend(check(a, timeframes, sub))
    return errs


def mirror(e):
    """The short-side reading of a long-side tree (PLATFORM-SPEC.md §4.4, `direction: both`)."""
    if isinstance(e, (int, float, bool)):
        return e
    if "field" in e:
        return {**e, "field": _MIRROR_FIELD.get(e["field"], e["field"])}
    if "ind" in e:
        cls = get_class(e["ind"])
        out = dict(e)
        if cls.mirror_name:
            out["ind"] = cls.mirror_name
        params = dict(e.get("params") or {})
        if "side" in params and params["side"] in ("bid", "ask"):
            params["side"] = "ask" if params["side"] == "bid" else "bid"
        if "color" in params and params["color"] in ("green", "red"):
            params["color"] = "red" if params["color"] == "green" else "green"
        if params:
            out["params"] = params
        return out
    op, args = e["op"], e.get("args", [])
    if op in COMPARE_OPS or op in ("cross_above", "cross_below", "rising", "falling", "held_above", "held_below"):
        kinds = [_mirror_kind(a) for a in args[:2]]
        directional = any(k in ("price", "signed", "rsi") for k in kinds)
        new_args = [_mirror_arg(a, kinds) for a in args[:2]] + [mirror(a) for a in args[2:]]
        new_op = _MIRROR_OP.get(op, op) if directional else op
        return {"op": new_op, "args": new_args}
    if op == "between":
        k = _mirror_kind(args[0])
        if k in ("signed", "rsi"):
            x, lo, hi = args
            return {"op": "between", "args": [mirror(x), _mirror_const(hi, k), _mirror_const(lo, k)]}
        return {"op": op, "args": [mirror(a) for a in args]}
    return {"op": op, "args": [mirror(a) for a in args]}


def _mirror_kind(a) -> str:
    if isinstance(a, (int, float, bool)):
        return "const"
    if "field" in a:
        return "price" if a["field"] in ("open", "high", "low", "close") else "signed" if a["field"] == "delta" else "none"
    if "ind" in a:
        return get_class(a["ind"]).mirror
    kinds = {_mirror_kind(x) for x in a.get("args", [])}
    for k in ("price", "signed", "rsi"):
        if k in kinds:
            return k
    return "none"


def _mirror_const(c, kind: str):
    if kind == "signed":
        return -c
    if kind == "rsi":
        return 100 - c
    return c


def _mirror_arg(a, kinds):
    if isinstance(a, (int, float, bool)):
        other = [k for k in kinds if k != "const"]
        return _mirror_const(a, other[0]) if other else a
    return mirror(a)


# ----------------------------------------------------------------------------
# Compiled nodes
# ----------------------------------------------------------------------------

class Node:
    def on_bar(self):
        pass

    def eval(self):
        raise NotImplementedError


class Const(Node):
    def __init__(self, v):
        self.v = v

    def eval(self):
        return self.v


class Ind(Node):
    def __init__(self, ctx, name, params, tf):
        self.ctx = ctx
        self.inst = ctx.primitive(name, params, tf)

    def eval(self):
        return self.inst.value(self.ctx)


class Field(Node):
    def __init__(self, ctx, field, tf):
        self.ctx, self.field, self.tf = ctx, field, tf

    def eval(self):
        b = self.ctx.series[self.tf or self.ctx.primary_tf].last()
        return None if b is None else getattr(b, self.field)


def _cmp(op, a, b):
    if a is None or b is None:
        return None
    if op == "gt":
        return a > b
    if op == "gte":
        return a >= b
    if op == "lt":
        return a < b
    if op == "lte":
        return a <= b
    return abs(a - b) < 1e-9


class Op(Node):
    def __init__(self, op, args: list[Node], ctx):
        self.op, self.args, self.ctx = op, args, ctx
        self.hist: deque = deque(maxlen=64)
        self.state: dict[str, Any] = {}

    def on_bar(self):
        for a in self.args:
            a.on_bar()
        op = self.op
        if op in ("cross_above", "cross_below", "rising", "falling"):
            self.hist.append((self.args[0].eval(), self.args[1].eval() if op.startswith("cross") else None))
        elif op in ("held_above", "held_below"):
            lvl, x = self.args[0].eval(), self.ctx.bar
            if x is None or lvl is None:
                self.state["run"] = 0
            else:
                ok = x.close > lvl if op == "held_above" else x.close < lvl
                self.state["run"] = self.state.get("run", 0) + 1 if ok else 0
        elif op == "bars_since":
            v = self.args[0].eval()
            n = self.state.get("n")
            self.state["n"] = 0 if v else (None if n is None else n + 1)
        elif op == "touched":
            self._touched_step()
        elif op == "retest":
            self._retest_step()

    def _touched_step(self):
        lvl = self.args[0].eval()
        tol = self.args[1].eval() * self.ctx.tick
        b = self.ctx.bar
        n = self.state.get("n")
        if lvl is not None and b is not None and (b.low - tol <= lvl <= b.high + tol):
            self.state["n"] = 0
        else:
            self.state["n"] = None if n is None else n + 1

    def _retest_step(self):
        """State: idle -> broke (close beyond level) -> returned (came within tol) -> fires when a bar
        closes back beyond the level within `within` bars of the break; long side reading (mirror handles short)."""
        lvl = self.args[0].eval()
        tol = self.args[1].eval() * self.ctx.tick
        within = int(self.args[2].eval())
        b = self.ctx.bar
        st = self.state
        st["fire"] = False
        if lvl is None or b is None:
            return
        if st.get("phase") in ("broke", "returned"):
            st["age"] = st.get("age", 0) + 1
            if st["age"] > within:
                st["phase"] = None
        if st.get("phase") is None:
            if b.close > lvl and b.open <= lvl:
                st["phase"], st["age"] = "broke", 0
            elif b.close > lvl and self._prev_close is not None and self._prev_close <= lvl:
                st["phase"], st["age"] = "broke", 0
        elif st["phase"] == "broke":
            if b.low <= lvl + tol:
                st["phase"] = "returned"
                if b.close > lvl:
                    st["fire"] = True
                    st["phase"] = None
        elif st["phase"] == "returned":
            if b.close > lvl:
                st["fire"] = True
                st["phase"] = None
            elif b.close < lvl - tol:
                st["phase"] = None
        self._prev_close = b.close

    _prev_close = None

    def eval(self):
        op = self.op
        if op == "and":
            vals = [a.eval() for a in self.args]
            return False if any(v is False or v is None for v in vals) else True
        if op == "or":
            vals = [a.eval() for a in self.args]
            return True if any(v is True for v in vals) else False
        if op == "not":
            v = self.args[0].eval()
            return None if v is None else not v
        if op in COMPARE_OPS:
            return _cmp(op, _num(self.args[0].eval()), _num(self.args[1].eval()))
        if op == "between":
            x, lo, hi = (_num(a.eval()) for a in self.args)
            return None if None in (x, lo, hi) else lo <= x <= hi
        if op == "within_ticks":
            a, b = _num(self.args[0].eval()), _num(self.args[1].eval())
            n = self.args[2].eval()
            return None if a is None or b is None else abs(a - b) <= n * self.ctx.tick + 1e-9
        if op in ("cross_above", "cross_below"):
            if len(self.hist) < 2:
                return False
            (a0, b0), (a1, b1) = self.hist[-2], self.hist[-1]
            if None in (a0, b0, a1, b1):
                return False
            return (a0 <= b0 and a1 > b1) if op == "cross_above" else (a0 >= b0 and a1 < b1)
        if op in ("rising", "falling"):
            n = int(self.args[1].eval())
            vals = [h[0] for h in list(self.hist)[-(n + 1):]]
            if len(vals) < n + 1 or any(v is None for v in vals):
                return False
            return all(vals[i] < vals[i + 1] for i in range(n)) if op == "rising" else all(vals[i] > vals[i + 1] for i in range(n))
        if op == "touched":
            n = self.state.get("n")
            within = int(self.args[2].eval())
            return n is not None and n <= within
        if op in ("held_above", "held_below"):
            return self.state.get("run", 0) >= int(self.args[1].eval())
        if op == "bars_since":
            return self.state.get("n")
        if op == "retest":
            return bool(self.state.get("fire"))
        raise ExprError(op)


def _num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return v


def compile_expr(e, ctx) -> Node:
    if isinstance(e, bool):
        return Const(e)
    if isinstance(e, (int, float)):
        return Const(float(e))
    if "ind" in e:
        params = dict(e.get("params") or {})
        tf = e.get("tf") or params.pop("tf", None)
        return Ind(ctx, e["ind"], params, tf)
    if "field" in e:
        return Field(ctx, e["field"], e.get("tf"))
    return Op(e["op"], [compile_expr(a, ctx) for a in e.get("args", [])], ctx)


class Evaluator:
    """One compiled tree; call `on_bar()` after every FeatureContext.on_bar, then `eval()`."""

    def __init__(self, expr, ctx, direction: str = "long"):
        self.expr = mirror(expr) if direction == "short" else expr
        self.direction = direction
        self.root = compile_expr(self.expr, ctx)

    def on_bar(self):
        self.root.on_bar()

    def eval(self):
        return self.root.eval()
