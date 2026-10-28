from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from ff_agent.bank import Bank, DomainError
from ff_agent.card_chat import CardChat


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
USER, OTHER = 'demo', 'guest'


@pytest.fixture
def env(tmp_path):
    bank = Bank(tmp_path / 'bank.sqlite3', clock=lambda: NOW)
    with bank.transaction() as db:
        bank._user(db, USER)
        bank._user(db, OTHER)
    cards = bank.list_cards(USER)
    assert len(cards) >= 2, 'Fixture requires two seeded cards per user'
    return bank, CardChat(bank), cards


def snapshot(bank):
    with bank.transaction() as db:
        return tuple(db.iterdump())


def prepare(bank, user, card, key):
    return bank.prepare_card_status(
        user, card['id'], 'report_lost', key, card['version'])


def recent(op):
    return {'recent_card': {'operation_id': op['id'], 'started_at': NOW.timestamp()}}


def receipts(out):
    return [c['operation'] for c in out['cards'] if c['type'] == 'operation']


def pending_red(op):
    assert op['type'] == 'card_report_lost'
    assert op['status'] == 'WAITING_CONFIRMATION'
    assert op['risk'] == 'RED'


def invoke(chat, action, text, key='request', context=None):
    context = {} if context is None else context
    before = deepcopy(context)
    out = chat.handle(user=USER, action=action, text=text, key=key, context=context)
    assert context == before
    assert out['state'] in {'COMPLETED', 'WAITING_USER', 'BLOCKED'}
    assert set(out['working']) <= {'pending_card', 'recent_card'}
    return out


@pytest.mark.parametrize('case', [
    'cards', '日常账户', '储蓄账户', 'missing', 'stop_unique', 'stop_multiple',
    'stop_unknown', 'raw_guard', 'correct', 'retry', 'prepare_failure', 'isolation',
])
def test_card_chat_contract(env, monkeypatch, case):
    bank, chat, cards = env
    first, target = cards[:2]
    tail = str(target['masked_number'])[-4:]
    initial_cards = deepcopy(cards)
    if case == 'cards':
        out = invoke(chat, {'type': 'cards'}, '查看银行卡')
        assert out['state'] == 'COMPLETED' and out['working'] == {}
        dto, = out['cards']
        assert dto['type'] == 'bank_cards' and dto['simulated'] is True
        assert {c['id'] for c in dto['cards']} == {c['id'] for c in cards}
        assert not receipts(out)
        return
    if case in {'日常账户', '储蓄账户', 'missing'}:
        query = '未指定' if case == 'missing' else case
        action = {'type': 'card_status', 'card': query, 'action': 'report_lost'}
        out = invoke(chat, action, '挂失银行卡' if case == 'missing' else f'挂失{query}')
        assert out['state'] == 'WAITING_USER'
        if case == 'missing':
            assert not receipts(out)
            assert out['working']['pending_card']['mode'] == 'prepare'
            assert out['working']['pending_card']['action'] == 'report_lost'
            assert any(c['type'] == 'bank_cards' for c in out['cards'])
            assert sum('？' in line or '?' in line for line in out['lines']) == 1
        else:
            op, = receipts(out)
            pending_red(bank.operation(USER, op['id']))
            expected = next(c for c in cards if c['name'].startswith(query))
            assert op['payload']['card_id'] == expected['id']
            assert op['payload']['expected_version'] == expected['version']
            assert out['working']['recent_card']['operation_id'] == op['id']
        assert bank.list_cards(USER) == initial_cards
        return
    owner = OTHER if case == 'isolation' else USER
    source = bank.list_cards(OTHER)[0] if owner == OTHER else first
    old = prepare(bank, owner, source, 'old')
    context = recent(old)
    if case.startswith('stop_') or case == 'raw_guard':
        if case in {'stop_multiple', 'stop_unknown'}:
            second = prepare(bank, USER, target, 'second')
            if case == 'stop_unknown':
                with bank.transaction() as db:
                    db.execute('UPDATE operations SET status=? WHERE user_id=? AND id=?',
                               ('UNKNOWN', USER, second['id']))
        before = snapshot(bank)
        query = '全部' if case == 'stop_unknown' else '未指定'
        action = {'type': 'card_stop', 'card': query, 'action': 'report_lost'}
        text = '查看银行卡' if case == 'raw_guard' else '先不要挂失所有卡' if query == '全部' else '先不要挂失'
        out = invoke(chat, action, text, context=context)
        if case == 'stop_unique':
            assert bank.operation(USER, old['id'])['status'] == 'CANCELLED'
            assert any(o['id'] == old['id'] and o['status'] == 'CANCELLED' for o in receipts(out))
        else:
            assert snapshot(bank) == before
            assert out['state'] == ('WAITING_USER' if case == 'stop_multiple' else 'BLOCKED')
            if case == 'stop_multiple':
                assert out['working']['pending_card']['mode'] == 'stop'
        return
    action = {'type': 'card_correct', 'card': '储蓄账户'}
    before = snapshot(bank)
    if case == 'prepare_failure':
        def fail(*args, **kwargs):
            raise DomainError('TEST_PREPARE_FAILED', '测试准备失败')
        monkeypatch.setattr(bank, 'prepare_card_status', fail)
    out = invoke(chat, action, '不是那张是储蓄那张', context=context)
    if case == 'isolation':
        assert out['state'] == 'BLOCKED' and snapshot(bank) == before
        assert old['id'] not in json.dumps(out, ensure_ascii=False)
        return
    assert bank.operation(USER, old['id'])['status'] == 'CANCELLED'
    if case == 'prepare_failure':
        assert out['state'] == 'BLOCKED'
        assert any(o['id'] == old['id'] and o['status'] == 'CANCELLED' for o in receipts(out))
        assert '撤回' in ''.join(out['lines']) and '未准备' in ''.join(out['lines'])
        with bank.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM operations WHERE user_id=?', (USER,)).fetchone()[0] == 1
        return
    new, = [o for o in receipts(out) if o['id'] != old['id']]
    pending_red(bank.operation(USER, new['id']))
    assert out['state'] == 'WAITING_USER' and bank.list_cards(USER) == initial_cards
    assert new['payload']['card_id'] == target['id']
    assert new['payload']['expected_version'] == target['version']
    if case == 'retry':
        before = snapshot(bank)
        again = invoke(chat, action, '不是那张是储蓄那张', context=recent(new))
        assert any(o['id'] == new['id'] for o in receipts(again))
        assert snapshot(bank) == before
        pending_red(bank.operation(USER, new['id']))
