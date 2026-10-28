from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from ff_agent.bank import Bank,DomainError
from ff_agent.api import create_app
from ff_agent.recurring_insights import detect_recurring,recurring_insights

TZ=ZoneInfo('Asia/Shanghai')

def row(i,at,amount=1500,merchant='周期商户',kind='EXPENSE'):
    return dict(id=i,occurred_at=at,amount_cents=amount,merchant=merchant,kind=kind)

def test_month_end_is_a_candidate_with_evidence_not_a_confirmed_subscription():
    rows=[row('a','2026-01-31T10:00:00+08:00'),row('b','2026-02-28T10:00:00+08:00'),row('c','2026-03-31T10:00:00+08:00')]
    found=detect_recurring(rows)
    assert len(found)==1
    c=found[0]
    assert c['occurrences']==3 and c['evidence_ids']==['a','b','c'] and c['verified'] is False
    assert c['amount_cents']==1500 and '25' in c['rule'] and c['last_seen'].endswith('+08:00')
    assert detect_recurring([row('a','2026-01-01'),row('b','2026-01-27')])==[]

@pytest.mark.parametrize('days,expected',[(24,False),(25,True),(35,True),(36,False)])
def test_monthly_gap_boundaries(days,expected):
    start=datetime(2026,1,15,tzinfo=TZ)
    found=detect_recurring([row('a',start.isoformat()),row('b',(start+timedelta(days=days)).isoformat())])
    assert bool(found) is expected

def test_daily_same_amount_cannot_skip_rows_to_fake_monthly_recurrence():
    start=datetime(2026,1,20,tzinfo=TZ)
    rows=[row(str(i),(start+timedelta(days=i)).isoformat()) for i in range(45)]
    assert detect_recurring(rows)==[]
    assert detect_recurring([row('a','2026-01-01'),row('b','2026-02-01',kind='REFUND')])==[]
    assert detect_recurring([row('a','2026-01-01'),row('b','2026-02-01',merchant='另一商户')])==[]

@pytest.mark.parametrize('change',[{'occurred_at':'2026-02-30'}, {'amount_cents':True}, {'amount_cents':1.2}, {'amount_cents':-1}])
def test_malformed_candidate_data_is_unavailable(change):
    bad=row('a','2026-01-01');bad.update(change)
    with pytest.raises(DomainError) as error:detect_recurring([bad])
    assert error.value.status==503

def test_service_filters_user_and_future_without_changing_bank_relationships(tmp_path):
    now=datetime(2026,3,31,12,tzinfo=TZ);bank=Bank(tmp_path/'recurring.db',clock=lambda:now)
    with bank.transaction() as db:
        db.execute('DELETE FROM transactions')
        for user,account,prefix in [('demo','a_daily','d'),('guest','b_daily','g')]:
            for i,at in enumerate(['2026-01-31T10:00:00+08:00','2026-02-28T10:00:00+08:00','2026-03-31T10:00:00+08:00','2026-04-30T10:00:00+08:00']):
                db.execute('INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,merchant,occurred_at) VALUES(?,?,?,?,?,?,?,?)',(prefix+str(i),user,account,'EXPENSE',1500,'其他','商户'+prefix,at))
        before=[tuple(r) for r in db.execute('SELECT * FROM subscriptions')]
    result=recurring_insights(bank,'demo')
    assert result['simulated'] and result['as_of']=='2026-03-31'
    assert result['candidates'][0]['evidence_ids']==['d0','d1','d2']
    assert '商户g' not in str(result)
    with bank.transaction() as db:
        assert before==[tuple(r) for r in db.execute('SELECT * FROM subscriptions')]
        assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0
    with pytest.raises(DomainError):recurring_insights(bank,'missing')

def test_recurring_api_has_no_user_override_or_extra_query(tmp_path):
    with TestClient(create_app(tmp_path/'recurring-api.db')) as client:
        assert client.get('/api/bill/recurring').status_code==401
        client.post('/api/session',json={'user':'guest'})
        assert client.get('/api/bill/recurring').json()=={'simulated':True,'as_of':client.app.state.bank.now().date().isoformat(),'candidates':[]}
        assert client.get('/api/bill/recurring?user=demo').status_code==422
