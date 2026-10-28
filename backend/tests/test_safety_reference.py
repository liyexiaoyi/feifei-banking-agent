import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ff_agent.api import create_app


@pytest.fixture
def reference_client(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    database=tmp_path/'reference.db'
    with TestClient(create_app(database)) as client:
        response=client.post('/api/session',json={'user':'demo'})
        client.headers['X-CSRF-Token']=response.json()['csrf']
        yield client,database


def dump(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


def test_reference_route_is_authenticated_and_read_only(reference_client,tmp_path):
    client,database=reference_client
    before=dump(database)
    response=client.get('/api/safety/references')
    assert response.status_code==200
    body=response.json()
    assert body['schema_version']=='1' and body['reference_only'] is True
    assert len(body['items'])==10
    assert len({item['code'] for item in body['items']})==10
    assert dump(database)==before
    with TestClient(create_app(tmp_path/'anonymous.db')) as anonymous:
        assert anonymous.get('/api/safety/references').status_code==401
    assert client.post('/api/safety/references',json={}).status_code==405


@pytest.mark.parametrize('query',['?code=private-value','?x=private-value&x=again','?mode=real'])
def test_reference_rejects_queries_without_echo_or_database_writes(reference_client,query):
    client,database=reference_client;before=dump(database)
    response=client.get('/api/safety/references'+query)
    assert response.status_code==422
    assert response.json()['error']['code']=='INVALID_REFERENCE_QUERY'
    assert 'private-value' not in response.text
    assert dump(database)==before


def test_reference_cannot_clear_hold_or_authorize_transfer(reference_client):
    client,database=reference_client
    risky=client.post('/api/chat',json={'text':'给妈妈转300元验资','request_key':'risk-reference'})
    assert risky.status_code==200
    hold=client.get('/api/state').json()['safety_hold']
    assert hold is not None
    before=dump(database)
    assert client.get('/api/safety/references').status_code==200
    assert dump(database)==before
    assert client.get('/api/state').json()['safety_hold']['id']==hold['id']
    denied=client.post('/api/transfers/prepare',json={'payee_id':'p_mom','amount_cents':30000,'request_key':'deny-after-reference'})
    assert denied.status_code==409


def test_static_reference_is_deepcopied_and_cases_are_accepted_archives():
    from ff_agent.safety_reference import reference_snapshot
    first=reference_snapshot()
    first['items'][0]['checks'].append('tampered')
    assert 'tampered' not in json.dumps(reference_snapshot(),ensure_ascii=False)
    catalogue=json.loads((Path(__file__).resolve().parents[2]/'docs/cases/catalog.json').read_text(encoding='utf8'))
    records={record['id']:record for record in catalogue['records']}
    sources={source['id']:source for source in catalogue['sources']}
    for item in reference_snapshot()['items']:
        assert item['reference_kind'] in ('case_pattern','rule_explanation')
        if item['reference_kind']=='rule_explanation':assert item['cases']==[]
        for case in item['cases']:
            record=records[case['id']];source=sources[record['source_id']]
            assert record['evidence_verified'] is True and source['accepted'] is True
            assert case['url']==source['url']
            assert case['publisher']==source['publisher']
            assert case['title']==record['title']
            assert case['archived_date']==source['retrieved_at'][:10]


def test_new_verified_cases_are_available_only_as_readonly_references(reference_client):
    client,database=reference_client
    before=dump(database)
    response=client.get('/api/safety/references')
    assert response.status_code==200
    items={item['code']:item for item in response.json()['items']}
    expected={
        'SAFE_ACCOUNT':{'C09'},
        'REMOTE_ACCESS':{'C02','C03','C10','C11'},
        'SECRET_DISCLOSURE':{'C05','C10','C11'},
        'FAKE_CANCELLATION_PAYMENT':{'C03','C11'},
    }
    for code,ids in expected.items():
        assert items[code]['reference_kind']=='case_pattern'
        assert {case['id'] for case in items[code]['cases']}==ids
        assert len(items[code]['cases'])<=4
    assert dump(database)==before
    catalogue=json.loads((Path(__file__).resolve().parents[2]/'docs/cases/catalog.json').read_text(encoding='utf8'))
    records={record['id']:record for record in catalogue['records']}
    assert records['C09']['outcome_type']=='劝阻成功，未损失'
    assert records['C10']['outcome_type']=='劝阻成功，避免数万元损失'
    assert records['C11']['outcome_type']=='实际盗刷19800元，案件侦办中'
