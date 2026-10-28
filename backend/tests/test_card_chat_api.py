from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path/'card-chat-api.db')
    with TestClient(app) as client:
        login(client)
        yield app, client


def login(client, user='demo'):
    session = client.post('/api/session', json={'user': user})
    assert session.status_code == 200
    client.headers['X-CSRF-Token'] = session.json()['csrf']


def say(client, text, key):
    response = client.post('/api/chat', json={'text': text, 'request_key': key})
    assert response.status_code == 200, response.text
    return response.json()


def operations(result):
    return [c['operation'] for c in result['cards'] if c['type'] == 'operation']


def stored(app):
    with app.state.bank.transaction() as db:
        row = db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()
        return json.loads(row[0]) if row else {}


def test_query_card_chat_returns_owned_simulated_cards_without_preparing_business(environment):
    app, client = environment
    result = say(client, '看看我的银行卡', 'list')
    card, = result['cards']
    assert card['type'] == 'bank_cards' and card['simulated'] is True
    assert {c['id'] for c in card['cards']} == {'card_a_daily', 'card_a_saving'}
    assert client.get('/api/state').json()['operations'] == []
    plan = client.get('/api/plans').json()['plans'][0]
    assert plan['nodes'][0]['kind'] == 'cards' and plan['nodes'][0]['state'] == 'DONE'


def test_prepare_chat_binds_current_card_and_plan_and_requires_separate_red_authorization(environment):
    app, client = environment
    before = deepcopy(client.get('/api/state').json()['accounts'])
    op, = operations(say(client, '挂失日常那张银行卡', 'prepare'))
    assert op['type'] == 'card_report_lost' and op['risk'] == 'RED'
    assert op['status'] == 'WAITING_CONFIRMATION'
    assert op['payload']['card_id'] == 'card_a_daily' and op['payload']['expected_version'] == 1
    with app.state.bank.transaction() as db:
        link = db.execute('SELECT plan_id FROM operation_plan_links WHERE operation_id=?', (op['id'],)).fetchone()
        assert link is not None
    url = '/api/operations/' + op['id']
    assert client.post(url+'/verify', json={'code': '246810'}).status_code == 409
    assert client.post(url+'/confirm', json={}).json()['status'] == 'WAITING_VERIFICATION'
    assert client.post(url+'/verify', json={'code': '246810'}).json()['status'] == 'SUCCEEDED'
    assert client.get('/api/state').json()['accounts'] == before
    result = say(client, '刚才挂失成功了吗', 'status')
    assert operations(result)[0]['status'] == 'SUCCEEDED'


def test_missing_card_clears_transfer_followup_and_tail_does_not_become_money(environment):
    app, client = environment
    say(client, '给妈妈转账', 'transfer-missing')
    assert 'pending_transfer' in stored(app)
    result = say(client, '挂失', 'card-missing')
    assert not operations(result)
    assert stored(app)['pending_card']['mode'] == 'prepare'
    assert 'pending_transfer' not in stored(app)
    tail = client.get('/api/cards').json()['cards'][0]['masked_number'][-4:]
    op, = operations(say(client, tail, 'tail'))
    assert op['type'] == 'card_report_lost'
    assert [o['type'] for o in client.get('/api/state').json()['operations']] == ['card_report_lost']


def test_stop_selection_followup_cancels_only_the_selected_old_card_request(environment):
    app, client = environment
    first = app.state.bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'daily', 1)
    second = app.state.bank.prepare_card_status('demo', 'card_a_saving', 'report_lost', 'saving', 1)
    result = say(client, '先不要挂失', 'stop-missing')
    assert not operations(result)
    assert stored(app)['pending_card']['mode'] == 'stop'
    tail = client.get('/api/cards').json()['cards'][0]['masked_number'][-4:]
    result = say(client, tail, 'stop-selected')
    assert operations(result)[0]['status'] == 'CANCELLED'
    assert app.state.bank.operation('demo', first['id'])['status'] == 'CANCELLED'
    assert app.state.bank.operation('demo', second['id'])['status'] == 'WAITING_CONFIRMATION'
    assert len(app.state.bank.snapshot('demo')['operations']) == 2


def test_correction_cancels_old_then_prepares_new_independent_red_confirmation_and_cache_replay(environment):
    app, client = environment
    old, = operations(say(client, '挂失日常那张银行卡', 'original'))
    result = say(client, '不是那张是储蓄那张', 'correct')
    new, = [o for o in operations(result) if o['id'] != old['id']]
    assert app.state.bank.operation('demo', old['id'])['status'] == 'CANCELLED'
    assert new['payload']['card_id'] == 'card_a_saving'
    assert new['status'] == 'WAITING_CONFIRMATION' and new['risk'] == 'RED'
    assert {o['id'] for o in operations(say(client, '不是那张是储蓄那张', 'correct'))} == {old['id'], new['id']}
    assert app.state.bank.operation('demo', new['id'])['status'] == 'WAITING_CONFIRMATION'
    assert len(app.state.bank.snapshot('demo')['operations']) == 2


@pytest.mark.parametrize('invalidated', ['session', 'other_business', 'age'])
def test_card_followup_does_not_survive_invalidating_context(environment, invalidated):
    app, client = environment
    say(client, '挂失', 'missing')
    if invalidated == 'session':
        login(client)
    elif invalidated == 'other_business':
        say(client, '查询余额', 'balance')
        assert 'pending_card' not in stored(app)
    else:
        value = stored(app)
        value['pending_card']['started_at'] -= 601
        with app.state.bank.transaction() as db:
            db.execute("UPDATE contexts SET value=? WHERE user_id='demo'", (json.dumps(value),))
    tail = client.get('/api/cards').json()['cards'][0]['masked_number'][-4:]
    assert not operations(say(client, tail, 'after-invalidated'))
    assert app.state.bank.snapshot('demo')['operations'] == []


def test_plan_cancel_cancels_its_linked_card_request(environment):
    app, client = environment
    op, = operations(say(client, '挂失日常那张银行卡', 'prepare'))
    plan = client.get('/api/plans').json()['plans'][0]
    response = client.post('/api/plans/'+plan['id']+'/cancel', json={'expected_version': plan['version']})
    assert response.status_code == 200
    assert app.state.bank.operation('demo', op['id'])['status'] == 'CANCELLED'


@pytest.mark.parametrize('action', ['cancel', 'handoff'])
def test_stopping_a_missing_card_plan_also_invalidates_its_selection_followup(environment, action):
    app, client = environment
    say(client, '挂失', 'missing-card')
    plan = client.get('/api/plans').json()['plans'][0]
    assert plan['business']['awaiting_selection'] is True
    response = client.post('/api/plans/'+plan['id']+'/'+action, json={'expected_version': plan['version']})
    assert response.status_code == 200
    tail = client.get('/api/cards').json()['cards'][0]['masked_number'][-4:]
    assert not operations(say(client, tail, 'old-selection'))
    assert app.state.bank.snapshot('demo')['operations'] == []


def test_model_stop_suggestion_without_matching_user_words_cannot_cancel(tmp_path):
    class ForgedPlanner:
        mode = 'model'
        def plan(self, text, context):
            return [{'type': 'card_stop', 'card': '未指定', 'action': 'report_lost'}]
    app = create_app(tmp_path/'model-stop.db', planner=ForgedPlanner())
    old = app.state.bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'old', 1)
    with TestClient(app) as client:
        login(client)
        result = say(client, '看看我的银行卡', 'forged')
        assert result['tasks'][0]['state'] == 'BLOCKED'
        assert app.state.bank.operation('demo', old['id'])['status'] == 'WAITING_CONFIRMATION'
        assert client.get('/api/plans').json()['plans'][0]['nodes'][0]['state'] == 'BLOCKED'


def test_correction_recovers_after_prepare_commits_but_its_response_is_lost(environment, monkeypatch):
    app, client = environment
    old, = operations(say(client, '挂失日常那张银行卡', 'original'))
    prepare = app.state.bank.prepare_card_status
    committed = []
    def lose_response(*args, **kwargs):
        op = prepare(*args, **kwargs)
        committed.append(op['id'])
        raise RuntimeError('test response lost after commit')
    monkeypatch.setattr(app.state.bank, 'prepare_card_status', lose_response)
    response = client.post('/api/chat', json={'text': '不是那张是储蓄那张', 'request_key': 'recover-correction'})
    assert response.status_code == 503
    assert len(committed) == 1
    new_id = committed[0]
    assert app.state.bank.operation('demo', old['id'])['status'] == 'CANCELLED'
    monkeypatch.setattr(app.state.bank, 'prepare_card_status', prepare)
    result = say(client, '不是那张是储蓄那张', 'recover-correction')
    assert {o['id'] for o in operations(result)} == {old['id'], new_id}
    assert app.state.bank.operation('demo', new_id)['status'] == 'WAITING_CONFIRMATION'
    assert len(app.state.bank.snapshot('demo')['operations']) == 2


def test_new_transfer_clarification_clears_old_card_selection(environment):
    app, client = environment
    say(client, '挂失', 'card-missing')
    say(client, '给妈妈转账', 'transfer-missing')
    assert 'pending_card' not in stored(app)
    result = say(client, '300元', 'amount')
    op, = operations(result)
    assert op['type'] == 'transfer' and op['payload']['amount_cents'] == 30000
