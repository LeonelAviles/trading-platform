"""Recoverable exact-ID archival; preserves backtests, lineage and audit rows."""
import copy
import json
from pathlib import Path
import database
from models import Strategy, Backtest, utc_now
from research_agent.memory import ResearchConflict


def archive(strategy_ids, backup_path, reason):
    ids=sorted(set(strategy_ids))
    if not ids or not reason.strip(): raise ValueError('explicit IDs and reason required')
    with database.session_scope() as db:
        rows=db.query(Strategy).filter(Strategy.id.in_(ids)).all()
        if sorted(r.id for r in rows)!=ids: raise ValueError('unknown strategy ID; nothing archived')
        if db.query(Backtest).filter(Backtest.strategy_id.in_(ids), Backtest.status.in_(['queued','running'])).first():
            raise ResearchConflict('linked backtest is active; archive refused')
        if any((r.spec_json or {}).get('meta',{}).get('researchArchive') for r in rows):
            raise ResearchConflict('already archived; inspect existing backup')
        snapshot={'version':'strategy_archive_v1','at':utc_now(),'reason':reason,'strategies':[
            {'id':r.id,'status':r.status,'spec':copy.deepcopy(r.spec_json),
             'backtestIds':[b.id for b in db.query(Backtest).filter_by(strategy_id=r.id).all()]} for r in rows]}
        path=Path(backup_path); path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as f: json.dump(snapshot,f,indent=2,allow_nan=False)
        path.chmod(0o600)
        for row in rows:
            value=copy.deepcopy(row.spec_json or {})
            value.setdefault('meta',{})['researchArchive']={'at':snapshot['at'],'reason':reason,'priorStatus':row.status}
            row.spec_json=value; row.status='retired'
        return {'archivedIds':ids,'backup':str(path),'backtestsPreserved':sum(len(r['backtestIds']) for r in snapshot['strategies'])}


def restore(backup_path):
    snapshot=json.loads(Path(backup_path).read_text())
    if snapshot.get('version')!='strategy_archive_v1': raise ValueError('invalid archive backup')
    with database.session_scope() as db:
        for previous in snapshot['strategies']:
            row=db.get(Strategy,previous['id'])
            marker=(row.spec_json or {}).get('meta',{}).get('researchArchive') if row else None
            if not marker or marker['at']!=snapshot['at']: raise ResearchConflict('archive changed; restore refused')
            # Refuse to overwrite any edits made after archival.
            expected=copy.deepcopy(previous['spec']); expected.setdefault('meta',{})['researchArchive']=marker
            if expected!=row.spec_json or row.status!='retired': raise ResearchConflict('archived strategy was edited')
        for previous in snapshot['strategies']:
            row=db.get(Strategy,previous['id']); row.status=previous['status']; row.spec_json=previous['spec']
    return {'restoredIds':[r['id'] for r in snapshot['strategies']]}
