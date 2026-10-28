from datetime import datetime

import pytest

from ff_agent.bank import Bank, DomainError, TZ
from ff_agent.card_chat_auth import check_card_ticket
from ff_agent.task_plan import PlanStore


ACTION = {'type': 'card_status', 'card': '日常账户', 'action': 'report_lost'}


@pytest.fixture
def environment(tmp_path):
    bank = Bank(tmp_path/'card-auth.db', clock=lambda: datetime(2026, 10, 27, 1, tzinfo=TZ))
    plans = PlanStore(bank.path, clock=lambda: bank.now().timestamp())
    yield bank, plans
    plans.close()


def ticket_for(plans, action=ACTION):
    plan, _ = plans.reserve('demo', 'auth-plan', 'test', '')
    plan = plans.freeze('demo', plan['id'], plan['version'], [{'idx': 0, 'action': action, 'deps': []}])
    plan, token = plans.claim('demo', plan['id'], 0, plan['version'], '')
    return plan, {'id': plan['id'], 'idx': 0, 'token': token}, plan['nodes'][0]['effect_key']


def test_matching_card_ticket_is_read_only_and_none_does_not_query_plan_tables(environment):
    bank, plans = environment
    _, ticket, key = ticket_for(plans)
    with bank.transaction() as db:
        before = db.total_changes
        assert check_card_ticket(bank, db, 'demo', ticket, ACTION, key) is None
        assert check_card_ticket(bank, db, 'demo', None, ACTION, 'internal') is None
        assert db.total_changes == before


@pytest.mark.parametrize('node_action', [
    {'type': 'transfer', 'payee': '妈妈', 'amount_cents': 100},
    {'type': 'cancel_subscription', 'merchant': '视频'},
    {'type': 'card_status', 'card': '储蓄账户', 'action': 'report_lost'},
    {'type': 'card_status', 'card': '日常账户', 'action': 'restore'},
])
def test_a_valid_ticket_for_different_action_cannot_authorize_card_work(environment, node_action):
    bank, plans = environment
    _, ticket, key = ticket_for(plans, node_action)
    with bank.transaction() as db, pytest.raises(DomainError):
        check_card_ticket(bank, db, 'demo', ticket, ACTION, key)
    assert bank.snapshot('demo')['operations'] == []


@pytest.mark.parametrize('change', ['user', 'key', 'token', 'stopped', 'settled'])
def test_ownership_effect_key_and_current_plan_node_state_are_rechecked(environment, change):
    bank, plans = environment
    plan, ticket, key = ticket_for(plans)
    user = 'guest' if change == 'user' else 'demo'
    if change == 'key':
        key += '-different'
    if change == 'token':
        ticket = {**ticket, 'token': 'wrong'}
    if change == 'stopped':
        plans.control('demo', plan['id'], plan['version'], 'pause', '')
    if change == 'settled':
        plans.settle('demo', plan['id'], 0, ticket['token'], 'DONE', {})
    with bank.transaction() as db, pytest.raises(DomainError):
        check_card_ticket(bank, db, user, ticket, ACTION, key)
    assert bank.snapshot('demo')['operations'] == []
