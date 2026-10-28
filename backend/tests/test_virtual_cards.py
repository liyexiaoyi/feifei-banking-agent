from datetime import datetime, timedelta
import json

import pytest

from ff_agent.bank import Bank, DomainError, TZ


def fixture(tmp_path):
    now=[datetime(2026,10,27,2,45,tzinfo=TZ)]
    return Bank(tmp_path/'virtual-cards.db',clock=lambda:now[0]),now


def physical_rows(bank):
    with bank.transaction() as db:
        return [tuple(r) for r in db.execute('SELECT * FROM bank_cards ORDER BY id')]


def finances(bank):
    with bank.transaction() as db:
        return ([tuple(r) for r in db.execute('SELECT * FROM accounts ORDER BY id')],
                [tuple(r) for r in db.execute('SELECT * FROM transactions ORDER BY id')])


def create(bank,key='application',account='a_daily'):
    op=bank.prepare_virtual_card('demo',account,key)
    assert op['type']=='virtual_card_create' and op['risk']=='YELLOW'
    assert op['status']=='WAITING_CONFIRMATION'
    return bank.confirm('demo',op['id'])


def test_virtual_application_is_yellow_confirmed_once_and_never_changes_money(tmp_path):
    bank,_=fixture(tmp_path)
    before,rows=finances(bank),physical_rows(bank)
    op=bank.prepare_virtual_card('demo','a_daily','application')
    assert len(bank.list_cards('demo'))==2
    assert set(op['payload'])=={'account_id','account_name','card_name','simulated'}
    assert op['risk']=='YELLOW' and op['status']=='WAITING_CONFIRMATION'
    with pytest.raises(DomainError):
        bank.verify('demo',op['id'],'246810')
    done=bank.confirm('demo',op['id'])
    assert done['status']=='SUCCEEDED'
    assert done['result']['simulated'] is True
    card=next(c for c in bank.list_cards('demo') if c['id']==done['result']['card_id'])
    assert card['id'].startswith('vcard_') and card['status']=='ACTIVE'
    assert set(card)=={'id','account_id','name','masked_number','status','version','simulated'}
    assert 'DEMO' in card['masked_number'] and card['masked_number'][-4:].isdigit()
    assert all(key not in json.dumps(card).lower() for key in ('cvv','password','pan'))
    assert bank.confirm('demo',op['id'])['result']==done['result']
    assert bank.prepare_virtual_card('demo','a_daily','application')['id']==op['id']
    assert len(bank.list_cards('demo'))==3
    assert finances(bank)==before and physical_rows(bank)==rows


def test_virtual_card_loss_restore_uses_red_and_updates_only_virtual_storage(tmp_path):
    bank,_=fixture(tmp_path)
    result=create(bank)
    card_id=result['result']['card_id']
    before,rows=finances(bank),physical_rows(bank)
    for action,version,status in [('report_lost',1,'LOST'),('restore',2,'ACTIVE')]:
        op=bank.prepare_card_status('demo',card_id,action,action,version)
        assert op['risk']=='RED'
        assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
        done=bank.verify('demo',op['id'],'246810')
        assert done['result']['status']==status
        assert physical_rows(bank)==rows and finances(bank)==before
    again=Bank(bank.path,clock=bank.clock)
    card=next(c for c in again.list_cards('demo') if c['id']==card_id)
    assert card['version']==3 and card['status']=='ACTIVE'
    assert len(again.list_cards('demo'))==3


@pytest.mark.parametrize('unresolved',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unresolved_virtual_application_is_not_replaced_with_a_new_key(tmp_path,unresolved):
    bank,_=fixture(tmp_path)
    op=bank.prepare_virtual_card('demo','a_daily','old')
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?',(unresolved,op['id']))
    for key in ('old','new'):
        with pytest.raises(DomainError):
            bank.prepare_virtual_card('demo','a_daily',key)
    assert len(bank.list_cards('demo'))==2
    assert bank.receipt('demo',op['id'])['observed_status']==unresolved


def test_one_virtual_per_account_is_local_constraint_and_other_accounts_remain_available(tmp_path):
    bank,_=fixture(tmp_path)
    create(bank)
    with pytest.raises(DomainError):
        bank.prepare_virtual_card('demo','a_daily','another')
    other=create(bank,'saving-application','a_saving')
    cards=bank.list_cards('demo')
    assert len(cards)==4 and len({c['id'] for c in cards})==4
    assert other['result']['card_id'] in {c['id'] for c in cards}
    assert {c['id'] for c in bank.list_cards('guest')}=={'card_b_daily','card_b_saving'}


def test_duplicate_public_card_id_in_corrupted_storage_is_rejected_instead_of_selecting_first(tmp_path):
    bank,_=fixture(tmp_path)
    with bank.transaction() as db:
        db.execute("INSERT INTO bank_virtual_cards VALUES(?,?,?,?,?,?,?,?)",
                   ('card_a_daily','demo','a_daily','碰撞演示卡','DEMO VIRTUAL ···· 1234','ACTIVE',1,'source'))
    with pytest.raises(DomainError):
        bank.list_cards('demo')
    with pytest.raises(DomainError):
        bank.prepare_card_status('demo','card_a_daily','report_lost','collision',1)
    assert all(row[-2]=='ACTIVE' for row in physical_rows(bank))
