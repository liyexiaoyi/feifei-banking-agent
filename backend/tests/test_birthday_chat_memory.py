"""Birthday working memory is a session slot, never a retrieved-note authorization."""
from datetime import datetime, timedelta
import json

from ff_agent.bank import Bank
from ff_agent.memory import Memory
from ff_agent.birthday_chat_parse import canonical_intent, summary_hash


NOW = datetime.fromisoformat('2026-10-28T12:00:00+08:00')


def pending(amount=50000, order=1):
    p = dict(stage='SUMMARIZED', recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily',
             budget_cents=amount, asked='confirm', session_id='owned-session', epoch='', started_at=NOW.timestamp(),
             origin_order=1, summary_order=order, canonical_intent=None, summary_hash=None)
    p['canonical_intent'] = canonical_intent(p)
    p['summary_hash'] = summary_hash(p)
    return p


def test_working_birthday_survives_only_in_its_original_session_and_ttl(tmp_path):
    clock = [NOW]
    b = Bank(tmp_path / 'memory.db', clock=lambda: clock[0]); m = Memory(b); p = pending()
    m.save_working('demo', {'pending_birthday': p}, '', session_id='owned-session', request_order=1)
    assert m.context('demo', '生日', session_id='owned-session')['pending_birthday'] == p
    assert 'pending_birthday' not in m.context('demo', '生日', session_id='another-session')
    clock[0] += timedelta(seconds=300)
    m.save_working('demo', {'pending_birthday': p}, '', session_id='owned-session', request_order=2)
    clock[0] += timedelta(seconds=301)
    assert 'pending_birthday' not in m.context('demo', '生日', session_id='owned-session')


def test_malformed_birthday_slot_clears_instead_of_retaining_old_authorization(tmp_path):
    b = Bank(tmp_path / 'memory.db', clock=lambda: NOW); m = Memory(b); p = pending()
    m.save_working('demo', {'pending_birthday': p}, '', session_id='owned-session', request_order=1)
    p['budget_cents'] = True
    m.save_working('demo', {'pending_birthday': p}, '', session_id='owned-session', request_order=2)
    assert 'pending_birthday' not in m.context('demo', '生日', session_id='owned-session')


def test_old_request_order_cannot_overwrite_newer_explicit_birthday_summary(tmp_path):
    b = Bank(tmp_path / 'memory.db', clock=lambda: NOW); m = Memory(b)
    m.save_working('demo', {'pending_birthday': pending(30000, 3)}, '', session_id='owned-session', request_order=3)
    m.save_working('demo', {'pending_birthday': pending(50000, 1)}, '', session_id='owned-session', request_order=1)
    assert m.context('demo', '生日', session_id='owned-session')['pending_birthday']['budget_cents'] == 30000


def test_reference_replay_is_owned_minimal_select_only_and_missing_refs_are_inert(tmp_path):
    from ff_agent.birthday_store import BirthdayStore
    b = Bank(tmp_path / 'memory.db', clock=lambda: NOW); m = Memory(b)
    f = BirthdayStore(b).create('demo', '妈妈', '2026-10-29', 'a_daily', 50000, 'owned-birthday-reference')
    def value():
        return {'message': '请查看生日安排', 'cards': [{'type': 'birthday_flow', 'flow_id': f['id'], 'simulated': True, 'merchant_connected': False, 'budget_cents': 999999, 'approved': True}], 'tasks': []}
    with b.transaction() as db:
        before = db.total_changes
        own = m.refresh_cards(db, 'demo', value())
        foreign = m.refresh_cards(db, 'guest', value())
        assert db.total_changes == before
    assert own['cards'] == [{'type': 'birthday_flow', 'flow_id': f['id'], 'simulated': True, 'merchant_connected': False}]
    assert foreign['cards'] == [{'type': 'birthday_flow', 'flow_id': None, 'simulated': True, 'merchant_connected': False}]
