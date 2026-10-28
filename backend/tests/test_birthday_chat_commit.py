"""Confirmation must check the live original conversation and create in one transaction."""
from datetime import datetime
from copy import deepcopy
import importlib
import importlib.util
import json
import time

import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.birthday_store import BirthdayStore
from ff_agent.birthday_chat_parse import canonical_intent, summary_hash
from ff_agent.birthday_chat_slot import derive_chat_key


NOW = datetime.fromisoformat('2026-10-28T12:00:00+08:00')


def setup(tmp_path):
    assert importlib.util.find_spec('ff_agent.birthday_chat_commit'), 'atomic birthday confirmation missing'
    module = importlib.import_module('ff_agent.birthday_chat_commit')
    b = Bank(tmp_path / 'commit.db', clock=lambda: NOW); s = BirthdayStore(b)
    module.bootstrap_birthday_chat(b)
    with b.transaction() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('live-session', 'demo', 'csrf-test', time.time() + 3600))
        db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'DONE',?)", ('demo', 'summary-key', '给妈妈安排生日', time.time()))
        origin = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'PROCESSING',?)", ('demo', 'start-key', '启动', time.time()))
        order = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute('INSERT INTO chat_request_epochs VALUES(?,?,?)', ('demo', 'start-key', ''))
        p = dict(stage='SUMMARIZED', recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily',
                 budget_cents=50000, asked='confirm', session_id='live-session', epoch='', started_at=NOW.timestamp(),
                 origin_order=origin, summary_order=origin, canonical_intent=None, summary_hash=None)
        p['canonical_intent'] = canonical_intent(p); p['summary_hash'] = summary_hash(p)
        stored = dict(pending_birthday=p, working_updated_at=NOW.timestamp(), safety_epoch='', session_id='live-session', request_order=origin)
        db.execute('INSERT INTO contexts VALUES(?,?)', ('demo', json.dumps(stored, ensure_ascii=False)))
    return module, b, s, p, order


def confirm(env, expected=None):
    m, b, s, p, order = env
    return m.confirm_birthday(b, s, 'demo', '启动', 'start-key', '', 'live-session', order, p if expected is None else expected)


def test_atomic_start_links_original_request_and_clears_slot_without_money(tmp_path):
    env = setup(tmp_path); f = confirm(env)
    with env[1].transaction() as db:
        link = db.execute("SELECT flow_id FROM birthday_chat_links WHERE user_id='demo' AND request_key='start-key'").fetchone()
        assert link and link[0] == f['id']
        stored = json.loads(db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()[0])
        assert 'pending_birthday' not in stored and stored['request_order'] == env[4]
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


@pytest.mark.parametrize('damage', ['expired_session', 'later_user_request', 'changed_expected', 'wrong_epoch'])
def test_changed_live_authority_is_rejected_before_metadata_creation(tmp_path, damage):
    env = setup(tmp_path); expected = deepcopy(env[3])
    with env[1].transaction() as db:
        if damage == 'expired_session':
            db.execute('UPDATE sessions SET expires_at=?', (time.time() - 1,))
        elif damage == 'later_user_request':
            db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'PROCESSING',?)", ('demo', 'later-key', '改成爸爸', time.time()))
        elif damage == 'wrong_epoch':
            db.execute("UPDATE chat_request_epochs SET epoch='changed'")
        else:
            expected['budget_cents'] = 30000
            expected['canonical_intent'] = canonical_intent(expected); expected['summary_hash'] = summary_hash(expected)
    with pytest.raises(DomainError):
        confirm(env, expected)
    with env[1].transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 0


def test_failure_after_locked_create_rolls_back_source_and_context_together(tmp_path, monkeypatch):
    env = setup(tmp_path); original = env[2]._create_locked
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('after-real-create')
    monkeypatch.setattr(env[2], '_create_locked', broken)
    with pytest.raises(RuntimeError):
        confirm(env)
    with env[1].transaction() as db:
        for table in ('birthday_flows', 'birthday_nodes', 'birthday_requests', 'birthday_chat_links'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert 'pending_birthday' in json.loads(db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()[0])


def test_unlinked_preoccupied_derived_key_cannot_be_adopted(tmp_path):
    env = setup(tmp_path)
    env[2].create('demo', '原直接安排', '2026-10-29', 'a_daily', 10000, derive_chat_key('start-key'))
    with pytest.raises(DomainError) as e:
        confirm(env)
    assert e.value.code == 'KEY_CONFLICT'
    with env[1].transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM birthday_chat_links').fetchone()[0] == 0
