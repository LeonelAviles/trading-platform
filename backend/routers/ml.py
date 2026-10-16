"""Human-controlled discovery API; deliberately absent from agent tool schemas."""
from fastapi import APIRouter
from pydantic import Field

from research_ml import accounts, service
from research_ml.schema import AccountPolicy, Plan, StrictModel
from routers.agent import DecisionRequest, _research_call

router = APIRouter(prefix="/api/agent/threads/{thread_id}/ml-experiments", tags=["ml-research"])


class ExportRequest(StrictModel):
    strategy: dict
    testPlan: dict


class AccountRequest(StrictModel):
    jobId: str = Field(min_length=1, max_length=64)
    propFirm: AccountPolicy | None
    personal: AccountPolicy | None


@router.get("")
def history(thread_id: str):
    return _research_call(service.history, thread_id)


@router.post("")
def propose(thread_id: str, body: Plan):
    return _research_call(service.propose, thread_id, body)


@router.post("/{experiment_id}/decision")
def decide(thread_id: str, experiment_id: str, body: DecisionRequest):
    return _research_call(service.decide, thread_id, experiment_id, body.digest, body.action, body.reason)


@router.post("/{experiment_id}/run")
def run(thread_id: str, experiment_id: str):
    return _research_call(service.run, thread_id, experiment_id)


@router.post("/{experiment_id}/candidate-proposal")
def candidate(thread_id: str, experiment_id: str, body: ExportRequest):
    return _research_call(service.candidate_proposal, thread_id, experiment_id, body.strategy, body.testPlan)


@router.post("/{experiment_id}/accounts")
def account_report(thread_id: str, experiment_id: str, body: AccountRequest):
    return _research_call(accounts.report, thread_id, experiment_id, body.jobId, body.propFirm, body.personal)
