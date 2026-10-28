"""B5 controls: changes require Bank confirmation and bound verification."""
import importlib
import importlib.util
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from ff_agent.bank import Bank, DomainError


@pytest.fixture
def bank(tmp_path):
    return Bank(tmp_path/'controls.db',clock=lambda:datetime(2026,10,27,12,tzinfo=ZoneInfo('Asia/Shanghai')))


def service(bank):
    assert importlib.util.find_spec('ff_agent.card_controls') is not None, 'B5 card controls not implemented'
    return importlib.import_module('ff_agent.card_controls').CardControls(bank)


def prepare(bank, action='lock', value=True, key='control-1', version=None, card='card_a_daily'):
    s=service(bank)
    v=s.get_controls('demo',card)['control_version'] if version is None else version
    return s.prepare_control('demo',card,action,value,v,key)


def finish(bank, op):
    confirmed=bank.confirm('demo',op['id'])
    assert confirmed['risk']=='RED' and confirmed['status']=='WAITING_VERIFICATION'
    return bank.verify('demo',op['id'],'246810')


def finances(bank):
    with bank.transaction() as db:
        return ([tuple(x) for x in db.execute('SELECT * FROM accounts ORDER BY id')],
                [tuple(x) for x in db.execute('SELECT * FROM transactions ORDER BY id')])


def test_get_controls_owned_and_separate_card_schema_unchanged(bank):
    s=service(bank); cards=bank.list_cards('demo')
    assert set(cards[0])=={'id','account_id','name','masked_number','status','version','simulated'}
    controls=s.get_controls('demo','card_a_daily')
    assert controls['locked'] is False and controls['single_limit_cents']==2000000
    assert controls['daily_limit_cents']==5000000 and controls['control_version']==1
    assert controls['spent_today_cents']==0 and controls['simulated'] is True
    assert {x['card_id'] for x in s.list_controls('guest')}=={'card_b_daily','card_b_saving'}
    with pytest.raises(DomainError) as e:s.get_controls('guest','card_a_daily')
    assert e.value.status==404


def test_control_change_only_after_confirm_and_verify_and_no_funds_change(bank):
    s=service(bank); before=finances(bank); old=bank.list_cards('demo')
    op=prepare(bank)
    assert op['type']=='card_controls_update' and op['risk']=='RED'
    assert s.get_controls('demo','card_a_daily')['locked'] is False
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    bank.confirm('demo',op['id'])
    assert s.get_controls('demo','card_a_daily')['locked'] is False
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'000000')
    assert bank.verify('demo',op['id'],'246810')['status']=='SUCCEEDED'
    view=s.get_controls('demo','card_a_daily')
    assert view['locked'] is True and view['control_version']==2
    assert bank.list_cards('demo')==old and finances(bank)==before
    assert sum(x['event']=='CARD_CONTROLS_CHANGED' for x in bank.audit('demo'))==1
    assert bank.confirm('demo',op['id'])['status']=='SUCCEEDED'
    assert bank.verify('demo',op['id'],'246810')['status']=='SUCCEEDED'
    assert sum(x['event']=='CARD_CONTROLS_CHANGED' for x in bank.audit('demo'))==1


@pytest.mark.parametrize('action,value',[('set_single_limit',1),('set_single_limit',100000),('set_daily_limit',2000000)])
def test_even_small_limit_adjustment_is_fixed_red(bank,action,value):
    op=prepare(bank,action,value)
    assert op['risk']=='RED'
    assert finish(bank,op)['status']=='SUCCEEDED'


@pytest.mark.parametrize('action,value',[('lock',1),('unlock',0),('lock',False),('set_single_limit',True),('set_daily_limit',1.0),('set_single_limit',0),('set_daily_limit',5000001),('set_daily_limit',1),('missing',1)])
def test_invalid_changes_cannot_prepare_or_change_controls(bank,action,value):
    s=service(bank);before=s.get_controls('demo','card_a_daily')
    with pytest.raises(DomainError):prepare(bank,action,value)
    assert s.get_controls('demo','card_a_daily')==before
    with bank.transaction() as db:assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_foreign_card_prepare_hidden(bank):
    with pytest.raises(DomainError) as e:service(bank).prepare_control('guest','card_a_daily','lock',True,1,'foreign')
    assert e.value.status==404


def test_stale_controls_cas_rejects_second_prepared_change(bank):
    s=service(bank);a=prepare(bank);b=prepare(bank,'set_single_limit',100000,'other')
    finish(bank,a)
    with pytest.raises(DomainError):finish(bank,b)
    assert s.get_controls('demo','card_a_daily')['control_version']==2
    assert s.get_controls('demo','card_a_daily')['single_limit_cents']==2000000


def test_prepare_replay_keeps_original_and_changed_key_payload_conflicts(bank):
    s=service(bank);a=prepare(bank);finish(bank,a)
    replay=prepare(bank,version=1)
    assert replay['id']==a['id'] and replay['status']=='SUCCEEDED'
    with pytest.raises(DomainError) as e:prepare(bank,'set_single_limit',100000,version=1)
    assert e.value.code=='IDEMPOTENCY_CONFLICT'
    assert s.get_controls('demo','card_a_daily')['control_version']==2


def test_unknown_control_blocks_other_controls_and_status_without_releasing(bank):
    s=service(bank);a=prepare(bank)
    with bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(a['id'],))
    assert prepare(bank,version=1)['status']=='UNKNOWN'
    with pytest.raises(DomainError):prepare(bank,'set_single_limit',100000,'other')
    with pytest.raises(DomainError):bank.prepare_card_status('demo','card_a_daily','report_lost','lost',1)
    assert bank.receipt('demo',a['id'])['recorded_status']=='UNKNOWN'
    assert s.get_controls('demo','card_a_daily')['locked'] is False


def test_lost_and_transaction_lock_are_independent_and_survive_restart(bank):
    s=service(bank);finish(bank,prepare(bank))
    lost=bank.prepare_card_status('demo','card_a_daily','report_lost','lost',1);finish(bank,lost)
    finish(bank,prepare(bank,'unlock',False,'unlock'))
    assert s.get_controls('demo','card_a_daily')['locked'] is False
    card=next(x for x in bank.list_cards('demo') if x['id']=='card_a_daily')
    assert card['status']=='LOST' and card['version']==2
    again=Bank(bank.path,clock=bank.clock)
    assert service(again).get_controls('demo','card_a_daily')==s.get_controls('demo','card_a_daily')


def test_new_virtual_card_has_default_controls_in_same_transaction(bank):
    s=service(bank)
    op=bank.prepare_virtual_card('demo','a_daily','newvirtual')
    result=bank.confirm('demo',op['id'])
    card=result['result']['card_id'];assert s.get_controls('demo',card)['control_version']==1
    assert finish(bank,prepare(bank,card=card))['status']=='SUCCEEDED'
    assert s.get_controls('demo',card)['locked'] is True
    assert s.get_controls('demo','card_a_daily')['locked'] is False


def test_control_payload_tamper_rejected_and_expiry_does_not_change_state(bank):
    s=service(bank);op=prepare(bank)
    with bank.transaction() as db:
        payload=op['payload'];payload['after']['locked']=False
        db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,sort_keys=True),op['id']))
    with pytest.raises(DomainError):finish(bank,op)
    assert s.get_controls('demo','card_a_daily')['locked'] is False


@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unresolved_card_operation_cannot_be_exempted_by_its_own_id(bank,state):
    service(bank);op=prepare(bank)
    module=importlib.import_module('ff_agent.card_controls')
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?',(state,op['id']))
        with pytest.raises(DomainError):module.assert_card_reconciled(db,'demo','card_a_daily',op['id'])


def test_default_control_row_requires_an_existing_owned_card(bank):
    service(bank);module=importlib.import_module('ff_agent.card_controls')
    with bank.transaction() as db:
        before=db.execute('SELECT count(*) FROM card_controls').fetchone()[0]
        with pytest.raises(DomainError):module.ensure_controls_row(db,'demo','nonexistent-card','physical')
        assert db.execute('SELECT count(*) FROM card_controls').fetchone()[0]==before


def test_default_control_row_requires_callers_transaction(bank):
    from ff_agent.db import connect
    service(bank);module=importlib.import_module('ff_agent.card_controls');db=connect(bank.path)
    try:
        assert not db.in_transaction
        with pytest.raises(DomainError):module.ensure_controls_row(db,'demo','card_a_daily','physical')
    finally:db.close()
