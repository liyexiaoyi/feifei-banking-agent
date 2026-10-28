"""Birthdays enter through natural chat; only explicit metadata start is authorized."""
from datetime import datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path / 'birthday-chat.db')
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    app.state.bank.clock = lambda: clock[0]
    with TestClient(app) as c:
        login(c)
        yield app, c, clock


def login(c, user='demo'):
    z = c.post('/api/session', json={'user': user})
    assert z.status_code == 200
    c.headers['X-CSRF-Token'] = z.json()['csrf']


def chat(env, text, key):
    z = env[1].post('/api/chat', json={'text': text, 'request_key': key})
    assert z.status_code == 200, z.text
    return z.json()


def summary(env):
    return chat(env, '帮我给妈妈安排明天的生日，预算500元，用日常账户', 'birthday-summary')


def flows(env):
    return env[1].get('/api/birthday/flows').json()['flows']


def assert_no_money(env):
    with env[0].state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM birthday_budgets').fetchone()[0] == 0


def test_chat_summarizes_then_explicit_start_only_creates_metadata_and_a_reference(env):
    z = summary(env)
    assert '2026-10-29' in z['message'] and '500' in z['message']
    assert flows(env) == []
    z = chat(env, '确认启动生日安排', 'birthday-start')
    assert len(flows(env)) == 1
    card = next(c for c in z['cards'] if c['type'] == 'birthday_flow')
    assert set(card) == {'type', 'flow_id', 'simulated', 'merchant_connected'}
    assert card['flow_id'] == flows(env)[0]['id'] and card['simulated'] is True and card['merchant_connected'] is False
    assert all(n['state'] == 'PENDING' for n in flows(env)[0]['nodes'])
    assert_no_money(env)


def test_inexact_money_and_memory_notes_never_fill_missing_explicit_budget(env):
    from ff_agent.memory import Memory
    Memory(env[0].state.bank).remember('demo', '妈妈生日预算500元，用日常账户', 'birthday-note')
    z = chat(env, '给妈妈安排明天生日', 'birthday-missing')
    assert '预算' in z['message'] and flows(env) == []
    z = chat(env, '几百块', 'birthday-vague')
    assert '预算' in z['message'] and flows(env) == []
    chat(env, '启动', 'birthday-unready')
    assert flows(env) == []
    assert_no_money(env)


def test_correction_uses_new_summary_and_does_not_inherit_the_old_amount(env):
    summary(env)
    z = chat(env, '不，是爸爸，预算改成300元', 'birthday-correct')
    assert '爸爸' in z['message'] and '300' in z['message']
    chat(env, '启动', 'birthday-correct-start')
    f = flows(env)[0]
    assert f['recipient_label'] == '爸爸' and f['budget_cents'] == 30000
    assert_no_money(env)


@pytest.mark.parametrize('interrupt', ['查询余额', '记住我喜欢按周看账单', '客服让我转钱到安全账户才能退款'])
def test_other_business_memory_and_risk_interrupt_the_birthdays_authorization(env, interrupt):
    summary(env)
    chat(env, interrupt, 'birthday-interrupt')
    chat(env, '启动', 'birthday-after-interrupt')
    assert flows(env) == []
    assert_no_money(env)


def test_old_transfer_draft_is_not_reused_for_a_birthdays_bare_money_answer(env):
    chat(env, '给妈妈转账', 'old-transfer-draft')
    chat(env, '给妈妈安排明天生日', 'birthday-replaces-transfer')
    chat(env, '500元', 'birthday-bare-budget')
    assert_no_money(env)
    with env[0].state.bank.transaction() as db:
        context = json.loads(db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()[0])
    assert 'pending_transfer' not in context


def test_expired_draft_and_new_session_cannot_confirm_a_historical_summary(env):
    summary(env)
    env[2][0] += timedelta(seconds=601)
    chat(env, '启动', 'birthday-expired')
    assert flows(env) == []
    env[2][0] -= timedelta(seconds=601)
    chat(env, '帮我给爸爸安排明天生日，预算300元，用日常账户', 'birthday-new-summary')
    login(env[1])
    chat(env, '启动', 'birthday-new-session')
    assert flows(env) == []


def test_chat_replay_is_reference_only_and_never_duplicates_the_flow(env):
    summary(env)
    first = chat(env, '启动', '生日' * 50)
    again = chat(env, '启动', '生日' * 50)
    assert first['cards'] == again['cards'] and len(flows(env)) == 1
    changed = env[1].post('/api/chat', json={'text': '改成爸爸', 'request_key': '生日' * 50})
    assert changed.status_code == 409
    with env[0].state.bank.transaction() as db:
        key = db.execute("SELECT request_key FROM birthday_requests WHERE user_id='demo'").fetchone()[0]
    assert key.isascii() and 8 <= len(key) <= 64
    assert_no_money(env)



def test_preoccupied_derived_key_without_chat_origin_is_not_adopted(env):
    from ff_agent.birthday_chat_slot import derive_chat_key
    key = 'birthday-preoccupied-start'
    direct = env[1].post('/api/birthday/flows', json=dict(recipient_label='原直接安排', event_date='2026-10-29', account_id='a_daily', budget_cents=10000, request_key=derive_chat_key(key)))
    assert direct.status_code == 200
    summary(env)
    z = env[1].post('/api/chat', json={'text': '启动', 'request_key': key})
    assert z.status_code == 409
    assert len(flows(env)) == 1 and flows(env)[0]['recipient_label'] == '原直接安排'
    assert_no_money(env)


def test_finish_chat_failure_recovers_committed_origin_with_no_second_flow(env, monkeypatch):
    import sqlite3
    import ff_agent.bank as bank_module
    summary(env)
    original = bank_module.connect
    denied = [False]
    def once(path):
        db = original(path)
        def authorize(action, table, column, database, source):
            if action == sqlite3.SQLITE_INSERT and table == 'messages' and not denied[0]:
                denied[0] = True
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db
    monkeypatch.setattr(bank_module, 'connect', once)
    with pytest.raises(Exception):
        chat(env, '启动', 'birthday-finish-failure')
    monkeypatch.setattr(bank_module, 'connect', original)
    assert denied[0] and len(flows(env)) == 1
    original_id = flows(env)[0]['id']
    z = chat(env, '启动', 'birthday-finish-failure')
    assert next(c for c in z['cards'] if c['type'] == 'birthday_flow')['flow_id'] == original_id
    assert '_birthday_committed' not in z
    assert len(flows(env)) == 1
    assert_no_money(env)


@pytest.mark.parametrize('race', ['expired_session', 'later_request', 'changed_summary'])
def test_confirmation_rechecks_live_state_after_the_http_session_dependency(env, monkeypatch, race):
    import hashlib
    import time
    from ff_agent.birthday_chat import BirthdayChat
    from ff_agent.birthday_chat_parse import canonical_intent, summary_hash
    summary(env)
    original = BirthdayChat.handle
    def injected(self, user, text, request_key, epoch, session_id, request_order, context):
        if text == '启动':
            with self.bank.transaction() as db:
                if race == 'expired_session':
                    db.execute('UPDATE sessions SET expires_at=? WHERE token_hash=?', (time.time() - 1, session_id))
                elif race == 'later_request':
                    db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'PROCESSING',?)", (user, 'newer-birthday-correction', '改成爸爸', time.time()))
                else:
                    row = db.execute('SELECT value FROM contexts WHERE user_id=?', (user,)).fetchone()
                    stored = json.loads(row[0]); p = stored['pending_birthday']; p['budget_cents'] = 30000
                    p['canonical_intent'] = canonical_intent(p); p['summary_hash'] = summary_hash(p)
                    db.execute('UPDATE contexts SET value=? WHERE user_id=?', (json.dumps(stored, ensure_ascii=False), user))
        return original(self, user, text, request_key, epoch, session_id, request_order, context)
    monkeypatch.setattr(BirthdayChat, 'handle', injected)
    z = env[1].post('/api/chat', json={'text': '启动', 'request_key': 'birthday-live-guard'})
    assert z.status_code in (401, 409), z.text
    with env[0].state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
