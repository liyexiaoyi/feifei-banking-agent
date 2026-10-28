from datetime import datetime

import pytest

from ff_agent.bank import Bank, TZ
from ff_agent.card_chat_controls import CardControls
from ff_agent.card_working import validate_card_slots
from ff_agent.task_plan import PlanStore


@pytest.fixture
def environment(tmp_path):
    bank = Bank(tmp_path/'pending-plan.db', clock=lambda: datetime(2026, 10, 27, 2, tzinfo=TZ))
    plans = PlanStore(bank.path, clock=lambda: bank.now().timestamp())
    plan, _ = plans.reserve('demo', 'missing', '挂失', '')
    action = {'type':'card_status','card':'未指定','action':'report_lost'}
    plan = plans.freeze('demo', plan['id'], plan['version'], [{'idx':0,'action':action,'deps':[]}])
    plan, token = plans.claim('demo', plan['id'], 0, plan['version'], '')
    ticket = {'id':plan['id'],'idx':0,'token':token}
    yield bank, plans, plan, ticket
    plans.close()


def value(bank, plan, watermark=0):
    return {'safety_epoch':'', 'pending_card':{'mode':'prepare','action':'report_lost',
            'started_at':bank.now().timestamp(),'operation_watermark':watermark,
            'source_plan_id':plan['id'],'source_plan_idx':0}}


def check(bank, stored, tuple_rows=False):
    with bank.transaction() as db:
        if tuple_rows:
            db.row_factory = None
        db.execute('PRAGMA query_only=ON')
        before = db.total_changes
        result = validate_card_slots(db, 'demo', stored, bank.now().timestamp())
        assert db.total_changes == before
        return result


@pytest.mark.parametrize('tuple_rows', [False, True])
def test_live_unfilled_plan_source_is_valid_without_creating_or_authorizing_an_operation(environment, tuple_rows):
    bank, plans, plan, ticket = environment
    stored = value(bank, plan)
    assert check(bank, stored, tuple_rows)['pending_card'] == stored['pending_card']
    assert bank.snapshot('demo')['operations'] == []


def test_plan_sourced_slot_requires_a_server_operation_watermark(environment):
    bank, _, plan, _ = environment
    stored = value(bank, plan)
    del stored['pending_card']['operation_watermark']
    assert check(bank, stored) == {}


@pytest.mark.parametrize('cancelled', [False, True])
def test_plan_node_with_an_existing_operation_cannot_supply_selection_again_even_after_cancel(environment, cancelled):
    bank, plans, plan, ticket = environment
    key = plan['nodes'][0]['effect_key']
    op = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', key, 1, ticket)
    plans.settle('demo', plan['id'], 0, ticket['token'], 'PREPARED', {'cards':[{'type':'operation','operation':op}]})
    with bank.transaction() as db:
        watermark = db.execute("SELECT MAX(rowid) FROM operations WHERE user_id='demo'").fetchone()[0]
    if cancelled:
        CardControls(bank).cancel('demo','stop','hash',{'type':'card_stop','card':'未指定','action':'report_lost'},[op['id']])
    # Server-side invariant probe: HTTP never supplies this combination today.
    assert check(bank, value(bank, plan, watermark)) == {}
