"""Bounded, append-only next-idea chain. Never fits, scores or executes.

The exported context excludes later prices, labels and economic metrics. A
post-evaluation recommendation is not represented as blinded or independent.
"""
import json
import re
import database
from models import MLExperiment, utc_now
from market.paths import get_paths
from research_agent import memory
from research_ml import discovery
from research_ml.schema import StrictModel
from pydantic import Field

MAX_IDEAS = 5


class NextIdea(StrictModel):
    discoveryDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contextDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    previousIdeaDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hypothesis: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    trainingOnlyChanges: list[str] = Field(min_length=1, max_length=8)
    falsification: str = Field(min_length=1, max_length=2000)
    notEvaluatedReason: str = Field(min_length=1, max_length=2000)
    author: str = Field(min_length=1, max_length=200)


def _folder(discovery_id):
    if not re.fullmatch(r"[a-f0-9]{12}", discovery_id):
        raise ValueError("invalid discovery identity")
    return get_paths().data_dir / "research_iterations" / discovery_id


def _history(discovery_id):
    records = []
    for path in sorted(_folder(discovery_id).glob("*.json")):
        if len(records) >= MAX_IDEAS:
            raise ValueError("iteration history exceeds bounded limit")
        if path.stat().st_size > 100_000:
            raise ValueError("iteration record exceeds size limit")
        envelope = json.loads(path.read_text())
        document = envelope["document"]
        if memory.digest(document) != envelope["digest"]:
            raise memory.ResearchConflict("iteration record changed")
        expected = records[-1]["digest"] if records else None
        if document["previousIdeaDigest"] != expected or document["iteration"] != len(records) + 1:
            raise memory.ResearchConflict("iteration chain changed")
        records.append(envelope)
    return records


def context(thread_id, discovery_id):
    with database.session_scope() as db:
        row = discovery._row(db, thread_id, discovery_id)
        result, result_digest = discovery._verified_result(row)
        plan = row.document_json["plan"]
        exposed = db.query(MLExperiment).filter_by(discovery_id=row.id).filter(
            MLExperiment.status.in_(["running", "candidate", "no_edge", "blocked"])).first() is not None
        document = {"discoveryId": row.id, "discoveryDigest": row.digest,
            "resultDigest": result_digest, "snapshotSha256": row.document_json["dataset"]["sha256"],
            "contract": plan["contract"], "trainingStart": plan["start"], "trainingEnd": plan["trainEnd"],
            "summary": result["summary"], "scope": "training-design recommendation only; no later rows or metrics",
            "laterOutcomesPreviouslyExposed": exposed,
            "blinding": "not an independent blinded review" if exposed else "no staged evaluation recorded"}
        return {"document": document, "digest": memory.digest(document)}


def history(thread_id, discovery_id):
    with database.session_scope() as db:
        discovery._row(db, thread_id, discovery_id)
    return {"ideas": _history(discovery_id), "maxIdeas": MAX_IDEAS,
            "scope": "recommendations only; zero fit, evaluation or execution budget"}


def propose(thread_id, discovery_id, request):
    request = NextIdea.model_validate(request)
    if any(not str(value).strip() for value in [request.hypothesis, request.rationale,
            request.falsification, request.notEvaluatedReason, request.author, *request.trainingOnlyChanges]):
        raise ValueError("explicit nonblank iteration reasoning required")
    exported = context(thread_id, discovery_id)
    if request.contextDigest != exported["digest"] or request.discoveryDigest != exported["document"]["discoveryDigest"]:
        raise memory.ResearchConflict("exact training-context digest required")
    with database.session_scope() as db:
        memory._write_reservation(db)
        row = discovery._row(db, thread_id, discovery_id)
        if memory.thread_stopped(db, thread_id):
            raise memory.ResearchConflict("thread was stopped")
        result, result_digest = discovery._verified_result(row)
        if result_digest != exported["document"]["resultDigest"]:
            raise memory.ResearchConflict("training result changed")
        exposed_now = db.query(MLExperiment).filter_by(discovery_id=row.id).filter(
            MLExperiment.status.in_(["running", "candidate", "no_edge", "blocked"])).first() is not None
        if exposed_now != exported["document"]["laterOutcomesPreviouslyExposed"]:
            raise memory.ResearchConflict("evaluation exposure changed; reload training context")
        records = _history(discovery_id)
        if len(records) >= MAX_IDEAS or request.previousIdeaDigest != (records[-1]["digest"] if records else None):
            raise memory.ResearchConflict("iteration budget exhausted or stale previous idea")
        document = {**request.model_dump(mode="json"), "previousIdeaDigest": request.previousIdeaDigest,
            "discoveryId": discovery_id, "threadId": thread_id, "iteration": len(records) + 1,
            "trainingContext": exported, "at": utc_now(), "status": "not_evaluated",
            "fitBudget": 0, "evaluationBudget": 0, "executionBudget": 0,
            "nextRunRequirement": "new explicit scope and unconsumed later sessions; never reset prior reservations"}
        envelope = {"document": document, "digest": memory.digest(document)}
        folder = _folder(discovery_id)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{len(records)+1:02}-{envelope['digest']}.json").open("x") as stream:
            json.dump(envelope, stream, sort_keys=True, allow_nan=False, indent=2)
        return envelope
