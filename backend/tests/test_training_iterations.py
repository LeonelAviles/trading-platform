import json
import pytest
from models import MLHeldoutUse
import database
from research_ml import iterations,service,discovery
from research_agent import memory
from tests.test_mbo_research import raw_context  # noqa: F401
from tests.test_cluster_discovery import fitted,reviewed,hypothesis_request


def idea(context,previous=None):
    return {'discoveryDigest':context['document']['discoveryDigest'],'contextDigest':context['digest'],
        'previousIdeaDigest':previous,'hypothesis':'Separate signed pressure from activity',
        'rationale':'Activity centers are balanced, so activity alone cannot justify direction',
        'trainingOnlyChanges':['Specify signed-flow ratio and time-of-day controls on training only'],
        'falsification':'Fails later cost-adjusted criteria under a new scope',
        'notEvaluatedReason':'Current economic budget exhausted','author':'Synthetic test assistant'}


def test_next_ideas_form_bounded_chain_without_fit_or_holdout_use(raw_context,monkeypatch):
    tid,_=raw_context; row=fitted(tid); context=iterations.context(tid,row['id'])
    assert context['document']['laterOutcomesPreviouslyExposed'] is False
    monkeypatch.setattr(discovery,'fit_clusters',lambda *a:pytest.fail('next ideas cannot fit'))
    monkeypatch.setattr(service,'run',lambda *a:pytest.fail('next ideas cannot evaluate'))
    previous=None
    for index in range(5):
        result=iterations.propose(tid,row['id'],idea(context,previous))
        assert result['document']['iteration']==index+1 and result['document']['evaluationBudget']==0
        previous=result['digest']
    assert len(iterations.history(tid,row['id'])['ideas'])==5
    with pytest.raises(memory.ResearchConflict): iterations.propose(tid,row['id'],idea(context,previous))
    with database.session_scope() as db: assert db.query(MLHeldoutUse).count()==0
    path=next(iterations._folder(row['id']).glob('*.json')); value=json.loads(path.read_text()); value['document']['hypothesis']='tampered';path.write_text(json.dumps(value))
    with pytest.raises(memory.ResearchConflict,match='changed'): iterations.history(tid,row['id'])


def test_post_evaluation_context_is_honest_and_never_reopens_dates(raw_context):
    tid,_=raw_context; row=reviewed(tid)
    e=discovery.propose_hypothesis(tid,row['id'],hypothesis_request(row,minMeanNetTicks=1000000.0))
    service.decide(tid,e['id'],e['digest'],'approve','Synthetic scoped approval');service.run(tid,e['id'])
    c=iterations.context(tid,row['id'])
    assert c['document']['laterOutcomesPreviouslyExposed'] is True
    assert c['document']['blinding']=='not an independent blinded review'
    assert 'metrics' not in c['document'] and 'netTicks' not in json.dumps(c)
    with pytest.raises(memory.ResearchConflict): iterations.propose(tid,row['id'],{**idea(c),'contextDigest':'0'*64})
    iterations.propose(tid,row['id'],idea(c))
    with database.session_scope() as db: assert db.query(MLHeldoutUse).count()==2
    with pytest.raises(memory.ResearchConflict,match='stale'): iterations.propose(tid,row['id'],idea(c))
