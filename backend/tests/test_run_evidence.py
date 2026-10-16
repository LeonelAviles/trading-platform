import pytest
from research_ml import run_evidence


def test_reports_are_immutable_and_inventory_omits_trade_details(tmp_path,monkeypatch):
    monkeypatch.setattr(run_evidence,'directory',lambda:tmp_path)
    report={'id':'run-one','status':'rejected','trades':[],'sessions':[]}
    run_evidence.save(report)
    assert run_evidence.read('run-one')==report
    assert 'trades' not in run_evidence.inventory()['runs'][0]
    assert run_evidence.reassess('run-one',{})['status']=='insufficient_sample'
    with pytest.raises(FileExistsError): run_evidence.save(report)
    with pytest.raises(ValueError): run_evidence.read('../secret')


def test_run_detail_endpoint_only_reads_existing_report(client,tmp_path,monkeypatch):
    monkeypatch.setattr(run_evidence,'directory',lambda:tmp_path)
    report={'id':'read-only-run','status':'rejected','trades':[],'sessions':[]}
    run_evidence.save(report)
    response=client.get('/api/ml/features/runs/read-only-run')
    assert response.status_code==200 and response.json()==report
    assert client.get('/api/ml/features/runs/missing').status_code==404
