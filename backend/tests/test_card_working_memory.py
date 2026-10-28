from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from ff_agent.bank import Bank, TZ
from ff_agent.memory import Memory


def fixture(tmp_path):
    now = [datetime(2026, 10, 27, 1, 0, tzinfo=TZ)]
    bank = Bank(tmp_path / 'card-memory.db', clock=lambda: now[0])
    return Memory(bank), bank, now


def pending(**extra):
    return {'mode': 'prepare', 'action': 'report_lost', **extra}


def raw_context(bank):
    with bank.transaction() as db:
        return json.loads(db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()[0])


def save(memory, value, **extra):
    memory.save_working('demo', value, '', session_id='first', request_order=1, **extra)


def test_card_pending_survives_only_same_session_and_writer_does_not_mutate_input(tmp_path):
    memory, bank, now = fixture(tmp_path)
    source = {'pending_card': pending(extra='drop'), 'memories': [{'action': 'restore'}]}
    original = deepcopy(source)
    save(memory, source)
    expected = pending(started_at=now[0].timestamp(), operation_watermark=0)
    assert memory.context('demo', '0826', 'first')['pending_card'] == expected
    assert source == original
    assert 'memories' not in raw_context(bank)
    assert 'pending_card' not in memory.context('demo', '0826', 'second')
    assert 'pending_card' not in memory.context('guest', '0826', 'first')


@pytest.mark.parametrize('seconds,valid', [(600, True), (600.001, False), (-1, False)])
def test_card_pending_has_independent_deadline_even_after_context_resave(tmp_path, seconds, valid):
    memory, _, now = fixture(tmp_path)
    save(memory, {'pending_card': pending()})
    stored = memory.context('demo', '选择', 'first')
    now[0] += timedelta(seconds=seconds)
    memory.save_working('demo', stored, '', session_id='first', request_order=2)
    assert ('pending_card' in memory.context('demo', '0826', 'first')) is valid


def test_other_user_operation_does_not_supersede_pending_but_new_owned_card_does(tmp_path):
    memory, bank, _ = fixture(tmp_path)
    save(memory, {'pending_card': pending()})
    bank.prepare_card_status('guest', 'card_b_daily', 'report_lost', 'guest-op', 1)
    assert 'pending_card' in memory.context('demo', '0826', 'first')
    bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'own-op', 1)
    assert 'pending_card' not in memory.context('demo', '0826', 'first')


def test_new_pending_records_current_owned_operation_watermark_not_global(tmp_path):
    memory, bank, _ = fixture(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 100, 'demo-transfer')
    bank.prepare_card_status('guest', 'card_b_daily', 'report_lost', 'guest-op', 1)
    save(memory, {'pending_card': pending(mode='stop', action='any')})
    with bank.transaction() as db:
        own_rowid = db.execute('SELECT rowid FROM operations WHERE id=?', (op['id'],)).fetchone()[0]
    assert raw_context(bank)['pending_card']['operation_watermark'] == own_rowid
    assert memory.context('demo', '0826', 'first')['pending_card']['mode'] == 'stop'


def test_recent_card_comes_from_owned_operation_and_uses_its_actual_time(tmp_path):
    memory, bank, now = fixture(tmp_path)
    op = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'own-op', 1)
    now[0] += timedelta(seconds=30)
    save(memory, {'recent_card': {'operation_id': op['id'], 'started_at': now[0].timestamp()}})
    expected = {'operation_id': op['id'], 'started_at': op['created_at']}
    assert memory.context('demo', '刚才那张', 'first')['recent_card'] == expected
    assert raw_context(bank)['recent_card'] == expected
    now[0] += timedelta(seconds=571)
    memory.save_working('demo', {'recent_card': {'operation_id': op['id']}}, '', 'first', 2)
    assert 'recent_card' not in memory.context('demo', '刚才那张', 'first')


@pytest.mark.parametrize('kind', ['foreign', 'transfer', 'invented'])
def test_writer_rejects_recent_pointer_without_owned_card_provenance(tmp_path, kind):
    memory, bank, _ = fixture(tmp_path)
    if kind == 'foreign':
        opid = bank.prepare_card_status('guest', 'card_b_daily', 'report_lost', 'foreign', 1)['id']
    elif kind == 'transfer':
        opid = bank.prepare_transfer('demo', 'p_mom', 100, 'transfer')['id']
    else:
        opid = 'invented'
    save(memory, {'recent_card': {'operation_id': opid}})
    assert 'recent_card' not in raw_context(bank)
    assert 'recent_card' not in memory.context('demo', '刚才那张', 'first')


def test_context_revalidates_ownership_after_pointer_was_saved(tmp_path):
    memory, bank, _ = fixture(tmp_path)
    op = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'own-op', 1)
    save(memory, {'recent_card': {'operation_id': op['id']}})
    assert 'recent_card' in memory.context('demo', '那张', 'first')
    with bank.transaction() as db:
        db.execute("UPDATE accounts SET user_id='guest' WHERE id='a_daily'")
    assert 'recent_card' not in memory.context('demo', '那张', 'first')


def test_epoch_gate_and_request_order_prevent_card_context_resurrection(tmp_path):
    memory, bank, _ = fixture(tmp_path)
    save(memory, {'pending_card': pending()})
    stored = raw_context(bank)
    with bank.transaction() as db:
        stored['safety_epoch'] = 'different-epoch'
        db.execute("UPDATE contexts SET value=? WHERE user_id='demo'", (json.dumps(stored),))
    assert 'pending_card' not in memory.context('demo', '0826', 'first')
    memory.save_working('demo', {}, '', 'first', 5)
    memory.save_working('demo', {'pending_card': pending()}, '', 'first', 4)
    assert 'pending_card' not in memory.context('demo', '0826', 'first')
    memory.save_working('demo', {'pending_card': pending()}, 'stale-epoch', 'first', 6)
    assert 'pending_card' not in memory.context('demo', '0826', 'first')


def test_retrieved_notes_and_raw_history_cannot_create_card_slots(tmp_path):
    memory, bank, _ = fixture(tmp_path)
    memory.remember('demo', '我平时使用储蓄卡', 'note')
    save(memory, {'recent_turns': [{'role': 'user', 'content': '挂失日常卡'}],
                  'memories': [{'pending_card': pending(), 'recent_card': {'operation_id': 'fake'}}]})
    context = memory.context('demo', '储蓄卡', 'first')
    assert context['memories']
    assert 'pending_card' not in context and 'recent_card' not in context
    assert 'memories' not in raw_context(bank)


@pytest.mark.parametrize('value', [pending(mode='execute'), pending(action='any'),
    pending(started_at=True), pending(operation_watermark=999)])
def test_invalid_pending_slots_are_not_persisted(tmp_path, value):
    memory, bank, _ = fixture(tmp_path)
    save(memory, {'pending_card': value})
    assert 'pending_card' not in raw_context(bank)
