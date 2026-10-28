"""Working birthday slots must be bounded, owned and bound to this session/epoch."""
from copy import deepcopy
from datetime import datetime
import importlib
import importlib.util

import pytest
from ff_agent.bank import Bank


NOW = datetime.fromisoformat('2026-10-28T12:00:00+08:00')


def module():
    assert importlib.util.find_spec('ff_agent.birthday_chat_slot'), 'birthday slot validator missing'
    return importlib.import_module('ff_agent.birthday_chat_slot')


def pending():
    p = dict(stage='SUMMARIZED', recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily',
             budget_cents=50000, asked='confirm', session_id='session-owned', epoch='', started_at=NOW.timestamp(),
             origin_order=1, summary_order=4, summary_hash=None, canonical_intent=None)
    parse = importlib.import_module('ff_agent.birthday_chat_parse')
    p['canonical_intent'] = parse.canonical_intent(p)
    p['summary_hash'] = parse.summary_hash(p)
    return p


def validate(bank, p, user='demo', now=None, session='session-owned', epoch=''):
    m = module()
    with bank.transaction() as db:
        return m.validate_birthday_pending(db, user, {'pending_birthday': p}, NOW.timestamp() if now is None else now, epoch, session)


def test_owned_summary_is_revalidated_and_returned_as_a_copy(tmp_path):
    module()
    b = Bank(tmp_path / 'slot.db', clock=lambda: NOW)
    p = pending()
    assert validate(b, p) == {'pending_birthday': p}
    out = validate(b, p)
    out['pending_birthday']['budget_cents'] = 1
    assert p['budget_cents'] == 50000


@pytest.mark.parametrize('damage', ['bool', 'fraction', 'future_start', 'old_start', 'nan', 'foreign_account', 'hash', 'canonical', 'session', 'epoch', 'extra', 'order_bool', 'summary_order', 'invalid_date'])
def test_invalid_slot_is_discarded_without_becoming_authorization(tmp_path, damage):
    module()
    b = Bank(tmp_path / 'slot.db', clock=lambda: NOW)
    p = pending()
    changes = {'bool': ('budget_cents', True), 'fraction': ('budget_cents', 0.5),
               'future_start': ('started_at', NOW.timestamp() + 1), 'old_start': ('started_at', NOW.timestamp() - 601),
               'nan': ('started_at', float('nan')), 'foreign_account': ('account_id', 'g_daily'),
               'hash': ('summary_hash', 'wrong'), 'canonical': ('canonical_intent', '{}'),
               'session': ('session_id', 'other'), 'epoch': ('epoch', 'new-risk'), 'extra': ('approved', True),
               'order_bool': ('origin_order', True), 'summary_order': ('summary_order', 0),
               'invalid_date': ('event_date', '2026-02-30')}
    k, v = changes[damage]
    p[k] = v
    assert validate(b, p) == {}


def test_slot_cannot_cross_users_or_be_renewed_by_resaving(tmp_path):
    module()
    b = Bank(tmp_path / 'slot.db', clock=lambda: NOW)
    p = pending()
    assert validate(b, p, user='guest') == {}
    assert validate(b, deepcopy(p), now=NOW.timestamp() + 601) == {}


def test_derived_request_key_is_short_ascii_deterministic_and_content_sensitive():
    m = module()
    import re
    key = m.derive_chat_key('生日' * 50)
    assert re.fullmatch(r'chat-bday-[a-f0-9]{48}', key)
    assert key == m.derive_chat_key('生日' * 50) and key != m.derive_chat_key('生日' * 49 + '改口')

