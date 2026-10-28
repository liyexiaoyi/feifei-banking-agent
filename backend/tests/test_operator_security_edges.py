"""Independent privilege, replay and concurrency checks for operator integration."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from ff_agent.bank import DomainError
from test_operator_cases_api import setup, ACCESS, operator_login, share, financial


def test_operator_cookie_does_not_authorize_bank_actions(setup):
    app, customer, operator, plan, version = setup
    operator_login(operator)
    operation = app.state.bank.snapshot('demo')['operations'][0]['id']
    before = financial(app.state.bank)
    assert operator.post('/api/operations/' + operation + '/confirm', json={}).status_code == 401
    assert operator.get('/api/memories').status_code == 401
    assert financial(app.state.bank) == before


def test_credential_rotation_invalidates_existing_cookie(setup, monkeypatch):
    app, _, operator, _, _ = setup
    operator_login(operator)
    monkeypatch.setenv('FF_OPERATOR_ACCESS_TOKEN', ACCESS + '-rotated')
    assert operator.get('/api/operator/session').status_code == 401
    assert operator.get('/api/operator/cases').status_code == 401
    new = operator.post('/api/operator/session', json={'access_token': ACCESS + '-rotated'})
    assert new.status_code == 200
    operator.headers['X-CSRF-Token'] = new.json()['csrf']
    assert operator.get('/api/operator/cases').status_code == 200


def test_raw_tokens_not_persisted_in_auth_tables(setup):
    app, _, operator, _, _ = setup
    operator_login(operator)
    cookie = operator.cookies.get('ff_operator')
    with app.state.bank.transaction() as db:
        rows = [tuple(row) for row in db.execute('SELECT * FROM operator_sessions')]
    serialized = json.dumps(rows)
    assert cookie not in serialized and ACCESS not in serialized


def test_failed_login_window_expires_without_erasing_live_sessions(setup):
    app, _, operator, _, _ = setup
    operator_login(operator)
    for _ in range(5):
        assert operator.post('/api/operator/session', json={'access_token': 'wrong'}).status_code == 401
    assert operator.post('/api/operator/session', json={'access_token': ACCESS}).status_code == 429
    assert operator.get('/api/operator/session').status_code == 200
    future = app.state.bank.now() + timedelta(seconds=61)
    app.state.bank.clock = lambda: future
    assert operator.post('/api/operator/session', json={'access_token': ACCESS}).status_code == 200


def test_foreign_origin_cannot_claim_or_reply_even_with_cookie_and_csrf(setup):
    _, customer, operator, plan, version = setup
    case = share(customer, plan, version)['id']
    operator_login(operator)
    response = operator.post('/api/operator/cases/' + case + '/claim',
        headers={'Origin': 'https://foreign.invalid'},
        json={'expected_version': 1, 'request_key': 'foreign-origin-1'})
    assert response.status_code == 403
    assert customer.get('/api/operator-cases/' + case).json()['version'] == 1


def test_user_csrf_cannot_substitute_for_operator_csrf(setup):
    _, customer, operator, plan, version = setup
    case = share(customer, plan, version)['id']
    operator_login(operator)
    operator.headers['X-CSRF-Token'] = customer.headers['X-CSRF-Token']
    assert operator.post('/api/operator/cases/' + case + '/claim', json={
        'expected_version': 1, 'request_key': 'wrong-csrf-key'}).status_code == 403


def test_original_snapshot_is_immutable_while_current_fact_is_fresh(setup):
    app, customer, operator, plan, version = setup
    created = share(customer, plan, version)
    op = created['snapshot']['operations'][0]
    assert op['status'] == 'CANCELLED'
    with app.state.bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    before = financial(app.state.bank)
    operator_login(operator)
    actual = operator.get('/api/operator/cases/' + created['id']).json()
    assert actual['snapshot'] == created['snapshot']
    assert actual['current_facts']['operations'][0]['status'] == 'UNKNOWN'
    assert financial(app.state.bank) == before


def test_reply_requires_claim_and_changed_key_payload_is_rejected(setup):
    _, customer, operator, plan, version = setup
    case = share(customer, plan, version)['id']
    operator_login(operator)
    assert operator.post('/api/operator/cases/' + case + '/reply', json={
        'expected_version': 1, 'request_key': 'reply-too-early', 'reply': '示例'}).status_code == 409
    url = '/api/operator/cases/' + case + '/claim'
    body = {'expected_version': 1, 'request_key': 'same-claim-key'}
    assert operator.post(url, json=body).status_code == 200
    replay = operator.post(url, json=body)
    assert replay.status_code == 200 and replay.json()['replayed'] is True
    assert operator.post(url, json={**body, 'expected_version': 2}).status_code == 409


def test_replayed_reply_after_withdraw_stays_forbidden(setup):
    _, customer, operator, plan, version = setup
    case = share(customer, plan, version)['id']
    operator_login(operator)
    assert operator.post('/api/operator/cases/' + case + '/claim', json={
        'expected_version': 1, 'request_key': 'claim-replay-test'}).status_code == 200
    body = {'expected_version': 2, 'request_key': 'reply-replay-test', 'reply': '演示回复，不是银行处理结果。'}
    assert operator.post('/api/operator/cases/' + case + '/reply', json=body).status_code == 200
    assert customer.post('/api/operator-cases/' + case + '/withdraw', json={
        'expected_version': 3, 'request_key': 'withdraw-replay-test'}).status_code == 200
    assert operator.post('/api/operator/cases/' + case + '/reply', json=body).status_code == 404
    assert customer.get('/api/operator-cases/' + case).json()['snapshot'] is None
    assert customer.post('/api/plans/' + plan + '/operator-case', json={
        'expected_version': version, 'confirmed': True, 'request_key': 'new-share-closed'}).status_code == 409


def test_concurrent_same_share_key_has_one_effect(setup):
    app, _, _, plan, version = setup
    service = app.state.operator_cases
    before = financial(app.state.bank)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.share('demo', plan, version, 'concurrent-share-key', True), range(2)))
    assert len({r['id'] for r in results}) == 1
    assert sorted(r['replayed'] for r in results) == [False, True]
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM operator_cases').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM operator_case_events').fetchone()[0] == 1
    assert financial(app.state.bank) == before


def test_concurrent_claim_vs_withdraw_has_one_winner(setup):
    app, customer, _, plan, version = setup
    case = share(customer, plan, version)['id']
    before = financial(app.state.bank)
    service = app.state.operator_cases
    def apply(action):
        try:
            if action == 'claim':
                return 200, service.act_operator(case, 1, 'claim', None, 'concurrent-claim')['status']
            return 200, service.withdraw('demo', case, 1, 'concurrent-withdraw')['status']
        except DomainError as error:
            return error.status, error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ('claim', 'withdraw')))
    assert sum(code == 200 for code, _ in results) == 1
    assert all(code in (200, 404, 409) for code, _ in results)
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM operator_case_events').fetchone()[0] == 2
    assert financial(app.state.bank) == before


def test_shared_scope_does_not_expand_to_later_linked_operation(setup):
    from ff_agent.plan_ownership import bind_operation
    app, customer, operator, plan, version = setup
    original = share(customer, plan, version)
    later = app.state.bank.prepare_transfer('demo', 'p_dad', 10000, 'later-unshared-operation')
    with app.state.bank.transaction() as db:
        index = db.execute('SELECT idx FROM plan_store_nodes WHERE plan_id=? ORDER BY idx LIMIT 1', (plan,)).fetchone()[0]
        bind_operation(db, 'demo', later['id'], (plan, index), 'migration')
    before = financial(app.state.bank)
    operator_login(operator)
    actual = operator.get('/api/operator/cases/' + original['id']).json()
    assert actual['snapshot'] == original['snapshot']
    assert later['id'] not in json.dumps(actual, ensure_ascii=False)
    assert {item['id'] for item in actual['current_facts']['operations']} == {item['id'] for item in original['snapshot']['operations']}
    assert financial(app.state.bank) == before


def test_relogin_revokes_previous_cookie_only_on_success(setup):
    _, _, operator, _, _ = setup
    operator_login(operator)
    old = operator.cookies.get('ff_operator')
    assert operator.post('/api/operator/session', json={'access_token': 'bad'}).status_code == 401
    assert operator.get('/api/operator/session', headers={'Cookie': 'ff_operator=' + old}).status_code == 200
    operator_login(operator)
    assert operator.cookies.get('ff_operator') != old
    assert operator.get('/api/operator/session', headers={'Cookie': 'ff_operator=' + old}).status_code == 401
    assert operator.get('/api/operator/session').status_code == 200


def test_exact_plan_lookup_is_read_only_and_owner_bound(setup):
    app, customer, _, plan, version = setup
    before = financial(app.state.bank)
    url = '/api/plans/' + plan + '/operator-case'
    for _ in range(2):
        response = customer.get(url)
        assert response.status_code == 200, response.text
        assert response.json() == {'case': None}
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM operator_cases').fetchone()[0] == 0
    created = share(customer, plan, version)
    assert customer.get(url).json()['case']['id'] == created['id']
    from test_handoff_progress_api import login
    login(customer, 'guest')
    assert customer.get(url).status_code == 404
    assert financial(app.state.bank) == before


def test_re_share_after_withdraw_has_explicit_closed_message(setup):
    _, customer, _, plan, version = setup
    original = share(customer, plan, version)
    assert customer.post('/api/operator-cases/' + original['id'] + '/withdraw', json={
        'expected_version': 1, 'request_key': 'withdraw-for-closed'}).status_code == 200
    retry = customer.post('/api/plans/' + plan + '/operator-case', json={
        'expected_version': version, 'confirmed': True, 'request_key': 'new-share-key-closed'})
    assert retry.status_code == 409
    assert retry.json()['error']['code'] == 'CASE_WITHDRAWN'


def test_share_preview_is_read_only_minimal_and_owner_bound(setup):
    app, customer, _, plan, _ = setup
    before = financial(app.state.bank)
    response = customer.get('/api/plans/' + plan + '/operator-case/preview')
    assert response.status_code == 200, response.text
    preview = response.json()['preview']
    assert preview['plan_id'] == plan and preview['operations']
    assert '13800000001' not in json.dumps(preview)
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM operator_cases').fetchone()[0] == 0
    from test_handoff_progress_api import login
    login(customer, 'guest')
    assert customer.get('/api/plans/' + plan + '/operator-case/preview').status_code == 404
    assert financial(app.state.bank) == before
