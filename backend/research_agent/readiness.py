"""Strict execution gate for research; legacy spec normalization remains compatible."""
from __future__ import annotations

import math

import yaml

from pydantic import BaseModel
from config.instruments import CONFIG_PATH

from engine import expr, spec
from engine.primitives.base import get_class

# Descriptive fields have no trading semantics. Every other model field must be
# supplied, even when the choice is null/[] (explicitly disabled).
DESCRIPTIVE = {"id", "name", "description", "origin", "lineage", "status", "meta"}


def effective_execution(document: dict) -> dict:
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else {}
    config = yaml.safe_load(CONFIG_PATH.read_text())
    override = execution.get("slippageTicksOverride")
    slippage = config["costs"]["slippage_ticks_market"] if override is None else override
    return {"mode": execution.get("mode"), "requestedSlippageTicksOverride": override,
            "slippageTicks": slippage, "slippageSource": "engineConfig" if override is None else "explicit_override",
            "commissionPerSide": config["roots"]["ES"]["commission_per_side"],
            "tickSlippageSupport": "0 or 1 only; magnitude above one is not implemented"}


def executable_errors(document: dict) -> list[str]:
    try:
        errors = spec.validate_spec(document)
    except (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError) as exc:
        return [f"invalid executable parameter: {exc}"]
    if errors:
        return errors
    model = spec.StrategySpec.model_validate(document)

    def missing(value, path=""):
        if isinstance(value, BaseModel):
            for key in type(value).model_fields:
                if not path and key in DESCRIPTIVE:
                    continue
                location = f"{path}.{key}" if path else key
                if key not in value.model_fields_set:
                    errors.append(f"{location}: explicit choice required; no research default")
                else:
                    missing(getattr(value, key), location)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                missing(item, f"{path}[{i}]")

    missing(model)
    if model.instrument.root != "ES" or model.instrument.symbol != "ES1!":
        errors.append("research currently supports ES1! only")
    if model.execution.mode not in {"bars", "ticks"}:
        errors.append("L3 research approval is deferred; supported modes are bars and ticks")
    effective = effective_execution(document)
    slippage = effective["slippageTicks"]
    if type(slippage) is not int or slippage < 0 or (model.execution.mode == "ticks" and slippage not in {0, 1}):
        errors.append("execution.slippageTicksOverride/effective config: require nonnegative integer; ticks supports only 0 or 1")
    if model.execution.mode == "bars" and spec.required_mode(document) != "bars":
        errors.append("execution.mode: these rules require ticks")
    if model.exit.stop.type == "structure" or model.exit.target.type == "level":
        errors.append("structure/level exits are unsupported for exact-approved research: the worker can substitute fallback distances and swing lookbacks are implicit; keep the draft unresolved")
    if model.risk.passCriteria.minDeflatedSharpeProb is not None:
        errors.append("risk.passCriteria.minDeflatedSharpeProb: inferential threshold unsupported while total search trials are unknown")
    if model.risk.accountSize <= 0 or model.risk.riskPerTradePct <= 0 or model.risk.maxContracts < 1:
        errors.append("risk: positive account/risk and maxContracts >= 1 required")
    if model.risk.weeklyLossLimitPct != 0 or model.risk.weeklyTargetPct is not None:
        errors.append("weekly risk controls are unsupported by the worker; explicitly disable with weeklyLossLimitPct=0 and weeklyTargetPct=null or keep the draft unresolved")
    if model.constraints.maxConcurrentPositions != 1:
        errors.append("worker supports exactly one concurrent position")
    if model.sizing.type == "vol_scaled":
        errors.append("vol_scaled sizing is not implemented by this worker")
    if model.sizing.type == "fixed_contracts" and not model.sizing.value.is_integer():
        errors.append("fixed_contracts sizing requires an integer contract count")
    for key in ("maxTradesPerDay", "stopAfterConsecutiveLosses"):
        if getattr(model.risk, key) != getattr(model.constraints, key):
            errors.append(f"risk.{key} and constraints.{key} must agree; worker uses constraints")
    if model.risk.maxContracts != model.sizing.maxContracts:
        errors.append("risk.maxContracts and sizing.maxContracts must agree")
    if model.sizing.type == "fixed_risk" and model.sizing.value != model.risk.riskPerTradePct:
        errors.append("sizing.value and risk.riskPerTradePct must agree for fixed_risk")
    if model.entry.orderType == "stop" and model.entry.stopOffsetTicks <= 0:
        errors.append("stop orders require a positive explicit stopOffsetTicks; zero is unsupported")
    if model.risk.dailyLossLimitPct < 0 or model.constraints.cooldownBars < 0 or model.constraints.maxTradesPerDay < 0 or model.constraints.stopAfterConsecutiveLosses < 0:
        errors.append("risk/constraint limits must be nonnegative (zero disables supported limits)")
    # Primitive defaults otherwise escape Pydantic's field-presence checks.
    expressions = [model.entry.trigger, *model.filters, *(s.when for s in model.entry.sequence)]
    for expression in expressions:
        for node in expr.walk(expression):
            if isinstance(node, (float, int)) and not math.isfinite(node):
                errors.append("expression constants must be finite")
            if isinstance(node, dict):
                allowed = {"ind", "params", "tf"} if "ind" in node else {"field", "tf"} if "field" in node else {"op", "args"}
                if set(node) - allowed:
                    errors.append(f"expression contains ignored/unsupported fields: {sorted(set(node) - allowed)}")
        for name, params, _tf in expr.referenced_primitives(expression):
            cls = get_class(name)
            for key, descriptor in cls.params.items():
                value = params.get(key)
                label = f"{name}.params.{key}"
                if key not in params:
                    errors.append(f"{label}: explicit choice required")
                    continue
                if value is None:
                    if not (descriptor.type == "price" and descriptor.default is None and not descriptor.required):
                        errors.append(f"{label}: null is not an executable parameter choice")
                    continue
                if descriptor.type == "int" and type(value) is not int:
                    errors.append(f"{label}: must be an explicit integer")
                    continue
                if descriptor.type in {"int", "float", "price"}:
                    if type(value) not in {int, float} or not math.isfinite(value):
                        errors.append(f"{label}: finite numeric value required")
                        continue
                    if key in {"period", "n", "minutes", "count", "within_bars", "levels", "ratio", "stdev"} and value <= 0:
                        errors.append(f"{label}: must be positive")
                    if key in {"ticks", "within_ticks", "max_range_ticks", "min_volume", "min_size", "tail_max"} and value < 0:
                        errors.append(f"{label}: must be nonnegative")
    return errors
