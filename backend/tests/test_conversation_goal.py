"""Goal metadata/journal cannot authorize bank effects or replay unknown steps."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib
import importlib.util
import json
import pytest
from ff_agent.bank import Bank, DomainError


def make(tmp_path):
    assert importlib.util.find_spec('ff_agent.conversation_goal'), 'conversation goal store not implemented'
    bank = Bank(tmp_path / 'goals.db', clock=lambda: datetime.fromisoformat('2026-10-28T12:00:00+08:00'))
    store = importlib.import_module('ff_agent.conversation_goal').GoalStore(bank)
    return bank, store


def new(store, key='goal-original-request', **slots):
    return store.create('demo', 'room-a', '', 'birthday', {'recipient_label': '妈妈', 'budget_cents': 50000, **slots}, key)


def bank_rows(bank):
    with bank.transaction() as db:
        return {name: [tuple(x) for x in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')] for name in ('accounts', 'operations', 'transactions', 'birthday_budgets')}


def test_goal_survives_reopen_and_has_no_financial_side_effect(tmp_path):
    bank, s = make(tmp_path); before = bank_rows(bank); g = new(s)
    assert g['kind'] == 'birthday' and g['status'] == 'ACTIVE' and g['version'] == 1
    assert g['slots']['budget_cents'] == 50000 and g['steps'] == []
    assert s.current('demo', 'room-a', '')['id'] == g['id']
    reopened = type(s)(Bank(bank.path, clock=bank.clock))
    assert reopened.get('demo', 'room-a', '', g['id']) == g
    assert bank_rows(bank) == before


def test_creation_replay_checks_intent_and_does_not_switch_current_goal(tmp_path):
    _, s = make(tmp_path); g = new(s)
    assert new(s) == g
    with pytest.raises(DomainError) as e: new(s, budget_cents=60000)
    assert e.value.code == 'IDEMPOTENCY_CONFLICT'
    with pytest.raises(DomainError): new(s, key='other-distinct-goal-request')
    assert s.current('demo', 'room-a', '')['id'] == g['id']


@pytest.mark.parametrize('user,session', [('guest', 'room-a'), ('demo', 'room-b')])
def test_owner_and_session_cannot_adopt_someone_elses_goal(tmp_path, user, session):
    _, s = make(tmp_path); g = new(s)
    assert s.current(user, session, '') is None
    with pytest.raises(DomainError) as e: s.get(user, session, '', g['id'])
    assert e.value.status == 404
    with pytest.raises(DomainError): s.update(user, session, '', g['id'], g['version'], {'phase': 'CHOOSING'})


def test_epoch_mismatch_is_read_only_pause_and_cannot_write(tmp_path):
    bank, s = make(tmp_path); g = new(s)
    with bank.transaction() as db: before = list(db.iterdump())
    stale = s.get('demo', 'room-a', 'different-risk-epoch', g['id'])
    assert stale['status'] == 'PAUSED' and stale['reason'] == 'RISK_CHANGED'
    with bank.transaction() as db: assert list(db.iterdump()) == before
    with pytest.raises(DomainError): s.update('demo', 'room-a', 'different-risk-epoch', g['id'], g['version'], {'phase': 'CHOOSING'})


def test_existing_claim_can_record_observation_after_risk_change_but_cannot_dispatch_again(tmp_path,monkeypatch):
    bank,s=make(tmp_path);g=new(s)
    started,token=s.start_step('demo','room-a','',g['id'],g['version'],'subscription_query',{})
    monkeypatch.setattr(bank,'_safety_epoch',lambda db,user:'risk-after-dispatch')
    settled=s.finish_step('demo','room-a','',g['id'],token,'OBSERVED',{'operation_ids':['owned-query-operation']})
    assert settled['status']=='PAUSED'
    assert settled['steps'][0]['state']=='OBSERVED'
    assert settled['steps'][0]['result']['operation_ids']==['owned-query-operation']
    with pytest.raises(DomainError):
        s.start_step('demo','room-a','risk-after-dispatch',g['id'],settled['version'],'subscription_query',{})


def test_update_is_versioned_and_metadata_does_not_prove_payment(tmp_path):
    bank, s = make(tmp_path); g = new(s); before = bank_rows(bank)
    n = s.update('demo', 'room-a', '', g['id'], g['version'], {'phase': 'CHOOSING', 'question': '选哪束花？', 'observations': {'item_ids': ['flowers_simple']}})
    assert n['version'] == g['version'] + 1 and n['status'] == 'ACTIVE'
    with pytest.raises(DomainError): s.update('demo', 'room-a', '', g['id'], g['version'], {'phase': 'OLD'})
    assert bank_rows(bank) == before


@pytest.mark.parametrize('slot', [{'budget_cents': True}, {'password': 'secret'}, {'nested': {'verification_code': '246810'}}, {'csrf': 'private'}, {'api_key': 'private'}])
def test_private_secrets_and_noninteger_money_are_not_saved(tmp_path, slot):
    _, s = make(tmp_path)
    with pytest.raises(DomainError): new(s, **slot)
    assert s.current('demo', 'room-a', '') is None


def test_one_step_has_durable_intent_and_cannot_be_dispatched_twice(tmp_path):
    bank, s = make(tmp_path); g = new(s)
    working, token = s.start_step('demo', 'room-a', '', g['id'], g['version'], 'birthday_create', {'request_label': '妈妈'})
    step = working['steps'][-1]
    assert step['state'] == 'STARTED' and 8 <= len(step['effect_key']) <= 64
    assert token and token not in json.dumps(s.get('demo', 'room-a', '', g['id']), ensure_ascii=False)
    reopened = type(s)(Bank(bank.path, clock=bank.clock))
    assert reopened.get('demo', 'room-a', '', g['id'])['steps'][-1]['effect_key'] == step['effect_key']
    with pytest.raises(DomainError): reopened.start_step('demo', 'room-a', '', g['id'], working['version'], 'birthday_create', {'request_label': '妈妈'})
    done = s.finish_step('demo', 'room-a', '', g['id'], token, 'OBSERVED', {'flow_id': 'flow-owned'})
    assert done['steps'][-1]['state'] == 'OBSERVED'
    assert s.finish_step('demo', 'room-a', '', g['id'], token, 'OBSERVED', {'flow_id': 'different'}) == done


def test_unknown_is_not_made_successful_or_retried_by_metadata_patch(tmp_path):
    bank, s = make(tmp_path); g = new(s); before = bank_rows(bank)
    working, token = s.start_step('demo', 'room-a', '', g['id'], g['version'], 'birthday_advance', {'flow_id': 'owned', 'expected_version': 2})
    u = s.finish_step('demo', 'room-a', '', g['id'], token, 'UNKNOWN', {})
    assert u['status'] == 'UNKNOWN'
    with pytest.raises(DomainError): s.start_step('demo', 'room-a', '', g['id'], u['version'], 'birthday_advance', {'flow_id': 'owned', 'expected_version': 2})
    with pytest.raises(DomainError): s.update('demo', 'room-a', '', g['id'], u['version'], {'status': 'ACTIVE'})
    assert bank_rows(bank) == before


@pytest.mark.parametrize('action', ['confirm', 'verify', 'cancel', '_execute', 'unknown_tool'])
def test_goal_journal_cannot_claim_financial_authority(tmp_path, action):
    _, s = make(tmp_path); g = new(s)
    with pytest.raises(DomainError): s.start_step('demo', 'room-a', '', g['id'], g['version'], action, {})
    assert s.get('demo', 'room-a', '', g['id'])['steps'] == []


def test_concurrent_step_claim_has_one_winner(tmp_path):
    _, s = make(tmp_path); g = new(s)
    def claim(_):
        try: return s.start_step('demo', 'room-a', '', g['id'], g['version'], 'birthday_create', {})
        except DomainError: return None
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(claim, range(4)))
    assert sum(x is not None for x in results) == 1
    assert len(s.get('demo', 'room-a', '', g['id'])['steps']) == 1
