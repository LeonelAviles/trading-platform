"""Explicit discovery, advisory AI review and separately approved evaluation."""
from fastapi import APIRouter, HTTPException
from pydantic import Field

from research_agent import service as agent
from research_ml import discovery
from research_ml.schema import ClusterDiscoveryPlan, ClusterHypothesisRequest, ClusterReview, StrictModel
from routers.agent import DecisionRequest, _research_call

router = APIRouter(prefix="/api/agent/threads/{thread_id}/ml-discoveries", tags=["ml-discovery"])


class DigestRequest(StrictModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class StopRequest(DigestRequest):
    reason: str = Field(min_length=1, max_length=4000)


@router.get("")
def history(thread_id: str):
    return _research_call(discovery.history, thread_id)


@router.post("")
def propose(thread_id: str, body: ClusterDiscoveryPlan):
    return _research_call(discovery.propose, thread_id, body)


@router.post("/{discovery_id}/decision")
def decide(thread_id: str, discovery_id: str, body: DecisionRequest):
    return _research_call(discovery.decide, thread_id, discovery_id, body.digest, body.action, body.reason)


@router.post("/{discovery_id}/fit")
def fit(thread_id: str, discovery_id: str):
    return _research_call(discovery.fit, thread_id, discovery_id)


@router.post("/{discovery_id}/review")
def review(thread_id: str, discovery_id: str, body: DigestRequest):
    try:
        return _research_call(discovery.review, thread_id, discovery_id, body.digest)
    except agent.AgentConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/{discovery_id}/hypothesis")
def hypothesis(thread_id: str, discovery_id: str, body: ClusterHypothesisRequest):
    return _research_call(discovery.propose_hypothesis, thread_id, discovery_id, body)


@router.post("/{discovery_id}/stop")
def stop(thread_id: str, discovery_id: str, body: StopRequest):
    return _research_call(discovery.stop, thread_id, discovery_id, body.digest, body.reason)


class AssistantReviewRequest(DigestRequest):
    inputDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=200)
    review: ClusterReview


@router.get("/{discovery_id}/review-context")
def review_context(thread_id: str, discovery_id: str, digest: str):
    return _research_call(discovery.review_context, thread_id, discovery_id, digest)


@router.post("/{discovery_id}/assistant-review")
def assistant_review(thread_id: str, discovery_id: str, body: AssistantReviewRequest):
    return _research_call(discovery.submit_review, thread_id, discovery_id, body.digest,
                          body.inputDigest, body.review, body.reviewer)


from research_ml import iterations


@router.get("/{discovery_id}/next-idea-context")
def next_idea_context(thread_id: str, discovery_id: str):
    return _research_call(iterations.context, thread_id, discovery_id)


@router.get("/{discovery_id}/next-ideas")
def next_ideas(thread_id: str, discovery_id: str):
    return _research_call(iterations.history, thread_id, discovery_id)


@router.post("/{discovery_id}/next-ideas")
def propose_next_idea(thread_id: str, discovery_id: str, body: iterations.NextIdea):
    return _research_call(iterations.propose, thread_id, discovery_id, body)
