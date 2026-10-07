"""Strict execution gate for research; legacy spec normalization remains compatible."""
from __future__ import annotations

from pydantic import BaseModel

from engine import expr, spec
from engine.primitives.base import get_class

# Descriptive fields have no trading semantics. Every other model field must be
# supplied, even when the choice is null/[] (explicitly disabled).
DESCRIPTIVE = {"id", "name", "description", "origin", "lineage", "status", "meta"}


def executable_errors(document: dict) -> list[str]:
    errors = spec.validate_spec(document)
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
    if model.execution.mode == "bars" and spec.required_mode(document) != "bars":
        errors.append("execution.mode: these rules require ticks")
    if model.risk.passCriteria.minDeflatedSharpeProb is not None:
        errors.append("risk.passCriteria.minDeflatedSharpeProb: inferential threshold unsupported while total search trials are unknown")
    if model.risk.accountSize <= 0 or model.risk.riskPerTradePct <= 0 or model.risk.maxContracts < 1:
        errors.append("risk: positive account/risk and maxContracts >= 1 required")
    # Primitive defaults otherwise escape Pydantic's field-presence checks.
    expressions = [model.entry.trigger, *model.filters, *(s.when for s in model.entry.sequence)]
    for expression in expressions:
        for name, params, _tf in expr.referenced_primitives(expression):
            cls = get_class(name)
            for key in cls.params:
                if key not in params:
                    errors.append(f"{name}.params.{key}: explicit choice required")
    return errors
