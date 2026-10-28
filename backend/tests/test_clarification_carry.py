"""A clarification is a bound draft, never a confirmation or enduring authority."""
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


def login(client, user='demo'):
    response = client.post('/api/session', json={'user': user})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def chat(client, text, key):
    response = client.post('/api/chat', json={'text': text, 'request_key': key})
    assert response.status_code == 200
    return response.json()


def ops(reply):
    return [c['operation'] for c in reply['cards'] if c['type'] == 'operation']


def balances(bank):
    with bank.transaction() as db:
        return {t: [tuple(x) for x in db.execute('SELECT * FROM ' + t + ' ORDER BY id')]
                for t in ('accounts', 'transactions')}


def test_ambiguous_amount_then_number_then_correction_stays_unconfirmed(tmp_path):
    app = create_app(tmp_path / 'amount-draft.db')
    with TestClient(app) as client:
        login(client)
        bank = app.state.bank
        before = balances(bank)
        assert ops(chat(client, '给妈妈转几百块', 'ambiguous')) == []
        first = ops(chat(client, '500元', 'fill'))
        assert len(first) == 1
        assert first[0]['payload']['payee_id'] == 'p_mom'
        assert first[0]['payload']['amount_cents'] == 50000
        assert first[0]['status'] == 'WAITING_CONFIRMATION'
        second = ops(chat(client, '改成300元', 'correct'))
        assert len(second) == 1 and second[0]['id'] != first[0]['id']
        assert second[0]['payload']['amount_cents'] == 30000
        assert second[0]['status'] == 'WAITING_CONFIRMATION'
        assert bank.operation('demo', first[0]['id'])['status'] == 'CANCELLED'
        assert client.post('/api/operations/' + first[0]['id'] + '/confirm', json={}).status_code == 409
        chat(client, '确认', 'chat-is-not-confirmation')
        assert bank.operation('demo', second[0]['id'])['status'] == 'WAITING_CONFIRMATION'
        assert balances(bank) == before


@pytest.mark.parametrize('boundary', ['new_session', 'guest', 'expiry', 'new_task', 'negation', 'safety_epoch'])
def test_amount_clarification_does_not_survive_its_boundary(tmp_path, boundary):
    app = create_app(tmp_path / (boundary + '.db'))
    with TestClient(app) as client:
        login(client)
        bank = app.state.bank
        chat(client, '给妈妈转几百块', 'draft')
        if boundary == 'new_session': login(client)
        elif boundary == 'guest': login(client, 'guest')
        elif boundary == 'expiry':
            expired = bank.now() + timedelta(seconds=601)
            bank.clock = lambda: expired
        elif boundary == 'new_task': chat(client, '查今年账单', 'other-business')
        elif boundary == 'negation': chat(client, '算了别转了', 'stop')
        else:
            bank.activate_safety_hold('demo', {'codes': ['SAFE_ACCOUNT'], 'reasons': ['test'], 'advice': '先核实', 'hold': True}, 'new-epoch')
        assert ops(chat(client, '500元', 'after-boundary')) == []


def test_year_only_answer_completes_read_only_period_clarification(tmp_path):
    app = create_app(tmp_path / 'bill-year-draft.db')
    with TestClient(app) as client:
        login(client)
        before = balances(app.state.bank)
        chat(client, '看看去年和今年一共花了多少', 'range')
        reply = chat(client, '2026年', 'single-year')
        bill = [c['bill'] for c in reply['cards'] if c['type'] == 'bill']
        assert len(bill) == 1 and bill[0]['period'] == '2026'
        assert ops(reply) == []
        assert balances(app.state.bank) == before


@pytest.mark.parametrize('boundary', ['new_session', 'expiry', 'new_task', 'negation'])
def test_bill_pending_year_cannot_leak_across_boundaries(tmp_path, boundary):
    app = create_app(tmp_path / ('year-' + boundary + '.db'))
    with TestClient(app) as client:
        login(client)
        bank = app.state.bank
        chat(client, '看看去年和今年一共花了多少', 'range')
        if boundary == 'new_session': login(client)
        elif boundary == 'expiry':
            expired = bank.now() + timedelta(seconds=601)
            bank.clock = lambda: expired
        elif boundary == 'new_task': chat(client, '查今年账单', 'other-business')
        else: chat(client, '算了不查了', 'stop')
        reply = chat(client, '2026年', 'after-boundary')
        assert not any(c['type'] == 'bill' for c in reply['cards'])


def test_note_cannot_supply_a_payee_to_a_numeric_followup(tmp_path):
    app = create_app(tmp_path / 'no-note-authorization.db')
    with TestClient(app) as client:
        login(client)
        chat(client, '记住：我通常给妈妈转账', 'preference')
        assert ops(chat(client, '500元', 'bare-amount')) == []


def test_bill_clarification_replaces_a_pending_transfer_not_its_amount(tmp_path):
    app = create_app(tmp_path / 'switch-to-bill.db')
    with TestClient(app) as client:
        login(client)
        chat(client, '给妈妈转一点钱', 'pending-transfer')
        chat(client, '看看去年和今年一共花了多少', 'range')
        reply = chat(client, '2026', 'year-only')
        assert ops(reply) == []
        assert any(c['type'] == 'bill' and c['bill']['period'] == '2026' for c in reply['cards'])


def test_pending_bill_first_timestamp_is_not_extended_by_wrong_answer(tmp_path):
    app = create_app(tmp_path / 'bill-fixed-ttl.db')
    with TestClient(app) as client:
        login(client)
        bank = app.state.bank
        chat(client, '看看去年和今年一共花了多少', 'range')
        later = bank.now() + timedelta(seconds=590)
        bank.clock = lambda: later
        chat(client, '1800年', 'invalid-year')
        later = later + timedelta(seconds=20)
        reply = chat(client, '2026年', 'expired-year')
        assert not any(c['type'] == 'bill' for c in reply['cards'])


def test_correct_month_reuses_the_explicit_year_not_the_current_year(tmp_path):
    app = create_app(tmp_path / 'month-with-existing-year.db')
    with TestClient(app) as client:
        login(client)
        chat(client, '2025年13月账单', 'bad-month')
        reply = chat(client, '3月', 'correct-month')
        assert any(c['type'] == 'bill' and c['bill']['period'] == '2025-03' for c in reply['cards'])
