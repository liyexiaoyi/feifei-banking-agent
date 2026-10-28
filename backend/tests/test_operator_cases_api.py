"""Explicit sharing and separate operator credentials never authorize payment."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from test_handoff_progress_api import HandoffPlanner, login, prepare

ACCESS = 'demo-operator-test-credential-not-a-real-key-2026'


def operator_login(client):
    response = client.post('/api/operator/session', json={'access_token': ACCESS})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    assert response.json()['simulated'] is True
    assert 'access_token' not in response.json()
    return response


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_OPERATOR_ACCESS_TOKEN', ACCESS)
    app = create_app(tmp_path / 'operator-api.db', planner=HandoffPlanner())
    with TestClient(app) as customer:
        operator = TestClient(app)
        try:
            login(customer)
            _, summary = prepare(customer)
            plan = summary['plan_id']
            version = customer.get('/api/plans/' + plan).json()['version']
            yield app, customer, operator, plan, version
        finally:
            operator.close()


def share(customer, plan, version, key='share-first-key'):
    response = customer.post('/api/plans/' + plan + '/operator-case', json={
        'expected_version': version, 'confirmed': True, 'request_key': key})
    assert response.status_code == 200, response.text
    return response.json()


def financial(bank):
    with bank.transaction() as db:
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                for table in ('accounts', 'operations', 'transactions', 'subscriptions', 'bank_cards')}


def test_separate_short_lived_revocable_operator_session(setup):
    app, customer, operator, _, _ = setup
    assert customer.get('/api/operator/cases').status_code == 401
    assert operator.get('/api/state').status_code == 401
    response = operator_login(operator)
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()
    assert operator.get('/api/state').status_code == 401
    assert operator.get('/api/operator/session').json()['operator_id'] == 'local-demo-operator'
    assert operator.delete('/api/operator/session').status_code == 200
    assert operator.get('/api/operator/cases').status_code == 401


def test_unconfigured_operator_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv('FF_OPERATOR_ACCESS_TOKEN', raising=False)
    with TestClient(create_app(tmp_path / 'disabled.db')) as client:
        assert client.post('/api/operator/session', json={'access_token': ACCESS}).status_code == 503


def test_wrong_token_origin_csrf_and_expiry(setup):
    app, _, operator, _, _ = setup
    assert operator.post('/api/operator/session', json={'access_token': 'bad'}).status_code == 401
    assert operator.post('/api/operator/session', json={'access_token': ACCESS}, headers={'origin': 'https://foreign.invalid'}).status_code == 403
    operator_login(operator)
    csrf = operator.headers.pop('X-CSRF-Token')
    assert operator.delete('/api/operator/session').status_code == 403
    operator.headers['X-CSRF-Token'] = csrf
    future = app.state.bank.now() + timedelta(seconds=1801)
    app.state.bank.clock = lambda: future
    assert operator.get('/api/operator/cases').status_code == 401


def test_failed_login_is_rate_limited(setup):
    _, _, operator, _, _ = setup
    for _ in range(5):
        assert operator.post('/api/operator/session', json={'access_token': 'bad'}).status_code == 401
    assert operator.post('/api/operator/session', json={'access_token': 'bad'}).status_code == 429


def test_explicit_share_reply_never_changes_financial_fact(setup):
    app, customer, operator, plan, version = setup
    operator_login(operator)
    assert operator.get('/api/operator/cases').json()['cases'] == []
    with app.state.bank.transaction() as db:
        row = db.execute("SELECT id FROM operations WHERE user_id='demo' ORDER BY rowid DESC LIMIT 1").fetchone()
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (row['id'],))
    before = financial(app.state.bank)
    created = share(customer, plan, version)
    assert created['status'] == 'QUEUED' and created['version'] == 1
    assert created['simulated'] is True
    assert len(operator.get('/api/operator/cases').json()['cases']) == 1
    case = created['id']
    response = operator.post('/api/operator/cases/' + case + '/claim', json={
        'expected_version': 1, 'request_key': 'operator-claim-1'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'IN_REVIEW'
    reply = operator.post('/api/operator/cases/' + case + '/reply', json={
        'expected_version': 2, 'request_key': 'operator-reply-1', 'reply': '请先核对官方渠道，当前转账结果还不能确定。'})
    assert reply.status_code == 200, reply.text
    assert reply.json()['status'] == 'REPLIED'
    actual = customer.get('/api/operator-cases/' + case).json()
    assert actual['status'] == 'REPLIED' and '请先核对' in actual['reply']
    assert financial(app.state.bank) == before
    assert actual['current_facts']['operations'][0]['status'] == 'UNKNOWN'


def test_withdraw_after_reply_blocks_old_share_and_operator_replay(setup):
    app, customer, operator, plan, version = setup
    operator_login(operator)
    case = share(customer, plan, version)['id']
    claim = {'expected_version': 1, 'request_key': 'operator-claim-1'}
    assert operator.post('/api/operator/cases/' + case + '/claim', json=claim).status_code == 200
    assert operator.post('/api/operator/cases/' + case + '/reply', json={
        'expected_version': 2, 'request_key': 'operator-reply-1', 'reply': '仅本机演示回复。'}).status_code == 200
    before = financial(app.state.bank)
    withdrawn = customer.post('/api/operator-cases/' + case + '/withdraw', json={
        'expected_version': 3, 'request_key': 'withdraw-case-1'})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()['status'] == 'CANCELLED'
    replay = share(customer, plan, version)
    assert replay['status'] == 'CANCELLED' and replay['snapshot'] is None
    assert operator.get('/api/operator/cases').json()['cases'] == []
    assert operator.get('/api/operator/cases/' + case).status_code == 404
    assert operator.post('/api/operator/cases/' + case + '/claim', json=claim).status_code == 404
    assert financial(app.state.bank) == before


def test_case_owner_cas_and_key_binding(setup):
    _, customer, _, plan, version = setup
    case = share(customer, plan, version)['id']
    assert customer.post('/api/plans/' + plan + '/operator-case', json={
        'expected_version': version + 1, 'confirmed': True, 'request_key': 'share-first-key'}).status_code == 409
    assert customer.post('/api/operator-cases/' + case + '/withdraw', json={
        'expected_version': 0, 'request_key': 'stale-withdraw'}).status_code == 409
    login(customer, 'guest')
    assert customer.get('/api/operator-cases/' + case).status_code == 404
    assert customer.post('/api/plans/' + plan + '/operator-case', json={
        'expected_version': version, 'confirmed': True, 'request_key': 'guest-share-key'}).status_code == 404
    assert customer.post('/api/operator-cases/' + case + '/withdraw', json={
        'expected_version': 1, 'request_key': 'guest-withdraw'}).status_code == 404


@pytest.mark.parametrize('change', [{'confirmed': False}, {'confirmed': 1}, {'expected_version': True},
                                     {'operator_id': 'owner'}, {'request_key': 'tiny'}])
def test_share_requires_strict_explicit_consent(setup, change):
    _, customer, _, plan, version = setup
    payload = {'expected_version': version, 'confirmed': True, 'request_key': 'strict-share-key'}
    payload.update(change)
    assert customer.post('/api/plans/' + plan + '/operator-case', json=payload).status_code == 422


def test_shared_facts_exclude_private_notes_chat_and_full_phone(setup):
    _, customer, operator, plan, version = setup
    private = '只属于用户的私密聊天偏好示例'
    assert customer.post('/api/memories', json={'content': private, 'request_key': 'private-note-key'}).status_code == 200
    case = share(customer, plan, version)['id']
    operator_login(operator)
    data = operator.get('/api/operator/cases/' + case)
    assert data.status_code == 200, data.text
    serialized = json.dumps(data.json(), ensure_ascii=False)
    assert private not in serialized and '13800000001' not in serialized
    assert ACCESS not in serialized and '246810' not in serialized
