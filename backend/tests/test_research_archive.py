import pytest
import strategy_store
from research_ml import archive
from research_agent.memory import ResearchConflict
from tests.test_strategy_store import store  # noqa: F401
from tests.test_spec_validation import ORB


def test_archive_is_exact_recoverable_and_keeps_records(store,tmp_path):
    s=strategy_store.save_strategy(ORB)
    path=tmp_path/'archive.json'
    with pytest.raises(ValueError): archive.archive([s['id'],'missing'],path,'Authorized old run cleanup')
    assert not path.exists()
    archive.archive([s['id']],path,'Authorized old run cleanup')
    assert strategy_store.list_strategies()==[]
    assert len(strategy_store.list_strategies(include_archived=True))==1
    assert strategy_store.get_strategy(s['id'])['status']=='retired'
    archive.restore(path)
    assert strategy_store.list_strategies()[0]['status']=='draft'


def test_restore_refuses_overwriting_later_edits(store,tmp_path):
    s=strategy_store.save_strategy(ORB); path=tmp_path/'archive.json'
    archive.archive([s['id']],path,'Authorized cleanup')
    strategy_store.update_strategy(s['id'],{'name':'Edited after archive'})
    with pytest.raises(ResearchConflict): archive.restore(path)
