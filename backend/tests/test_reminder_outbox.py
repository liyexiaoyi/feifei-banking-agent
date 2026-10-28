from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.bank import Bank,DomainError
from ff_agent.reminder_outbox import bootstrap_reminders,check_reminders,list_reminders,ack_reminder

def setup(tmp_path):
    now=[datetime(2026,10,27,10,tzinfo=ZoneInfo('Asia/Shanghai'))]
    bank=Bank(tmp_path/'reminders.db',clock=lambda:now[0]);bootstrap_reminders(bank)
    with bank.transaction() as db:
        db.execute('DELETE FROM subscriptions')
        for sid,offset in [('due',0),('soon',3),('later',4),('past',-1)]:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?)',(sid,'demo','商户'+sid,100,(now[0]+timedelta(days=offset)).date().isoformat(),'ACTIVE',1,'合成关系'))
    return bank,now

def consent(bank,user='demo'):
    op=bank.prepare_subscription_query(user,'reminder-consent');bank.confirm(user,op['id'])

def test_no_implicit_consent_and_only_current_three_day_window(tmp_path):
    bank,_=setup(tmp_path)
    for fn in [check_reminders,list_reminders]:
        with pytest.raises(DomainError) as e:fn(bank,'demo')
        assert e.value.code=='SUBSCRIPTION_CONSENT_REQUIRED'
    consent(bank)
    result=check_reminders(bank,'demo');assert result['created_count']==2
    assert {r['merchant'] for r in result['reminders']}=={'商户due','商户soon'}
    for item in result['reminders']:
        assert set(item)=={'id','merchant','amount_cents','charge_date','acknowledged','version','simulated'}
        assert item['simulated'] is True and item['acknowledged'] is False

def test_concurrent_checks_and_restart_do_not_duplicate_or_touch_financial_state(tmp_path):
    bank,now=setup(tmp_path);consent(bank)
    op=bank.prepare_transfer('demo','p_mom',10000,'unresolved')
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN',expires_at=0 WHERE id=?",(op['id'],))
        assets=[tuple(r) for r in db.execute('SELECT * FROM accounts')];ops=[tuple(r) for r in db.execute('SELECT * FROM operations')]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:check_reminders(bank,'demo'),range(2)))
    assert sum(r['created_count'] for r in results)==2
    restarted=Bank(bank.path,clock=lambda:now[0]);bootstrap_reminders(restarted)
    assert check_reminders(restarted,'demo')['created_count']==0
    with bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM renewal_reminders').fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM audit WHERE event='REMINDER_CREATED'").fetchone()[0]==2
        assert assets==[tuple(r) for r in db.execute('SELECT * FROM accounts')]
        assert ops==[tuple(r) for r in db.execute('SELECT * FROM operations')]

def test_ack_is_owner_bound_versioned_and_idempotent(tmp_path):
    bank,_=setup(tmp_path);consent(bank);consent(bank,'guest')
    note=check_reminders(bank,'demo')['reminders'][0]
    with pytest.raises(DomainError) as e:ack_reminder(bank,'guest',note['id'],1)
    assert e.value.status==404
    done=ack_reminder(bank,'demo',note['id'],1);assert done['version']==2 and done['acknowledged']
    assert ack_reminder(bank,'demo',note['id'],1)==done
    assert ack_reminder(bank,'demo',note['id'],2)==done
    for bad in [True,0,'1']:
        with pytest.raises(DomainError) as e:ack_reminder(bank,'demo',note['id'],bad)
        assert e.value.status==422
    with pytest.raises(DomainError) as e:ack_reminder(bank,'demo',note['id'],3)
    assert e.value.status==409
    with bank.transaction() as db:assert db.execute("SELECT COUNT(*) FROM audit WHERE event='REMINDER_ACKNOWLEDGED'").fetchone()[0]==1

def test_expired_authorization_and_changed_relationship_hide_old_reminder(tmp_path):
    bank,now=setup(tmp_path);consent(bank);notes=check_reminders(bank,'demo')['reminders']
    with bank.transaction() as db:db.execute("UPDATE subscriptions SET status='CANCELLED' WHERE id='due'")
    current=list_reminders(bank,'demo')['reminders'];assert len(current)==1 and current[0]['merchant']=='商户soon'
    now[0]+=timedelta(minutes=31)
    for fn in [list_reminders,check_reminders]:
        with pytest.raises(DomainError) as e:fn(bank,'demo')
        assert e.value.status==403
    with pytest.raises(DomainError) as e:ack_reminder(bank,'demo',notes[0]['id'],1)
    assert e.value.status==403

def test_reminder_api_uses_csrf_consent_strict_body_and_session_owner(tmp_path):
    with TestClient(create_app(tmp_path/'reminder-api.db')) as client:
        assert client.get('/api/subscriptions/reminders').status_code==401
        session=client.post('/api/session',json={'user':'demo'}).json()
        assert client.post('/api/subscriptions/reminders/check',json={}).status_code==403
        client.headers['X-CSRF-Token']=session['csrf']
        assert client.post('/api/subscriptions/reminders/check',json={}).status_code==403
        consent(client.app.state.bank)
        r=client.post('/api/subscriptions/reminders/check',json={});assert r.status_code==200
        item=r.json()['reminders'][0]
        for body in [{'user_id':'guest'},{'confirmed':True}]:assert client.post('/api/subscriptions/reminders/check',json=body).status_code==422
        assert client.get('/api/subscriptions/reminders?user=guest').status_code==422
        assert client.post('/api/subscriptions/reminders/'+item['id']+'/ack',json={'expected_version':True}).status_code==422
        assert client.post('/api/subscriptions/reminders/'+item['id']+'/ack',json={'expected_version':1}).status_code==200

@pytest.mark.parametrize('column,value',[('next_charge','not-a-date'),('amount_cents',1.5)])
def test_invalid_active_relationship_does_not_look_like_no_due_reminder(tmp_path,column,value):
    bank,_=setup(tmp_path);consent(bank)
    with bank.transaction() as db:db.execute('UPDATE subscriptions SET '+column+'=? WHERE id=?',(value,'due'))
    with pytest.raises(DomainError) as e:check_reminders(bank,'demo')
    assert e.value.status==503

def test_changed_merchant_refreshes_current_notice_and_revokes_old_ack_version(tmp_path):
    bank,_=setup(tmp_path);consent(bank)
    old=next(r for r in check_reminders(bank,'demo')['reminders'] if r['merchant']=='商户due')
    ack_reminder(bank,'demo',old['id'],1)
    with bank.transaction() as db:db.execute("UPDATE subscriptions SET merchant='更正后的商户' WHERE id='due'")
    current=check_reminders(bank,'demo')['reminders']
    changed=next(r for r in current if r['merchant']=='更正后的商户')
    assert changed['id']==old['id'] and not changed['acknowledged'] and changed['version']==3
    with pytest.raises(DomainError) as e:ack_reminder(bank,'demo',old['id'],1)
    assert e.value.status==409
