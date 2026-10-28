"""Strict session and technical-gate contract for LOCAL birthday workflows."""
from datetime import datetime
import json

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app


def login(c, user='demo'):
    z = c.post('/api/session', json={'user': user})
    assert z.status_code == 200
    c.headers['X-CSRF-Token'] = z.json()['csrf']


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path / 'birthday-api.db')
    app.state.bank.clock = lambda: datetime.fromisoformat('2026-10-28T12:00:00+08:00')
    with TestClient(app) as c:
        login(c)
        yield app, c


def body(**changes):
    return dict(recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily',
                budget_cents=100000, request_key='api-birthday-first') | changes


def create(env):
    z = env[1].post('/api/birthday/flows', json=body())
    assert z.status_code == 200, z.text
    return z.json()['flow']


def advance(env, f, key='api-advance-first'):
    z = env[1].post('/api/birthday/flows/' + f['id'] + '/advance',
                    json=dict(expected_version=f['version'], request_key=key))
    assert z.status_code == 200, z.text
    return z.json()['flow']


def test_api_metadata_advance_lookup_and_common_explicit_confirmation(env):
    app, c = env
    f = create(env)
    assert c.get('/api/birthday/flows').json()['flows'][0]['id'] == f['id']
    assert c.get('/api/birthday/catalog').json()['items'][0]['merchant_connected'] is False
    f = advance(env, f)
    opid = f['nodes'][0]['operation_id']
    assert f['nodes'][0]['observed_state'] == 'WAITING_CONFIRMATION'
    assert c.get('/api/birthday/requests/api-advance-first').json()['flow'] == f
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_budgets').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM messages WHERE request_key=?', ('direct-' + opid,)).fetchone()[0] == 1
    z = c.post('/api/operations/' + opid + '/confirm', json={})
    assert z.status_code == 200, z.text
    if z.json()['status'] == 'WAITING_VERIFICATION':
        z = c.post('/api/operations/' + opid + '/verify', json={'code': '246810'})
    assert z.json()['status'] == 'SUCCEEDED'


@pytest.mark.parametrize('changes', [dict(budget_cents=True), dict(budget_cents='1000'), dict(budget_cents=1.1),
    dict(budget_cents=0), dict(user_id='guest'), dict(risk='GREEN'), dict(status='COMPLETED'),
    dict(token='forged'), dict(request_key='short'), dict(recipient_label=1)])
def test_strict_create_body_cannot_override_identity_permission_or_money(env, changes):
    z = env[1].post('/api/birthday/flows', json=body(**changes))
    assert z.status_code == 422, z.text
    with env[0].state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 0


@pytest.mark.parametrize('extra', [dict(cake=0), dict(cake='false'), dict(cake=None), dict(cake=False, node_status='SUCCEEDED')])
def test_cake_json_is_strict_boolean_without_state_override(env, extra):
    f = create(env)
    z = env[1].post('/api/birthday/flows/' + f['id'] + '/cake',
                    json=dict(expected_version=f['version'], request_key='api-cake-strict') | extra)
    assert z.status_code == 422, z.text


def test_owner_bound_reads_csrf_and_cancel_confirmation(env):
    app, c = env
    f = create(env)
    path = '/api/birthday/flows/' + f['id']
    c.headers.pop('X-CSRF-Token')
    assert c.post(path + '/advance', json=dict(expected_version=1, request_key='no-csrf-advance')).status_code == 403
    login(c)
    assert c.post(path + '/cancel', json=dict(expected_version=1, request_key='cancel-without-approval', confirmed=False)).status_code in (400, 422)
    login(c, 'guest')
    assert c.get(path).status_code == 404
    assert c.get('/api/birthday/requests/api-birthday-first').status_code == 404
    assert c.get('/api/birthday/flows').json()['flows'] == []
    assert c.post(path + '/advance', json=dict(expected_version=1, request_key='guest-tried-advance')).status_code == 404


def test_advance_and_common_confirm_use_the_same_transfer_technical_gate(env):
    app, c = env
    f = advance(env, create(env))
    opid = f['nodes'][0]['operation_id']
    for _ in range(3):
        app.state.tool_guard.finish(app.state.tool_guard.start('demo', 'transfer'), 'tech')
    z = c.post('/api/operations/' + opid + '/confirm', json={})
    assert z.status_code == 503, z.text
    z = c.post('/api/birthday/flows/' + f['id'] + '/advance', json=dict(expected_version=f['version'], request_key='paused-tool-advance'))
    assert z.status_code == 503, z.text
    assert c.get('/api/birthday/flows/' + f['id']).status_code == 200


def test_confirmation_card_read_failure_after_commit_returns_saved_flow_not_false_failure(env, monkeypatch):
    app, c = env
    f = create(env)
    original = app.state.bank.receipt
    failed = [False]
    def fail_once(user, opid):
        if not failed[0]:
            failed[0] = True
            raise RuntimeError('isolated confirmation-card read failure')
        return original(user, opid)
    monkeypatch.setattr(app.state.bank, 'receipt', fail_once)
    path = '/api/birthday/flows/' + f['id'] + '/advance'
    payload = dict(expected_version=f['version'], request_key='post-commit-card-failure')
    z = c.post(path, json=payload)
    assert z.status_code == 200, z.text
    value = z.json()
    assert isinstance(value.get('notice'), str) and value['flow']['nodes'][0]['operation_id']
    assert c.get('/api/birthday/requests/post-commit-card-failure').json()['flow'] == value['flow']
    again = c.post(path, json=payload)
    assert again.status_code == 200, again.text
    assert again.json()['flow'] == value['flow']
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1
        opid = value['flow']['nodes'][0]['operation_id']
        assert db.execute('SELECT count(*) FROM messages WHERE request_key=?', ('direct-' + opid,)).fetchone()[0] == 1


def test_pc_options_are_owned_minimal_and_read_only_even_after_confirmation_expiry(env):
    from datetime import timedelta
    app, c = env
    f = advance(env, create(env))
    opid = f['nodes'][0]['operation_id']
    now = app.state.bank.now() + timedelta(seconds=301)
    app.state.bank.clock = lambda: now
    with app.state.bank.transaction() as db:
        before = tuple(db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone())
        expected = {r[0] for r in db.execute("SELECT id FROM accounts WHERE user_id='demo'")}
    z = c.get('/api/birthday/options')
    assert z.status_code == 200, z.text
    value = z.json()
    assert value['simulated'] is True and value['merchant_connected'] is False
    assert value['business_date'] == now.date().isoformat()
    assert {a['id'] for a in value['accounts']} == expected
    assert all(set(a) == {'id', 'name', 'masked_number'} for a in value['accounts'])
    with app.state.bank.transaction() as db:
        assert tuple(db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone()) == before
    login(c, 'guest')
    with app.state.bank.transaction() as db:
        expected_guest = {r[0] for r in db.execute("SELECT id FROM accounts WHERE user_id='guest'")}
    guest = c.get('/api/birthday/options').json()
    assert {a['id'] for a in guest['accounts']} == expected_guest
    assert not (expected_guest & expected)
