"""Primitive registry (PLATFORM-SPEC.md §4.4).

A primitive is a named, parameterised feature the expression tree can
reference (`{"ind": "ema", "params": {"period": 9, "tf": "15min"}}`). Each
class declares its parameter schema, output kind, when it updates and how
many bars it needs; instances are created per (name, params, tf) by the
`FeatureContext`, which feeds them closed bars (per timeframe), trades and
book views. The docstring is what the agent sees in `get_spec_schema`.

`mirror` says how a comparison involving the primitive flips for the short
side of a `direction: both` strategy: `price` (levels/prices — comparisons
flip), `signed` (signed flow — comparisons flip and constants negate),
`rsi` (x -> 100-x), `none` (unsigned quantities — unchanged).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

REGISTRY: dict[str, type["Primitive"]] = {}


@dataclass(frozen=True)
class Param:
    type: str                 # int | float | str | bool | price
    default: Any = None
    doc: str = ""
    choices: tuple | None = None
    required: bool = False


class Primitive:
    name: str = ""
    doc: str = ""
    params: dict[str, Param] = {}
    output: str = "number"        # number | price | bool | level
    update_on: str = "bar"        # bar | trade | book
    tf_capable: bool = False
    mirror: str = "none"          # price | signed | rsi | none
    mirror_name: str | None = None  # counterpart primitive for the short side (e.g. opening_range_high <-> _low)

    def __init__(self, params: dict | None = None, tf: str | None = None):
        p = dict(params or {})
        self.tf = tf
        self.p: dict[str, Any] = {}
        for k, spec in self.params.items():
            v = p.pop(k, spec.default)
            if v is None and spec.required:
                raise ValueError(f"{self.name}: parameter '{k}' is required")
            self.p[k] = _coerce(self.name, k, spec, v)
        p.pop("tf", None)
        if p:
            raise ValueError(f"{self.name}: unknown parameter(s) {sorted(p)}")

    # -- hooks ----------------------------------------------------------------
    def lookback_bars(self) -> int:
        return 1

    def on_bar(self, ctx, bar) -> None:
        pass

    def on_trade(self, ctx, trade) -> None:
        pass

    def value(self, ctx):
        raise NotImplementedError

    # -- helpers --------------------------------------------------------------
    def series(self, ctx):
        return ctx.series[self.tf or ctx.primary_tf]

    @classmethod
    def describe(cls) -> dict:
        return {
            "name": cls.name, "doc": inspect.cleandoc(cls.doc or cls.__doc__ or ""),
            "output": cls.output, "updateOn": cls.update_on, "tfCapable": cls.tf_capable, "mirror": cls.mirror,
            "params": {k: {"type": v.type, "default": v.default, "doc": v.doc, "required": v.required,
                           **({"choices": list(v.choices)} if v.choices else {})} for k, v in cls.params.items()},
        }


def _coerce(name: str, key: str, spec: Param, v):
    if v is None:
        return None
    try:
        if spec.type == "int":
            if isinstance(v, bool) or (isinstance(v, float) and not float(v).is_integer()):
                raise ValueError
            v = int(v)
        elif spec.type in ("float", "price"):
            if isinstance(v, bool):
                raise ValueError
            v = float(v)
        elif spec.type == "bool":
            if not isinstance(v, bool):
                raise ValueError
        elif spec.type == "str":
            if not isinstance(v, str):
                raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{name}: parameter '{key}' must be {spec.type}, got {v!r}") from None
    if spec.choices and v not in spec.choices:
        raise ValueError(f"{name}: parameter '{key}' must be one of {list(spec.choices)}, got {v!r}")
    return v


def register(cls: type[Primitive]) -> type[Primitive]:
    if not cls.name:
        raise ValueError("primitive needs a name")
    if cls.name in REGISTRY:
        raise ValueError(f"duplicate primitive {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


def get_class(name: str) -> type[Primitive]:
    _ensure_loaded()
    if name not in REGISTRY:
        raise KeyError(name)
    return REGISTRY[name]


def all_primitives() -> dict[str, type[Primitive]]:
    _ensure_loaded()
    return dict(REGISTRY)


def describe_all() -> list[dict]:
    return [cls.describe() for _, cls in sorted(all_primitives().items())]


_loaded = False


def _ensure_loaded():
    global _loaded
    if _loaded:
        return
    _loaded = True
    from engine.primitives import book, orderflow, price, profile, structure, time  # noqa: F401
