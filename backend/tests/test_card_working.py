from copy import deepcopy
from datetime import datetime,timedelta
import sqlite3

import pytest

from ff_agent.bank import Bank,TZ
from ff_agent.card_working import validate_card_slots


def make_bank(tmp_path):
    now=[datetime(2026,10,27,1,0,tzinfo=TZ)]
    return Bank(tmp_path/'card-working.db',clock=lambda:now[0]),now


def validated(bank,value,user='demo'):
    with bank.transaction() as db:
        db.execute('PRAGMA query_only=ON')
        before=db.total_changes
        result=validate_card_slots(db,user,value,bank.now().timestamp())
        assert db.total_changes==before
        assert db.execute('SELECT 1').fetchone()[0]==1
        return result


def pending(bank,**changes):
    return {'mode':'prepare','action':'report_lost','started_at':bank.now().timestamp(),**changes}


def prepare(bank,user='demo'):
    card='card_a_daily' if user=='demo' else 'card_b_daily'
    return bank.prepare_card_status(user,card,'report_lost','memory-source',1)


def test_valid_pending_is_a_reference_not_a_new_authorization_and_inputs_are_unchanged(tmp_path):
    bank,_=make_bank(tmp_path)
    value={'pending_card':{**pending(bank),'untrusted':'drop me'},'memories':[{'action':'restore'}]}
    before=deepcopy(value)
    result=validated(bank,value)
    assert result=={'pending_card':pending(bank)}
    assert result['pending_card'] is not value['pending_card']
    assert value==before


@pytest.mark.parametrize('changes',[{'mode':'unknown'},{'mode':'prepare','action':'any'},
    {'action':'report_loss'},{'started_at':True},{'started_at':float('nan')},
    {'started_at':float('inf')},{'operation_watermark':True},{'operation_watermark':-1}])
def test_invalid_pending_schema_never_supplies_a_slot(tmp_path,changes):
    bank,_=make_bank(tmp_path)
    assert validated(bank,{'pending_card':pending(bank,**changes)})=={}


def test_stop_mode_retains_its_purpose_instead_of_becoming_prepare(tmp_path):
    bank,_=make_bank(tmp_path)
    value={'pending_card':pending(bank,mode='stop',action='any',operation_watermark=0)}
    assert validated(bank,value)==value


def test_pending_ttl_is_independent_and_clock_rollback_fails_closed(tmp_path):
    bank,now=make_bank(tmp_path)
    value={'pending_card':pending(bank)}
    now[0]+=timedelta(seconds=600)
    assert 'pending_card' in validated(bank,value)
    now[0]+=timedelta(microseconds=1)
    assert validated(bank,value)=={}
    now[0]-=timedelta(seconds=601)
    assert validated(bank,value)=={}


def test_later_owned_card_operation_clears_pending_selection_but_other_user_does_not(tmp_path):
    bank,_=make_bank(tmp_path)
    value={'pending_card':pending(bank,operation_watermark=0)}
    prepare(bank,'guest')
    assert 'pending_card' in validated(bank,value)
    prepare(bank,'demo')
    assert validated(bank,value)=={}


def test_future_operation_watermark_is_not_trusted(tmp_path):
    bank,_=make_bank(tmp_path)
    assert validated(bank,{'pending_card':pending(bank,operation_watermark=999)})=={}


def test_recent_card_uses_owned_bank_operation_time_not_callers_claim(tmp_path):
    bank,now=make_bank(tmp_path)
    op=prepare(bank)
    value={'recent_card':{'operation_id':op['id'],'started_at':123,'extra':'drop'}}
    assert validated(bank,value)=={'recent_card':{'operation_id':op['id'],'started_at':op['created_at']}}
    now[0]+=timedelta(seconds=601)
    value['recent_card']['started_at']=bank.now().timestamp()
    assert validated(bank,value)=={}


def test_recent_pointer_cannot_refer_to_foreign_or_non_card_or_nonexistent_operation(tmp_path):
    bank,_=make_bank(tmp_path)
    foreign=prepare(bank,'guest')
    transfer=bank.prepare_transfer('demo','p_mom',100,'unrelated-transfer')
    for opid in (foreign['id'],transfer['id'],'invented'):
        assert validated(bank,{'recent_card':{'operation_id':opid}})=={}


def test_recent_pointer_rechecks_card_and_account_ownership(tmp_path):
    bank,_=make_bank(tmp_path)
    op=prepare(bank)
    with bank.transaction() as db:
        db.execute("UPDATE bank_cards SET user_id='guest' WHERE id='card_a_daily'")
    assert validated(bank,{'recent_card':{'operation_id':op['id']}})=={}


def test_malformed_payload_and_notes_cannot_create_working_memory(tmp_path):
    bank,_=make_bank(tmp_path)
    op=prepare(bank)
    with bank.transaction() as db:
        db.execute('UPDATE operations SET payload=? WHERE id=?',('not-json',op['id']))
    assert validated(bank,{'recent_card':{'operation_id':op['id']}})=={}
    assert validated(bank,{'memories':[{'pending_card':pending(bank)}],'recent_turns':[{'content':'use card_a_daily'}]})=={}
    assert validated(bank,None)=={}


def test_database_failure_is_not_silently_reported_as_empty_memory():
    with sqlite3.connect(':memory:') as db:
        db.row_factory=sqlite3.Row
        with pytest.raises(sqlite3.OperationalError):
            validate_card_slots(db,'demo',{'pending_card':{'mode':'stop','action':'any','started_at':1000,'operation_watermark':0}},1000)
