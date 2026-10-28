"""Birthday chat turn state: bounded, pure, never authorizes money.

No DB, HTTP, notes, history or training. Field extraction lives in the peer
birthday_chat_fields module; this module only manages draft state/summary.
"""
import hashlib
import json
from dataclasses import dataclass

from ff_agent.birthday_chat_fields import (  # noqa: F401  (re-exported)
    AccountOption,
    extract_birthday_fields,
    is_birthday_intent,
    is_other_business,
)

__all__ = ('AccountOption', 'BirthdayTurn', 'canonical_intent', 'summary_hash',
           'parse_birthday_turn', 'extract_birthday_fields',
           'is_birthday_intent', 'is_other_business')

FIELD_ORDER = ('recipient_label', 'event_date', 'budget_cents', 'account_id')
CONFIRM_WORDS = frozenset(('启动', '确认启动', '确认启动生日安排', '开始安排'))
ABANDON_WORDS = ('记住', '算了', '不安排', '不用安排', '取消安排', '不要安排')
SUMMARY_RESET = ('summary_hash', 'canonical_intent', 'summary_order')


@dataclass(frozen=True)
class BirthdayTurn:
    kind: str
    pending: object
    message: str
    reply_source: str = 'birthday_rules'


def canonical_intent(p):
    """Sorted JSON of only the four intent fields."""
    body = {k: p.get(k) for k in ('recipient_label', 'event_date',
                                  'account_id', 'budget_cents')}
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'))


def summary_hash(p):
    return hashlib.sha256(canonical_intent(p).encode('utf-8')).hexdigest()


def _new_pending(now):
    return {'stage': 'COLLECTING', 'asked': 'recipient_label',
            'recipient_label': None, 'event_date': None,
            'budget_cents': None, 'account_id': None,
            'session_id': None, 'epoch': None,
            'started_at': now.timestamp(), 'origin_order': None,
            'summary_order': None, 'summary_hash': None,
            'canonical_intent': None}


def _complete(p):
    return all(p.get(k) is not None for k in FIELD_ORDER)


def _yuan(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def _account(p, owned):
    for a in owned:
        if a.id == p.get('account_id'):
            return f'{a.name}（{a.masked_number}）'
    return '已选账户'


def _question(field, owned):
    if field == 'recipient_label':
        return '请问要给谁安排生日？'
    if field == 'event_date':
        return '请告诉我生日的具体日期，例如2026-10-29或“明天”，是哪一天？'
    if field == 'budget_cents':
        return '请问预算是多少元（请给出准确金额）？'
    opts = '、'.join(f'{a.name}（{a.masked_number}）' for a in owned)
    return f'可选账户：{opts}。请问使用哪个账户？'


def _summarize(p, owned):
    p['stage'], p['asked'] = 'SUMMARIZED', 'confirm'
    p['canonical_intent'] = canonical_intent(p)
    p['summary_hash'] = summary_hash(p)
    msg = (f"已整理生日安排：对象{p['recipient_label']}，日期{p['event_date']}，"
           f"预算{_yuan(p['budget_cents'])}元，账户{_account(p, owned)}。"
           '仅建立安排，不会自动付款。确认无误请回复“启动”。')
    return BirthdayTurn('SUMMARY', p, msg)


def parse_birthday_turn(text, pending, owned, now):
    t = (text or '').strip()
    if pending:
        if (t in CONFIRM_WORDS and pending.get('stage') == 'SUMMARIZED'
                and _complete(pending) and pending.get('summary_hash')):
            return BirthdayTurn('CONFIRM', dict(pending), '好的，正在建立生日安排。')
        if is_other_business(t) or any(w in t for w in ABANDON_WORDS):
            return BirthdayTurn('ABANDON', None, '已退出生日安排草稿。')
        p = dict(pending)
    else:
        if not is_birthday_intent(t):
            return BirthdayTurn('NONE', None, '')
        p = _new_pending(now)
    patch = extract_birthday_fields(t, pending, owned, now) or {}
    patch = {k: v for k, v in patch.items() if k in FIELD_ORDER}
    if patch:
        p.update(patch)
        for k in SUMMARY_RESET:
            p[k] = None
        p['stage'] = 'COLLECTING'
    missing = [k for k in FIELD_ORDER if p.get(k) is None]
    if missing:
        p['stage'], p['asked'] = 'COLLECTING', missing[0]
        for k in SUMMARY_RESET:
            p[k] = None
        return BirthdayTurn('CLARIFY', p, _question(missing[0], owned))
    return _summarize(p, owned)
