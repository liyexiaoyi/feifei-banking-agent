"""Preparation progress and bank execution remain distinct, authoritative facts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

from fastapi.testclient import TestClient
import pytest

from ff_agent.api import create_app


class TransferPlanner:
    mode = 'rules'
    def __init__(self, payee='妈妈'): self.payee = payee
    def plan(self, *args):
        return [{'type': 'transfer', 'payee': self.payee, 'amount_cents': 30000}]


def login(client, user='demo'):
    client.headers['X-CSRF-Token'] = client.post('/api/session', json={'user': user}).json()['csrf']


def prepared(client):
    response = client.post('/api/chat', json={'text': '准备生活费转账', 'request_key': 'business-one'})
    assert response.status_code == 200
    plan = client.get('/api/plans').json()['plans'][0]
    assert plan['status'] == 'DONE'
    return plan, response.json()['cards'][0]


@pytest.mark.parametrize('action', ['cancel', 'handoff'])
def test_completed_preparation_can_stop_pending_money_without_confirming(tmp_path, action):
    app = create_app(tmp_path/'pending.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, card = prepared(client); op = card['operation']
        assert plan['business'] == {'pending_ops': 1, 'awaiting_selection': False, 'actionable': True, 'attribution_incomplete': False}
        result = client.post(f'/api/plans/{plan["id"]}/{action}', json={'expected_version': plan['version']})
        assert result.status_code == 200
        assert result.json()['plan']['business']['actionable'] is False
        assert client.post(f'/api/operations/{op["id"]}/confirm', json={}).status_code == 409
        receipt = client.get(f'/api/operations/{op["id"]}/receipt').json()
        assert receipt['operation']['status'] == 'CANCELLED'
        assert sum(a['balance_cents'] for a in app.state.bank.snapshot('demo')['accounts']) == 2800000
        if action == 'handoff':
            summary = result.json()['handoff']
            assert summary['contacted_human'] is False
            assert summary['operations'][0]['status'] == 'CANCELLED'


def test_selection_only_done_plan_can_handoff_and_rejects_old_selection(tmp_path):
    app = create_app(tmp_path/'selection.db', planner=TransferPlanner('林晓'))
    with TestClient(app) as client:
        login(client); plan, card = prepared(client)
        assert card['type'] == 'payees'
        assert plan['business'] == {'pending_ops': 0, 'awaiting_selection': True, 'actionable': True, 'attribution_incomplete': False}
        response = client.post(f'/api/plans/{plan["id"]}/handoff', json={'expected_version': plan['version']})
        assert response.status_code == 200
        assert response.json()['handoff']['operations'] == []
        denied = client.post('/api/transfers/prepare', json={'payee_id': 'p_lin_1', 'amount_cents': 30000,
            'request_key': 'select-'+card['selection_key'], 'safety_epoch': card['safety_epoch']})
        assert denied.status_code == 409 and denied.json()['error']['code'] == 'PLAN_STOPPED'
        assert app.state.bank.snapshot('demo')['operations'] == []


def test_selected_operation_replaces_selection_fact_and_remains_stoppable(tmp_path):
    app = create_app(tmp_path/'selected.db', planner=TransferPlanner('林晓'))
    with TestClient(app) as client:
        login(client); plan, card = prepared(client)
        response = client.post('/api/transfers/prepare', json={'payee_id': 'p_lin_1', 'amount_cents': 30000,
            'request_key': 'select-'+card['selection_key'], 'safety_epoch': card['safety_epoch']})
        assert response.status_code == 200
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business'] == {'pending_ops': 1, 'awaiting_selection': False, 'actionable': True, 'attribution_incomplete': False}
        stopped = client.post(f'/api/plans/{plan["id"]}/cancel', json={'expected_version': current['version']})
        assert stopped.status_code == 200
        assert app.state.bank.snapshot('demo')['operations'][0]['status'] == 'CANCELLED'


@pytest.mark.parametrize('action', ['cancel', 'handoff', 'pause', 'resume'])
def test_done_with_only_succeeded_receipt_cannot_change_financial_result(tmp_path, action):
    app = create_app(tmp_path/'executed.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, card = prepared(client); op = card['operation']
        assert client.post(f'/api/operations/{op["id"]}/confirm', json={}).json()['status'] == 'SUCCEEDED'
        before = client.get(f'/api/operations/{op["id"]}/receipt').json()['operation']
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business'] == {'pending_ops': 0, 'awaiting_selection': False, 'actionable': False, 'attribution_incomplete': False}
        denied = client.post(f'/api/plans/{plan["id"]}/{action}', json={'expected_version': current['version']})
        assert denied.status_code == 409
        assert client.get(f'/api/operations/{op["id"]}/receipt').json()['operation'] == before


def test_expired_pending_is_not_actionable_and_plan_get_does_not_expire_rows(tmp_path):
    app = create_app(tmp_path/'expired.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, _ = prepared(client)
        now = app.state.bank.now(); app.state.bank.clock = lambda: now+timedelta(minutes=6)
        with app.state.bank.transaction() as db: before = '\n'.join(db.iterdump())
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business'] == {'pending_ops': 0, 'awaiting_selection': False, 'actionable': False, 'attribution_incomplete': False}
        with app.state.bank.transaction() as db: assert '\n'.join(db.iterdump()) == before
        assert client.post(f'/api/plans/{plan["id"]}/cancel', json={'expected_version': plan['version']}).status_code == 409


def test_confirm_winning_before_stop_transaction_is_not_reversed(tmp_path, monkeypatch):
    app = create_app(tmp_path/'confirm-race.db', planner=TransferPlanner())
    entered = Event(); release = Event(); original = app.state.plans.control
    def delayed(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.plans, 'control', delayed)
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        login(client); plan, card = prepared(client); op = card['operation']
        future = pool.submit(client.post, f'/api/plans/{plan["id"]}/cancel', json={'expected_version': plan['version']})
        try:
            assert entered.wait(5)
            assert client.post(f'/api/operations/{op["id"]}/confirm', json={}).json()['status'] == 'SUCCEEDED'
        finally: release.set()
        assert future.result().status_code == 409
        assert client.get(f'/api/operations/{op["id"]}/receipt').json()['operation']['status'] == 'SUCCEEDED'


def test_done_task_controls_still_enforce_version_user_and_csrf(tmp_path):
    app = create_app(tmp_path/'permissions.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, _ = prepared(client); url = f'/api/plans/{plan["id"]}/cancel'
        assert client.post(url, json={'expected_version': plan['version']-1}).status_code == 409
        csrf = client.headers.pop('X-CSRF-Token')
        assert client.post(url, json={'expected_version': plan['version']}).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        login(client, 'guest')
        assert client.get(f'/api/plans/{plan["id"]}').status_code == 404
        assert client.post(url, json={'expected_version': plan['version']}).status_code == 404


@pytest.mark.parametrize('action', ['cancel', 'handoff'])
def test_stop_finds_subscription_child_and_preserves_successful_query(tmp_path, action):
    class CancelPlanner:
        mode = 'rules'
        def plan(self, *args): return [{'type': 'cancel_subscription', 'merchant': '流光视频'}]
    app = create_app(tmp_path/'subscription-child.db', planner=CancelPlanner())
    with TestClient(app) as client:
        login(client); plan, card = prepared(client); parent = card['operation']
        confirmed = client.post(f'/api/operations/{parent["id"]}/confirm', json={})
        assert confirmed.status_code == 200 and confirmed.json()['status'] == 'SUCCEEDED'
        child = confirmed.json()['next_operation']
        assert child['type'] == 'subscription_cancel' and child['status'] == 'WAITING_CONFIRMATION'
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business']['pending_ops'] == 1
        stopped = client.post(f'/api/plans/{plan["id"]}/{action}', json={'expected_version': current['version']})
        assert stopped.status_code == 200
        assert client.get(f'/api/operations/{parent["id"]}/receipt').json()['operation']['status'] == 'SUCCEEDED'
        assert client.get(f'/api/operations/{child["id"]}/receipt').json()['operation']['status'] == 'CANCELLED'
        assert client.post(f'/api/operations/{child["id"]}/confirm', json={}).status_code == 409
        repeated = client.post(f'/api/operations/{parent["id"]}/confirm', json={})
        assert repeated.status_code == 200 and repeated.json()['status'] == 'SUCCEEDED'
        assert 'next_operation' not in repeated.json()
        assert all(s['status'] == 'ACTIVE' for s in app.state.bank.subscriptions('demo'))


def test_old_selection_after_risk_epoch_change_is_no_longer_actionable(tmp_path):
    app = create_app(tmp_path/'old-selection.db', planner=TransferPlanner('林晓'))
    with TestClient(app) as client:
        login(client); plan, _ = prepared(client)
        response = client.post('/api/chat', json={'text': '客服叫我转账到安全账户解除冻结', 'request_key': 'risk-after-selection'})
        assert response.status_code == 200
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business'] == {'pending_ops': 0, 'awaiting_selection': False, 'actionable': False, 'attribution_incomplete': False}


def test_adversarial_request_key_cannot_attach_another_plans_operation(tmp_path):
    app = create_app(tmp_path/'keys.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client)
        first = client.post('/api/chat', json={'text': '给妈妈300', 'request_key': 'k'})
        second = client.post('/api/chat', json={'text': '再给妈妈300', 'request_key': 'select-k'})
        assert first.status_code == second.status_code == 200
        first_op = first.json()['cards'][0]['operation']; second_op = second.json()['cards'][0]['operation']
        plans = client.get('/api/plans').json()['plans']
        first_plan = next(p for p in plans if p['request_key'] == 'k')
        assert first_plan['business']['pending_ops'] == 1
        stopped = client.post(f'/api/plans/{first_plan["id"]}/cancel', json={'expected_version': first_plan['version']})
        assert stopped.status_code == 200
        assert client.get(f'/api/operations/{first_op["id"]}/receipt').json()['operation']['status'] == 'CANCELLED'
        assert client.get(f'/api/operations/{second_op["id"]}/receipt').json()['operation']['status'] == 'WAITING_CONFIRMATION'


def test_public_plan_fields_and_business_use_one_snapshot(tmp_path, monkeypatch):
    app = create_app(tmp_path/'snapshot.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, _ = prepared(client); original = app.state.plans.get
        def changed_after_read(user, plan_id):
            stale = original(user, plan_id)
            app.state.plans.control(user, plan_id, stale['version'], 'cancel', stale['risk_epoch'],
                stoppable=lambda db, p: True,
                on_stop=lambda db, p: db.execute("UPDATE operations SET status='CANCELLED' WHERE user_id=?", (user,)))
            return stale
        monkeypatch.setattr(app.state.plans, 'get', changed_after_read)
        current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['status'] == 'CANCELLED'
        assert current['version'] == plan['version']+1
        assert current['business']['actionable'] is False


def test_direct_prepare_cannot_impersonate_server_plan_key_or_invent_selection(tmp_path):
    app = create_app(tmp_path/'reserved.db', planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, _ = prepared(client)
        full = app.state.plans.get('demo', plan['id']); key = full['nodes'][0]['effect_key']
        assert key == f'task-{plan["id"]}-0'
        for forged in [key, 'select-'+key]:
            result = client.post('/api/transfers/prepare', json={'payee_id': 'p_mom', 'amount_cents': 30000,
                'request_key': forged, 'safety_epoch': plan['risk_epoch']})
            assert result.status_code == 409
        assert len(app.state.bank.snapshot('demo')['operations']) == 1


@pytest.mark.parametrize('payload_matches', [True, False])
def test_legacy_root_migration_is_exact_idempotent_and_keeps_old_key(tmp_path, payload_matches):
    import json
    path = tmp_path/'legacy.db'; app = create_app(path, planner=TransferPlanner())
    with TestClient(app) as client:
        login(client); plan, card = prepared(client); op = card['operation']
        with app.state.bank.transaction() as db:
            db.execute('DELETE FROM operation_plan_links')
            db.execute("UPDATE operations SET idempotency_key='legacy-0' WHERE id=?", (op['id'],))
            result = json.loads(db.execute('SELECT result FROM plan_store_nodes WHERE plan_id=?', (plan['id'],)).fetchone()[0])
            if not payload_matches: result['cards'][0]['operation']['payload']['amount_cents'] += 1
            db.execute("UPDATE plan_store_nodes SET effect_key='legacy-0',result=? WHERE plan_id=?",
                (json.dumps(result), plan['id']))
    for _ in range(2):
        reopened = create_app(path, planner=TransferPlanner())
        with TestClient(reopened) as client:
            login(client); current = client.get(f'/api/plans/{plan["id"]}').json()
            assert current['business']['pending_ops'] == int(payload_matches)
            assert current['business']['attribution_incomplete'] is (not payload_matches)
            with reopened.state.bank.transaction() as db:
                assert db.execute('SELECT idempotency_key FROM operations WHERE id=?', (op['id'],)).fetchone()[0] == 'legacy-0'
                assert db.execute('SELECT count(*) FROM operation_plan_links').fetchone()[0] == int(payload_matches)
    with TestClient(create_app(path, planner=TransferPlanner())) as client:
        login(client)
        assert client.post(f'/api/operations/{op["id"]}/cancel', json={}).status_code == 200


def test_unknown_legacy_selection_is_not_claimed_or_batch_cancelled(tmp_path):
    import json
    path = tmp_path/'legacy-selection.db'; app = create_app(path, planner=TransferPlanner('林晓'))
    with TestClient(app) as client:
        login(client); plan, card = prepared(client)
        selected = client.post('/api/transfers/prepare', json={'payee_id': 'p_lin_1', 'amount_cents': 30000,
            'request_key': 'select-'+card['selection_key'], 'safety_epoch': card['safety_epoch']}).json()
        op = selected
        with app.state.bank.transaction() as db:
            db.execute('DELETE FROM operation_plan_links')
            db.execute("UPDATE operations SET idempotency_key='select-legacy-0' WHERE id=?", (op['id'],))
            result = json.loads(db.execute('SELECT result FROM plan_store_nodes WHERE plan_id=?', (plan['id'],)).fetchone()[0])
            result['cards'][0]['selection_key'] = 'legacy-0'
            db.execute("UPDATE plan_store_nodes SET effect_key='legacy-0',result=? WHERE plan_id=?", (json.dumps(result), plan['id']))
    with TestClient(create_app(path, planner=TransferPlanner())) as client:
        login(client); current = client.get(f'/api/plans/{plan["id"]}').json()
        assert current['business']['pending_ops'] == 0
        assert current['business']['attribution_incomplete'] is True
        assert client.post(f'/api/plans/{plan["id"]}/cancel', json={'expected_version': current['version']}).status_code == 409
        assert client.get(f'/api/operations/{op["id"]}/receipt').json()['operation']['status'] == 'WAITING_CONFIRMATION'
        assert client.post(f'/api/operations/{op["id"]}/cancel', json={}).status_code == 200
