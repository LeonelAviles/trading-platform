"""Strategies in SQLite (PLATFORM-SPEC.md §4.7) — Spec v2 documents.

Every strategy is stored as its full v2 spec (`spec_json`) plus the columns
the desk/lineage views query (name, status, origin, parent). Legacy v1
documents passed to `save()` are converted first (`engine.v1_to_v2`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import database
from engine import spec as spec_mod
from engine.v1_to_v2 import convert_v1_to_v2
from models import Strategy


class StrategyError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_v1(doc: dict) -> bool:
    return doc.get("schemaVersion") != 2 and "conditions" in doc


def coerce(doc: dict) -> dict:
    """v1 -> v2 when needed; returns the document untouched otherwise."""
    return convert_v1_to_v2(doc) if is_v1(doc) else doc


def validate(doc: dict) -> list[str]:
    return spec_mod.validate_spec(coerce(doc))


def _to_dict(row: Strategy) -> dict:
    spec = dict(row.spec_json or {})
    spec["id"] = row.id
    spec["name"] = row.name
    spec["status"] = row.status
    spec.setdefault("origin", {})
    spec["origin"] = {**spec["origin"], "type": row.origin_type, "sourceId": row.origin_id}
    spec.setdefault("lineage", {})
    spec["lineage"] = {**spec["lineage"], "parentId": row.parent_id}
    if row.risk_json:
        spec["risk"] = row.risk_json
    spec["createdAt"] = row.created_at
    spec["updatedAt"] = row.updated_at
    return spec


def list_strategies() -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(Strategy).order_by(Strategy.name).all()
        return [_to_dict(r) for r in rows]


def get_strategy(strategy_id: str) -> dict | None:
    with database.session_scope() as db:
        row = db.get(Strategy, strategy_id)
        return _to_dict(row) if row else None


def save_strategy(doc: dict, *, strategy_id: str | None = None) -> dict:
    """Validate + normalise + upsert. Returns the stored spec."""
    spec = coerce(dict(doc))
    for k in ("createdAt", "updatedAt"):
        spec.pop(k, None)
    errors = spec_mod.validate_spec(spec)
    if errors:
        raise StrategyError("; ".join(errors))
    norm = spec_mod.normalize(spec)
    for k in ("createdAt", "updatedAt"):
        norm.pop(k, None)
    sid = strategy_id or norm.get("id") or uuid.uuid4().hex[:12]
    norm["id"] = sid
    with database.session_scope() as db:
        row = db.get(Strategy, sid)
        if row is None:
            row = Strategy(id=sid, created_at=_now())
            db.add(row)
        row.name = norm["name"]
        row.status = norm.get("status", "draft")
        row.origin_type = (norm.get("origin") or {}).get("type", "manual")
        row.origin_id = (norm.get("origin") or {}).get("sourceId")
        pid = (norm.get("lineage") or {}).get("parentId")
        row.parent_id = pid if pid and pid != sid and db.get(Strategy, pid) is not None else None
        row.spec_json = norm
        row.risk_json = norm.get("risk")
        row.updated_at = _now()
        db.flush()
        return _to_dict(row)


def update_strategy(strategy_id: str, changes: dict) -> dict:
    """Shallow-merge `changes` onto the stored spec (same id)."""
    cur = get_strategy(strategy_id)
    if cur is None:
        raise StrategyError(f"strategy '{strategy_id}' not found")
    merged = {**cur, **changes, "id": strategy_id}
    return save_strategy(merged, strategy_id=strategy_id)


def delete_strategy(strategy_id: str) -> bool:
    with database.session_scope() as db:
        row = db.get(Strategy, strategy_id)
        if row is None:
            return False
        db.delete(row)
        return True


def set_status(strategy_id: str, status: str) -> dict:
    if status not in spec_mod.STATUSES:
        raise StrategyError(f"status must be one of {list(spec_mod.STATUSES)}")
    return update_strategy(strategy_id, {"status": status})


def patch_risk(strategy_id: str, risk: dict, proposed_by: str = "user") -> dict:
    cur = get_strategy(strategy_id)
    if cur is None:
        raise StrategyError(f"strategy '{strategy_id}' not found")
    merged_risk = {**(cur.get("risk") or {}), **risk, "proposedBy": proposed_by}
    if "passCriteria" in risk:
        merged_risk["passCriteria"] = {**((cur.get("risk") or {}).get("passCriteria") or {}), **risk["passCriteria"]}
    return update_strategy(strategy_id, {"risk": merged_risk})


def lineage(strategy_id: str) -> dict:
    """The tree rooted at the strategy's root ancestor, with each node's latest IS verdict."""
    with database.session_scope() as db:
        rows = {r.id: r for r in db.query(Strategy).all()}
        from models import Backtest

        verdicts: dict[str, dict] = {}
        for b in db.query(Backtest).filter(Backtest.window_kind == "is", Backtest.status == "done").order_by(Backtest.created_at).all():
            sid = b.strategy_id or (b.metrics_json or {}).get("legacyStrategyId")
            if sid and (b.metrics_json or {}).get("verdict"):
                verdicts[sid] = {**b.metrics_json["verdict"], "backtestId": b.id,
                                 "profitFactor": b.metrics_json.get("profitFactor"), "expectancyR": b.metrics_json.get("expectancyR"),
                                 "trades": b.metrics_json.get("trades")}
        if strategy_id not in rows:
            raise StrategyError(f"strategy '{strategy_id}' not found")
        root = rows[strategy_id]
        seen = set()
        while root.parent_id and root.parent_id in rows and root.id not in seen:
            seen.add(root.id)
            root = rows[root.parent_id]
        children: dict[str, list[Strategy]] = {}
        for r in rows.values():
            if r.parent_id:
                children.setdefault(r.parent_id, []).append(r)

        def node(r: Strategy) -> dict:
            lin = (r.spec_json or {}).get("lineage") or {}
            return {"id": r.id, "name": r.name, "status": r.status, "changedVariable": lin.get("changedVariable"),
                    "rationale": lin.get("rationale"), "trialIndex": lin.get("trialIndex", 0),
                    "verdict": verdicts.get(r.id), "createdAt": r.created_at,
                    "children": [node(c) for c in sorted(children.get(r.id, []), key=lambda x: x.created_at)]}

        return {"rootId": root.id, "tree": node(root), "champion": _champion(root, children, verdicts)}


def _champion(root, children, verdicts):
    best, best_key = None, None
    stack = [root]
    while stack:
        r = stack.pop()
        v = verdicts.get(r.id)
        if v:
            key = (1 if v.get("status") == "pass" else 0, v.get("expectancyR") or -1e9)
            if best_key is None or key > best_key:
                best, best_key = r.id, key
        stack.extend(children.get(r.id, []))
    return best
