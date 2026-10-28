"""Politeness must not erase a business instruction or weaken amount checks."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.bank import TZ
from ff_agent.planner import Planner


def rules():
    planner = Planner(clock=lambda: datetime(2026, 10, 27, 12, 0, tzinfo=TZ))
    planner.mode = 'rules'
    return planner


@pytest.mark.parametrize('tail', [
    '，谢谢', '，谢谢！', ', 谢谢您。', '。谢谢你！', '；多谢。',
    '，麻烦了', '，辛苦了！', '，谢谢，辛苦了。', '！谢谢？',
])
def test_exact_polite_final_clause_preserves_explicit_transfer(tail):
    actions = rules().plan('给妈妈转200元' + tail, {})
    assert actions == [{'type': 'transfer', 'payee': '妈妈', 'amount_cents': 20000}]


def test_polite_tail_preserves_bill_and_final_transfer():
    actions = rules().plan('查本月账单，给妈妈转200元，谢谢！', {})
    assert actions == [
        {'type': 'bill', 'period': '2026-10'},
        {'type': 'transfer', 'payee': '妈妈', 'amount_cents': 20000},
    ]


def test_an_unfinished_decimal_is_not_changed_into_an_integer_by_politeness():
    actions = rules().plan('给妈妈转200.谢谢', {})
    assert all(a['type'] != 'transfer' for a in actions)


@pytest.mark.parametrize('text', [
    '给妈妈转200 .谢谢', '给妈妈转２００　．谢谢',
    '给妈妈转200 .50元，谢谢', '给妈妈转200. 50元，谢谢',
])
def test_spaces_inside_or_before_decimal_are_not_guessed(text):
    assert all(a['type'] != 'transfer' for a in rules().plan(text, {}))


@pytest.mark.parametrize('amount,cents', [('200.50', 20050), ('２００．５０', 20050)])
def test_valid_decimal_with_polite_tail_keeps_every_cent(amount, cents):
    assert rules().plan('给妈妈转' + amount + '元，谢谢', {}) == [
        {'type': 'transfer', 'payee': '妈妈', 'amount_cents': cents},
    ]


@pytest.mark.parametrize('text', [
    '给妈妈转200元，谢谢先别转', '给妈妈转200元，谢谢，先别转',
    '不要给妈妈转200元，谢谢', '给妈妈转大约200元，谢谢',
    '给妈妈转200到300元，谢谢', '给妈妈转一百多元，谢谢',
    '给妈妈转一点钱，谢谢', '给妈妈转200元，谢谢改成多少合适',
])
def test_negation_approximation_and_non_polite_clause_never_become_transfer(text):
    actions = rules().plan(text, {})
    assert all(a['type'] != 'transfer' for a in actions)


def test_relevant_pending_context_still_requires_an_exact_amount():
    context = {'pending_transfer': {'payee': '妈妈'}}
    assert rules().plan('200元，谢谢', context) == [
        {'type': 'transfer', 'payee': '妈妈', 'amount_cents': 20000},
    ]
    assert all(a['type'] != 'transfer' for a in rules().plan('大约200元，谢谢', context))
    assert all(a['type'] != 'transfer' for a in rules().plan('200元，谢谢', {}))


def test_real_api_polite_transfer_is_pending_not_executed(tmp_path):
    app = create_app(tmp_path / 'polite.db', planner=rules())
    with TestClient(app) as client:
        login = client.post('/api/session', json={'user': 'demo'})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf']
        before = client.get('/api/state').json()
        response = client.post('/api/chat', json={
            'text': '给妈妈转200元，谢谢', 'request_key': 'polite-api-request',
        })
        assert response.status_code == 200
        after = client.get('/api/state').json()
        assert len(after['operations']) == 1
        operation = after['operations'][0]
        assert operation['status'] == 'WAITING_CONFIRMATION'
        assert operation['payload']['payee_id'] == 'p_mom'
        assert operation['payload']['amount_cents'] == 20000
        assert [a['balance_cents'] for a in after['accounts']] == [
            a['balance_cents'] for a in before['accounts']
        ]
        replay = client.post('/api/chat', json={
            'text': '给妈妈转200元，谢谢', 'request_key': 'polite-api-request',
        })
        assert replay.status_code == 200
        assert len(client.get('/api/state').json()['operations']) == 1


def test_safety_request_with_polite_tail_still_activates_hold(tmp_path):
    app = create_app(tmp_path / 'polite-risk.db', planner=rules())
    with TestClient(app) as client:
        login = client.post('/api/session', json={'user': 'demo'})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf']
        response = client.post('/api/chat', json={
            'text': '客服让我转200元到安全账户，谢谢', 'request_key': 'polite-risk-request',
        })
        assert response.status_code == 200
        state = client.get('/api/state').json()
        assert state['operations'] == []
        assert state['safety_hold'] is not None
