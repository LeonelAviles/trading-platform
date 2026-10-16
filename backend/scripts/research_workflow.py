"""Bounded two-phase real-data workflow; approvals remain exact and durable.

Phases: discover exports training-only context; evaluate imports an explicit
assistant review, evaluates one hypothesis, then runs validation-only Nautilus.
The caller must supply the user's scoped research authorization reason and an
unexpired UTC deadline. No scheduler, API key, live/paper adapter or auto-search.
"""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_ml import discovery, service, run_evidence, execution
from research_ml.schema import ClusterDiscoveryPlan, ClusterHypothesisRequest
from research_agent import service as agent
from models import utc_now
import database


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['discover','evaluate'])
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--authorization-reason',required=True)
    p.add_argument('--authorization-expires',required=True)
    a=p.parse_args()
    expires=datetime.fromisoformat(a.authorization_expires.replace('Z','+00:00'))
    if not a.authorization_reason.strip() or expires.tzinfo is None or datetime.now(timezone.utc)>=expires:
        p.error('explicit unexpired scoped user authorization required')
    reason='Assistant decision under scoped user authorization: '+a.authorization_reason+' (expires '+a.authorization_expires+')'
    payload=json.loads(a.input.read_text())
    if a.phase=='discover':
        if a.state.exists(): p.error('state already exists; inspect recorded run, never silently restart')
        plan=ClusterDiscoveryPlan.model_validate(payload)
        # This runner deliberately caps the search further than the general API.
        if len(plan.sources)>12 or plan.clusterCount!=3 or plan.maxIterations>50:
            p.error('runner cap: 12 sessions, one three-cluster fit, at most 50 iterations')
        database.init_db()
        thread=agent.create_thread(title='Overnight ES real-data workflow '+utc_now())
        scope={'actor':'in_session_assistant','authorizationReason':a.authorization_reason,
            'expires':a.authorization_expires,'fitBudget':1,'hypothesisBudget':1,'executionVariantBudget':1,
            'clusterCount':3,'maxIterations':50,'execution':{'contracts':1,'stopTicks':20,'targetTicks':30,
            'horizonBars':5,'maxTradesPerDay':10,'extraStressTicksPerSide':2,'commissionUsdPerSide':2.25},
            'labelScreen':{'roundTripCostTicks':4.36,'minEvaluationSignals':30,'minMeanNetTicks':0.0,
            'minImprovementOverBaselineTicks':0.0},'stopping':'stop on technical failure, review stop or first hypothesis result; no automatic retries',
            'holdout':'both later partitions consumed once; final platform holdout excluded; diagnostic uses validation only'}
        row=discovery.propose(thread['id'],plan,research_scope=scope)
        discovery.decide(thread['id'],row['id'],row['digest'],'approve',reason)
        row=discovery.fit(thread['id'],row['id'])
        state={'threadId':thread['id'],'discoveryId':row['id'],'discoveryDigest':row['digest'],
            'authorization':{'reason':a.authorization_reason,'expires':a.authorization_expires},'discovery':row}
        if row['status']=='discovered': state['reviewContext']=discovery.review_context(thread['id'],row['id'],row['digest'])
        with a.state.open('x') as f: json.dump(state,f,indent=2,allow_nan=False)
        print(json.dumps({k:state[k] for k in ('threadId','discoveryId','reviewContext') if k in state},indent=2))
        if row['status']!='discovered': raise RuntimeError(row['result'])
    else:
        state=json.loads(a.state.read_text())
        if state['authorization']!={'reason':a.authorization_reason,'expires':a.authorization_expires}:
            p.error('authorization must match the frozen discovery scope')
        tid=state['threadId']; did=state['discoveryId']; digest=state['discoveryDigest']
        # No hidden retry. A second invocation encounters the consumed review/
        # experiment reservation and cannot silently fit/evaluate again.
        review=discovery.submit_review(tid,did,digest,state['reviewContext']['inputDigest'],payload['review'],payload['reviewer'])
        run_id='workflow-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        report={'id':run_id,'title':'ESU6 bounded real-data workflow','threadId':tid,'createdAt':utc_now(),
            'authorization':state['authorization'],'discoveryId':did,'discoveryDigest':digest,
            'review':review['review'],'settings':state['discovery']['document']['plan'],
            'provenance':state['discovery']['document']['provenance'],'finalHoldoutUsed':False,
            'trialBudget':{'fits':1,'hypotheses':1,'executionVariants':1},
            'frozenResearchScope':state['discovery']['document'].get('researchScope'),'trades':[]}
        try:
            if payload['review']['recommendation']=='stop':
                report.update(status='rejected',reason='Assistant advised stop from training features; no hypothesis tested')
            else:
                if payload['hypothesis'].get('horizonBars') != 5:
                    raise ValueError('frozen runner budget requires five-minute horizon')
                for field,expected in {'roundTripCostTicks':4.36,'minEvaluationSignals':30,'minMeanNetTicks':0.0,
                        'minImprovementOverBaselineTicks':0.0}.items():
                    if payload['hypothesis'].get(field)!=expected: raise ValueError('frozen label-screen setting changed: '+field)
                request=ClusterHypothesisRequest.model_validate({**payload['hypothesis'],
                    'discoveryDigest':digest,'reviewDigest':review['review']['digest']})
                row=discovery.propose_hypothesis(tid,did,request)
                report['experimentId']=row['id']; report['hypothesis']=row['document']['plan']
                service.decide(tid,row['id'],row['digest'],'approve',reason)
                row=service.run(tid,row['id']); report['labelEvaluation']=row
                if row['status']=='blocked': raise RuntimeError(row['result'])
                diagnostic=execution.run(tid,row['id']); report.update({k:v for k,v in diagnostic.items() if k!='execution'})
                executed=diagnostic['execution']
                hash_document=lambda value: hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                report['artifactFiles']={'nautilus':run_evidence.save_artifact(run_id,'nautilus',executed)}
                report['artifactHashes']={'executionSha256':hash_document(executed),
                    'strategySha256':hash_document(diagnostic['strategy']),'tradesSha256':hash_document(executed['trades'])}
                report.update(status=diagnostic['outcome'],reason=row['result']['reason'],
                    trades=executed['trades'],executionSummary=executed['summary'],executionMeta=executed['meta'])
        except Exception as exc:
            report.update(status='blocked',reason=str(exc),errorType=type(exc).__name__)
            raise
        finally:
            run_evidence.save(report); print(json.dumps({k:v for k,v in report.items() if k not in {'trades','labelEvaluation'}},indent=2,allow_nan=False))


if __name__=='__main__': main()
