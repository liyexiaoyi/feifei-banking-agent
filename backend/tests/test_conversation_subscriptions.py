"""A continuous bill/renewal goal must observe its own real consent and receipts."""
from datetime import datetime, timedelta
import importlib
import importlib.util

import pytest

from ff_agent.bank import Bank, DomainError
from ff_agent.conversation_goal import GoalStore

TEXT = '看看这个月花哪儿了，再帮我处理不需要的续费'


def make(tmp_path):
    assert importlib.util.find_spec('ff_agent.conversation_subscriptions'), 'continuous subscription goal not implemented'
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    bank = Bank(tmp_path/'renewal-goal.db', clock=lambda: clock[0])
    store = GoalStore(bank)
    loop = importlib.import_module('ff_agent.conversation_subscriptions').SubscriptionGoal(bank, store)
    return bank, store, loop, clock


def talk(e, text=TEXT, key='renewal-first'):
    z = e[2].handle('demo', 'room-a', '', text, key)
    assert isinstance(z, dict) and isinstance(z.get('message'), str)
    assert isinstance(z.get('cards'), list) and isinstance(z.get('goal'), dict)
    return z


def operations(e, kind=None):
    with e[0].transaction() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM operations WHERE user_id=?', ('demo',))]
    return [r for r in rows if kind is None or r['type'] == kind]


def operation(z, kind):
    return next(c['operation'] for c in z['cards'] if c.get('type') == 'operation' and c['operation']['type'] == kind)


def confirm(e, op):
    out = e[0].confirm('demo', op['id'])
    assert out['status'] == 'SUCCEEDED'
    return out


def advance(e, g=None, key='renewal-continue'):
    g = g or e[2].view('demo', 'room-a', '')
    return e[2].continue_goal('demo', 'room-a', '', g['id'], g['version'], key)


def queried(e):
    first = talk(e)
    confirm(e, operation(first, 'subscription_query'))
    return advance(e)


def states(e):
    with e[0].transaction() as db:
        return dict(db.execute('SELECT id,status FROM subscriptions WHERE user_id=?', ('demo',)))


def test_first_turn_shows_real_bill_and_only_prepares_subscription_query(tmp_path):
    e = make(tmp_path)
    z = talk(e)
    assert z['goal']['kind'] == 'spending_subscriptions'
    bills = [c['bill'] for c in z['cards'] if c.get('type') == 'bill']
    assert len(bills) == 1 and bills[0]['period'] == '2026-10'
    assert bills[0]['expense_cents'] == e[0].analyze('demo', '2026-10')['expense_cents']
    assert operation(z, 'subscription_query')['status'] == 'WAITING_CONFIRMATION'
    assert not z['goal']['choices'] and len(operations(e)) == 1
    assert set(states(e).values()) == {'ACTIVE'}


def test_preexisting_other_query_cannot_authorize_this_goal(tmp_path, monkeypatch):
    e = make(tmp_path)
    confirm(e, e[0].prepare_subscription_query('demo', 'other-query-before-goal'))
    calls = []
    original = e[0].subscriptions
    monkeypatch.setattr(e[0], 'subscriptions', lambda user: (calls.append(user), original(user))[1])
    z = talk(e)
    assert operation(z, 'subscription_query')['status'] == 'WAITING_CONFIRMATION'
    assert z['goal']['choices'] == [] and calls == []


def test_plain_confirmation_and_unpaid_continue_do_not_query_or_cancel(tmp_path, monkeypatch):
    e = make(tmp_path)
    z = talk(e)
    monkeypatch.setattr(e[0], 'subscriptions', lambda user: pytest.fail('read before own query consent'))
    assert talk(e, '好的确认', 'renewal-text-confirm')['goal']['id'] == z['goal']['id']
    advance(e)
    assert len(operations(e)) == 1 and set(states(e).values()) == {'ACTIVE'}


def test_real_consent_then_one_explicit_merchant_then_separate_cancellation(tmp_path):
    e = make(tmp_path)
    z = queried(e)
    assert z['goal']['phase'] == 'CHOOSING'
    expected = e[0].subscriptions('demo')
    assert {c['id'] for c in z['goal']['choices']} == {s['id'] for s in expected if s['status'] == 'ACTIVE'}
    picked = next(c for c in z['goal']['choices'] if c['id'] == 'sub_video')
    chosen = talk(e, picked['label'], 'renewal-merchant-choice')
    op = operation(chosen, 'subscription_cancel')
    assert op['payload']['subscription_id'] == 'sub_video' and op['status'] == 'WAITING_CONFIRMATION'
    assert states(e)['sub_video'] == 'ACTIVE'
    before = e[0].snapshot('demo')['accounts']
    confirm(e, op)
    done = advance(e, key='renewal-after-cancel')
    assert done['goal']['status'] == 'COMPLETED' and states(e)['sub_video'] == 'CANCELLED'
    assert e[0].snapshot('demo')['accounts'] == before
    assert all(states(e)[s] == 'ACTIVE' for s in states(e) if s != 'sub_video')
    assert '退款成功' not in done['message'] and '退回' not in done['message']


@pytest.mark.parametrize('text', ['不要第一个', '第九个', '都不用了吧？', '留500', '这个能退款吗'])
def test_ambiguous_negative_or_refund_message_never_selects_a_merchant(tmp_path, text):
    e = make(tmp_path)
    z = queried(e)
    e[2].handle('demo', 'room-a', '', text, 'ambiguous-message')
    assert not operations(e, 'subscription_cancel')
    assert e[2].view('demo', 'room-a', '')['id'] == z['goal']['id']


def test_expired_own_consent_requires_new_query_even_if_another_query_is_fresh(tmp_path):
    e = make(tmp_path)
    queried(e)
    e[3][0] += timedelta(minutes=31)
    confirm(e, e[0].prepare_subscription_query('demo', 'other-query-after-expiry'))
    z = talk(e, '第一个', 'renewal-expired-choice')
    assert not operations(e, 'subscription_cancel') and z['goal']['choices'] == []
    assert operation(z, 'subscription_query')['status'] == 'WAITING_CONFIRMATION'


def test_merchant_external_action_is_not_claimed_cancelled(tmp_path):
    e = make(tmp_path)
    z = queried(e)
    merchant = next(c for c in z['goal']['choices'] if c['id'] == 'sub_gym')
    out = talk(e, merchant['label'], 'renewal-external-merchant')
    assert states(e)['sub_gym'] == 'ACTIVE' and not operations(e, 'subscription_cancel')
    assert out['goal']['status'] != 'COMPLETED' and '商户' in out['message']


def test_pending_repeat_and_pure_get_never_duplicate_operations_or_audit(tmp_path):
    e = make(tmp_path)
    z = talk(e)
    with e[0].transaction() as db:
        before = list(db.iterdump())
    for _ in range(3):
        assert e[2].view('demo', 'room-a', '', z['goal']['id'])['id'] == z['goal']['id']
    with e[0].transaction() as db:
        assert list(db.iterdump()) == before
    for i in range(3):
        advance(e, key='repeat-continue-'+str(i))
    assert len(operations(e)) == 1


def test_unknown_query_only_observes_and_does_not_redispatch(tmp_path):
    e = make(tmp_path)
    z = talk(e)
    op = operation(z, 'subscription_query')
    with e[0].transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    for i in range(2):
        out = advance(e, key='unknown-query-'+str(i))
        assert out['goal']['status'] == 'UNKNOWN' and out['goal']['choices'] == []
    assert len(operations(e)) == 1 and set(states(e).values()) == {'ACTIVE'}


def test_unknown_cancel_cannot_become_success_or_repeated(tmp_path):
    e = make(tmp_path)
    queried(e)
    out = talk(e, '第一个', 'renewal-pick-first')
    op = operation(out, 'subscription_cancel')
    with e[0].transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    assert advance(e)['goal']['status'] == 'UNKNOWN'
    e[2].handle('demo', 'room-a', '', '再取消一次', 'unknown-cancel-text')
    assert len(operations(e, 'subscription_cancel')) == 1


def test_foreign_owner_or_session_cannot_observe_or_continue(tmp_path):
    e = make(tmp_path)
    g = talk(e)['goal']
    for owner, session in [('guest', 'room-a'), ('demo', 'room-b')]:
        assert e[2].view(owner, session, '', g['id']) is None
        with pytest.raises(DomainError):
            e[2].continue_goal(owner, session, '', g['id'], g['version'], 'foreign-renewal')


def test_stale_version_rejected_and_balance_interruption_keeps_goal(tmp_path):
    e = make(tmp_path)
    g = talk(e)['goal']
    assert e[2].handle('demo', 'room-a', '', '查询余额', 'renewal-balance') is None
    assert e[2].view('demo', 'room-a', '')['id'] == g['id']
    with pytest.raises(DomainError):
        e[2].continue_goal('demo', 'room-a', '', g['id'], g['version']-1, 'renewal-stale')


def test_single_business_bill_query_is_not_hijacked_into_a_continuous_goal(tmp_path):
    e = make(tmp_path)
    assert e[2].handle('demo', 'room-a', '', '分析本月账单', 'single-bill') is None
    assert e[1].current('demo', 'room-a', '') is None


def test_zero_cost_owned_subscription_is_still_a_real_selectable_relationship(tmp_path):
    e = make(tmp_path)
    with e[0].transaction() as db:
        db.execute("UPDATE subscriptions SET amount_cents=0 WHERE id='sub_video'")
    z = queried(e)
    assert next(c for c in z['goal']['choices'] if c['id'] == 'sub_video')['price_cents'] == 0
    picked = talk(e, '第一个', 'renewal-free-choice')
    assert operation(picked, 'subscription_cancel')['payload']['subscription_id'] == 'sub_video'


def test_existing_tool_guard_callback_contract_is_used_instead_of_dynamic_method_dispatch(tmp_path):
    e = make(tmp_path)
    calls = []
    def guard(user, group, callback):
        calls.append((user, group))
        return callback()
    e = (e[0], e[1], type(e[2])(e[0], e[1], run_tool=guard), e[3])
    z = talk(e)
    assert operation(z, 'subscription_query')['status'] == 'WAITING_CONFIRMATION'
    assert calls == [('demo', 'bill'), ('demo', 'subscriptions')]


def test_completed_goal_does_not_override_a_later_unknown_cancellation_receipt(tmp_path):
    e = make(tmp_path)
    queried(e)
    op = operation(talk(e, '第一个', 'pick-before-loss'), 'subscription_cancel')
    confirm(e, op)
    g = advance(e)['goal']
    assert g['status'] == 'COMPLETED'
    with e[0].transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    g = e[2].view('demo', 'room-a', '', g['id'])
    assert g['status'] == 'UNKNOWN' and g['next_action'] == 'CHECK'


def test_unknown_query_observation_overrides_completion_even_when_cancel_receipt_succeeded(tmp_path):
    e = make(tmp_path);queried(e)
    op=operation(talk(e,'第一个','pick-before-query-loss'),'subscription_cancel')
    confirm(e,op);g=advance(e)['goal'];assert g['status']=='COMPLETED'
    with e[0].transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE type='subscription_query'")
    view=e[2].view('demo','room-a','',g['id'])
    assert view['status']=='UNKNOWN' and view['next_action']=='CHECK'


def test_nonconsent_domain_failure_does_not_fabricate_expiry_or_prepare_another_query(tmp_path,monkeypatch):
    e=make(tmp_path);g=queried(e)['goal']
    def unavailable(user):raise DomainError('BROKEN_SUBSCRIPTION_DATA','订阅记录暂时无法核对。',503)
    monkeypatch.setattr(e[0],'subscriptions',unavailable)
    view=e[2].view('demo','room-a','',g['id'])
    assert view['status']=='UNKNOWN' and view['next_action']=='CHECK'
    advance(e,view,'no-query-on-read-failure')
    assert len(operations(e,'subscription_query'))==1


def test_receipt_transport_failure_projects_unknown_without_an_executable_cached_card(tmp_path, monkeypatch):
    e = make(tmp_path)
    g = talk(e)['goal']
    monkeypatch.setattr(e[0], 'receipt', lambda *args: (_ for _ in ()).throw(RuntimeError('read unavailable')))
    out = e[2].view('demo', 'room-a', '', g['id'])
    assert out['status'] == 'UNKNOWN' and out['choices'] == []


@pytest.mark.parametrize('prefix,suffix', [('帮我取消', ''), ('', '的自动续费停掉')])
def test_explicit_natural_merchant_cancellation_is_a_selection_not_approval(tmp_path, prefix, suffix):
    e = make(tmp_path)
    z = queried(e)
    merchant = next(c['label'] for c in z['goal']['choices'] if c['id'] == 'sub_video')
    z = talk(e, prefix+merchant+suffix, 'natural-cancel')
    assert operation(z, 'subscription_cancel')['status'] == 'WAITING_CONFIRMATION'
    assert states(e)['sub_video'] == 'ACTIVE'


def test_named_merchant_negation_and_question_do_not_prepare_cancellation(tmp_path):
    e = make(tmp_path)
    z = queried(e)
    merchant = next(c['label'] for c in z['goal']['choices'] if c['id'] == 'sub_video')
    for i,text in enumerate(['不要取消'+merchant,'取消'+merchant+'能退款吗？',merchant+'和晨星音乐都取消']):
        talk(e,text,'natural-no-'+str(i))
    assert not operations(e, 'subscription_cancel')


@pytest.mark.parametrize('text', ['账单里的自动续费是什么意思？', '账单和订阅是什么区别？'])
def test_educational_questions_do_not_start_a_goal_or_prepare_permission(tmp_path, text):
    e = make(tmp_path)
    assert e[2].handle('demo', 'room-a', '', text, 'renewal-faq') is None
    assert e[1].current('demo', 'room-a', '') is None and not operations(e)


def test_colloquial_balance_interruption_does_not_get_consumed_as_a_merchant_answer(tmp_path):
    e = make(tmp_path)
    g = queried(e)['goal']
    assert e[2].handle('demo', 'room-a', '', '我还有多少钱', 'renewal-colloquial-balance') is None
    assert e[2].view('demo', 'room-a', '')['id'] == g['id']


def test_tool_budget_is_per_conversation_call_not_shared_between_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    e=make(tmp_path);barrier=Barrier(3)
    def guard(user,group,callback):
        if group=='bill':barrier.wait(timeout=5)
        return callback()
    loop=type(e[2])(e[0],e[1],run_tool=guard)
    def start(i):return loop.handle('demo','parallel-room-'+str(i),'',TEXT,'parallel-renewal-'+str(i))
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes=list(pool.map(start,range(3)))
    assert all(z['goal']['status']=='ACTIVE' for z in outcomes),outcomes
    assert len({z['goal']['id'] for z in outcomes})==3
    assert len(operations(e,'subscription_query'))==3
