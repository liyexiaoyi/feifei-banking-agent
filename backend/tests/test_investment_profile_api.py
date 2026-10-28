"""Explicit demo assessments are personal data, never trade authorization."""
import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from test_handoff_progress_api import login

ANSWERS = {name: 3 for name in ('loss_capacity', 'liquidity', 'horizon', 'experience', 'income_stability', 'attitude')}


@pytest.fixture
def case(tmp_path):
    app = create_app(tmp_path / 'profile.db')
    with TestClient(app) as client:
        login(client)
        yield app, client


def body(answers=None, version=0, key='profile-first-key'):
    return {'answers': copy.deepcopy(ANSWERS if answers is None else answers),
            'survey_version': 'demo-risk-v1', 'expected_version': version,
            'request_key': key, 'confirmed': True}


def finances(bank):
    with bank.transaction() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('accounts', 'operations', 'transactions', 'wealth_positions')}


def submit(client, payload=None):
    response = client.post('/api/investment-profile', json=body() if payload is None else payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_questionnaire_has_no_default_answers_and_profile_starts_missing(case):
    _, client = case
    response = client.get('/api/investment-profile/questionnaire')
    assert response.status_code == 200, response.text
    survey = response.json()
    assert survey['survey_version'] == 'demo-risk-v1' and survey['simulated'] is True
    assert {q['id'] for q in survey['questions']} == set(ANSWERS)
    assert all(len(q['choices']) == 4 and 'answer' not in q and 'default' not in q for q in survey['questions'])
    assert client.get('/api/investment-profile').json()['status'] == 'NONE'


def test_complete_explicit_submission_is_explainable_and_cannot_move_money(case):
    app, client = case
    before = finances(app.state.bank)
    result = submit(client)
    assert result['status'] == 'VALID' and result['simulated'] is True
    profile = result['profile']
    assert profile['version'] == 1 and profile['score'] == 18 and profile['level'] == 5
    assert profile['answers'] == ANSWERS and len(profile['contributions']) == 6
    assert profile['expires_at'] - profile['created_at'] == 180 * 86400
    assert finances(app.state.bank) == before
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM memory_notes').fetchone()[0] == 0


@pytest.mark.parametrize('change', [
    {'confirmed': False}, {'confirmed': 1}, {'expected_version': True},
    {'answers': {**ANSWERS, 'experience': True}}, {'answers': {**ANSWERS, 'experience': 4}},
    {'answers': {**ANSWERS, 'age': 75}}, {'answers': {'experience': 3}},
    {'survey_version': 'unknown'}, {'user_id': 'guest'}, {'request_key': 'short'},
])
def test_invalid_partial_or_inferred_answers_are_rejected(case, change):
    _, client = case
    payload = body(); payload.update(change)
    assert client.post('/api/investment-profile', json=payload).status_code == 422
    assert client.get('/api/investment-profile').json()['status'] == 'NONE'


def test_zero_loss_capacity_caps_the_explained_level(case):
    _, client = case
    result = submit(client, body({**ANSWERS, 'loss_capacity': 0}))
    assert result['profile']['score'] == 15 and result['profile']['level'] == 1
    assert result['profile']['caps']


def test_replay_keeps_current_profile_and_does_not_extend_expiry(case):
    app, client = case
    original = body()
    first = submit(client, original)
    assert submit(client, original)['replayed'] is True
    assert client.post('/api/investment-profile', json=body({**ANSWERS, 'experience': 2})).status_code == 409
    lower = submit(client, body({name: 0 for name in ANSWERS}, 1, 'profile-second-key'))
    replay = submit(client, original)
    assert replay['profile']['version'] == 2 and replay['profile']['level'] == 1
    assert replay['profile']['expires_at'] == lower['profile']['expires_at']
    assert replay['receipt']['submitted_version'] == first['profile']['version']
    assert client.post('/api/investment-profile', json=body(version=0, key='stale-profile-key')).status_code == 409


def test_expiry_is_absolute_read_only_and_not_refreshed_by_replay(case):
    app, client = case
    first = submit(client)
    future = app.state.bank.now() + timedelta(days=180, seconds=1)
    app.state.bank.clock = lambda: future
    before = finances(app.state.bank)
    expired = client.get('/api/investment-profile').json()
    assert expired['status'] == 'EXPIRED'
    replay = submit(client)
    assert replay['status'] == 'EXPIRED'
    assert replay['profile']['expires_at'] == first['profile']['expires_at']
    assert finances(app.state.bank) == before


def test_profile_is_owner_bound_and_post_requires_csrf(case):
    _, client = case
    submit(client)
    login(client, 'guest')
    assert client.get('/api/investment-profile').json()['status'] == 'NONE'
    token = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/investment-profile', json=body()).status_code == 403
    client.headers['X-CSRF-Token'] = token
    guest = submit(client)
    assert guest['profile']['version'] == 1
    login(client)
    assert client.get('/api/investment-profile').json()['profile']['version'] == 1


def test_comparison_is_read_only_and_does_not_guess_assessment(case):
    app, client = case
    before = finances(app.state.bank)
    response = client.get('/api/wealth/compare?product=demo_cash&product=demo_bond')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['simulated'] is True and result['policy_version'] == 'demo-products-v1'
    assert len(result['products']) == 2 and all(p['eligible'] is None for p in result['products'])
    submit(client, body({name: 0 for name in ANSWERS}))
    after = client.get('/api/wealth/compare').json()
    products = {p['id']: p for p in after['products']}
    assert products['demo_cash']['eligible'] is True and products['demo_bond']['eligible'] is False
    assert after['assessment']['profile']['version'] == 1
    assert finances(app.state.bank) == before


@pytest.mark.parametrize('query', ['product=demo_cash&product=demo_cash', 'product=unknown', 'user_id=guest'])
def test_comparison_rejects_ambiguous_or_unsupported_query(case, query):
    _, client = case
    assert client.get('/api/wealth/compare?' + query).status_code == 422
