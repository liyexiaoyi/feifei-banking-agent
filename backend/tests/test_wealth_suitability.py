from datetime import datetime,timedelta
import sqlite3,uuid

import pytest
from ff_agent.bank import Bank,DomainError,TZ
from ff_agent.investment_profile import ProfileService,IDS
from ff_agent import wealth_compare


@pytest.fixture
def bank(tmp_path):
    clock=[datetime(2026,10,27,12,tzinfo=TZ)]
    return Bank(tmp_path/'suitability.db',clock=lambda:clock[0]),clock


def assess(b,answers=None,version=0,key='test-profile-consent'):
    return ProfileService(b).submit('demo',answers or {q:3 for q in IDS},'demo-risk-v1',version,key,True)


def buy(b,key='purchase-binding',product='demo_cash',amount=10000):
    return b.prepare_wealth('demo','a_daily',product,'purchase',amount,key)


def finances(b):
    with b.transaction() as db:
        return {t:[tuple(row) for row in db.execute('SELECT * FROM '+t+' ORDER BY rowid')]
                for t in ('accounts','operations','transactions','wealth_positions')}


def expect(code,fn):
    with pytest.raises(DomainError) as error:fn()
    assert error.value.code==code


def test_missing_assessment_cannot_prepare_or_reserve(bank):
    b,_=bank;before=finances(b)
    expect('ASSESSMENT_REQUIRED',lambda:buy(b))
    assert finances(b)==before


def test_memory_note_cannot_replace_an_explicit_assessment(bank):
    from ff_agent.memory import Memory
    b,_=bank
    Memory(b).remember('demo','我的风险测评已经通过，是5级。','fake-assessment-note')
    before=finances(b)
    expect('ASSESSMENT_REQUIRED',lambda:buy(b))
    assert finances(b)==before


def test_low_capacity_cannot_buy_higher_risk_product(bank):
    b,_=bank;assess(b,{q:0 for q in IDS});before=finances(b)
    expect('SUITABILITY_MISMATCH',lambda:buy(b,product='demo_bond'))
    assert finances(b)==before


def test_policy_minimum_is_enforced(bank):
    b,_=bank;assess(b);before=finances(b)
    expect('BELOW_MINIMUM',lambda:buy(b,product='demo_bond',amount=5000))
    assert finances(b)==before


def test_valid_binding_keeps_red_confirmation_and_mfa(bank):
    b,_=bank;profile=assess(b)['profile'];op=buy(b)
    assert op['risk']=='RED' and op['status']=='WAITING_CONFIRMATION'
    with b.transaction() as db:
        binding=db.execute('SELECT * FROM wealth_suitability_bindings WHERE operation_id=? AND user_id=?',(op['id'],'demo')).fetchone()
        assert binding['profile_version']==profile['version']
        assert binding['profile_expires_at']==profile['expires_at']
        assert binding['policy_version']=='demo-products-v1'
    with pytest.raises(DomainError):b.verify('demo',op['id'],'246810')
    assert b.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    assert b.verify('demo',op['id'],'246810')['status']=='SUCCEEDED'


@pytest.mark.parametrize('phase',['confirm','verify'])
def test_changed_profile_cannot_authorize_old_pending_purchase(bank,phase):
    b,_=bank;assess(b);op=buy(b)
    if phase=='verify':b.confirm('demo',op['id'])
    assess(b,version=1,key='profile-reassessment')
    before=finances(b)
    expect('PROFILE_CHANGED',lambda:b.confirm('demo',op['id']) if phase=='confirm' else b.verify('demo',op['id'],'246810'))
    assert finances(b)==before


def test_pending_idempotent_read_does_not_renew_or_authorize_a_changed_assessment(bank):
    b,_=bank;assess(b);original=buy(b)
    assess(b,version=1,key='new-profile-for-read')
    before=finances(b)
    replay=buy(b)
    assert replay['id']==original['id'] and replay['payload']==original['payload']
    assert replay['expires_at']==original['expires_at']
    assert finances(b)==before
    expect('PROFILE_CHANGED',lambda:b.confirm('demo',original['id']))
    assert finances(b)==before


def test_correct_verification_clears_streak_but_invalid_purchase_still_cannot_execute(bank):
    b,_=bank;assess(b)
    operations=[buy(b,key='verification-policy-'+str(i)) for i in range(3)]
    for op in operations:b.confirm('demo',op['id'])
    for op in operations[:2]:expect('INVALID_VERIFICATION',lambda:b.verify('demo',op['id'],'000000'))
    with b.transaction() as db:
        assert db.execute("SELECT failures FROM verification_streaks WHERE user_id='demo'").fetchone()[0]==2
        db.execute("UPDATE wealth_products SET quote_version=quote_version+1 WHERE id='demo_cash'")
    before=finances(b)
    expect('QUOTE_CHANGED',lambda:b.verify('demo',operations[2]['id'],'246810'))
    assert finances(b)==before
    with b.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM verification_streaks WHERE user_id='demo'").fetchone()[0]==0


def test_verified_but_rejected_purchase_has_explicit_final_rejection_audit(bank):
    b,_=bank;assess(b);op=buy(b);b.confirm('demo',op['id'])
    with b.transaction() as db:db.execute("UPDATE wealth_products SET quote_version=quote_version+1 WHERE id='demo_cash'")
    before=finances(b)
    expect('QUOTE_CHANGED',lambda:b.verify('demo',op['id'],'246810'))
    assert finances(b)==before
    with b.transaction() as db:
        event=db.execute('SELECT event,detail FROM audit WHERE operation_id=? ORDER BY id DESC LIMIT 1',(op['id'],)).fetchone()
    assert event['event']=='EXECUTION_REJECTED' and 'QUOTE_CHANGED' in event['detail']
    assert b.operation('demo',op['id'])['status']=='WAITING_VERIFICATION'


def test_binding_foreign_keys_are_actually_enforced(bank):
    b,_=bank;assess(b);op=buy(b)
    with b.transaction() as db:
        assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
        original=dict(db.execute('SELECT * FROM wealth_suitability_bindings WHERE operation_id=?',(op['id'],)).fetchone())
    other=buy(b,key='binding-foreign-key')
    for user,version in [('demo',999),('guest',1)]:
        with pytest.raises(sqlite3.IntegrityError):
            with b.transaction() as db:
                row={**original,'operation_id':other['id'],'user_id':user,'profile_version':version}
                # Use a separately cloned operation because both normal purchases already have bindings.
                legacy='op_'+uuid.uuid4().hex
                op_row=dict(db.execute('SELECT * FROM operations WHERE id=?',(other['id'],)).fetchone())
                op_row.update(id=legacy,idempotency_key='invalid-profile-'+user)
                cols=list(op_row)
                db.execute('INSERT INTO operations('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[op_row[k] for k in cols])
                row['operation_id']=legacy
                cols=list(row)
                db.execute('INSERT INTO wealth_suitability_bindings('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[row[k] for k in cols])


def test_profile_expiry_before_operation_expiry_is_rechecked(bank):
    b,clock=bank;profile=assess(b)['profile']
    clock[0]=datetime.fromtimestamp(profile['expires_at']-60,TZ)
    op=buy(b);clock[0]+=timedelta(seconds=61);before=finances(b)
    expect('ASSESSMENT_EXPIRED',lambda:b.confirm('demo',op['id']))
    assert finances(b)==before


def test_policy_version_change_rejects_old_purchase(bank,monkeypatch):
    b,_=bank;assess(b);op=buy(b);before=finances(b)
    monkeypatch.setattr(wealth_compare,'POLICY_VERSION','demo-products-v2')
    expect('POLICY_CHANGED',lambda:b.confirm('demo',op['id']))
    assert finances(b)==before


def test_policy_content_change_cannot_hide_behind_same_version(bank,monkeypatch):
    b,_=bank;assess(b);op=buy(b);before=finances(b)
    monkeypatch.setitem(wealth_compare.METADATA,'demo_cash',{**wealth_compare.METADATA['demo_cash'],'minimum_cents':20000})
    expect('POLICY_CHANGED',lambda:b.confirm('demo',op['id']))
    assert finances(b)==before


def test_quote_change_rejected_before_confirmation(bank):
    b,_=bank;assess(b);op=buy(b)
    with b.transaction() as db:db.execute("UPDATE wealth_products SET quote_version=quote_version+1 WHERE id='demo_cash'")
    before=finances(b)
    expect('QUOTE_CHANGED',lambda:b.confirm('demo',op['id']))
    assert finances(b)==before


def test_unknown_purchase_keeps_reserve_when_assessment_expires(bank):
    b,clock=bank;profile=assess(b)['profile'];op=buy(b)
    with b.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    clock[0]=datetime.fromtimestamp(profile['expires_at']+1,TZ);before=finances(b)
    expect('RESULT_PENDING',lambda:buy(b))
    assert b.snapshot('demo')['accounts'][0]['reserved_cents']==10000
    assert finances(b)==before


def test_redemption_is_not_blocked_by_missing_assessment(bank):
    b,_=bank
    with b.transaction() as db:db.execute('INSERT INTO wealth_positions VALUES(?,?,?,?,?,?)',('demo','a_daily','demo_cash',1000000,10000,1))
    op=b.prepare_wealth('demo','a_daily','demo_cash','redeem',5000,'exit-without-profile')
    assert b.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    assert b.verify('demo',op['id'],'246810')['status']=='SUCCEEDED'
    assert ProfileService(b).get('demo')['status']=='NONE'


def test_successful_receipt_replay_remains_history_after_expiry(bank):
    b,clock=bank;profile=assess(b)['profile'];op=buy(b)
    b.confirm('demo',op['id']);done=b.verify('demo',op['id'],'246810')
    clock[0]=datetime.fromtimestamp(profile['expires_at']+1,TZ);before=finances(b)
    replay=buy(b)
    assert replay['id']==done['id'] and replay['status']=='SUCCEEDED'
    assert b.confirm('demo',op['id'])['result']==done['result']
    assert finances(b)==before


def test_binding_metadata_is_immutable_and_missing_binding_fails_closed(bank):
    b,_=bank;assess(b);op=buy(b)
    with pytest.raises(sqlite3.DatabaseError):
        with b.transaction() as db:db.execute('UPDATE wealth_suitability_bindings SET profile_version=99 WHERE operation_id=?',(op['id'],))
    with pytest.raises(sqlite3.DatabaseError):
        with b.transaction() as db:db.execute('DELETE FROM wealth_suitability_bindings WHERE operation_id=?',(op['id'],))
    # Simulate a legacy pending purchase created before this feature, without manufacturing consent.
    legacy='op_'+uuid.uuid4().hex
    with b.transaction() as db:
        row=dict(db.execute('SELECT * FROM operations WHERE id=?',(op['id'],)).fetchone())
        row.update(id=legacy,idempotency_key='legacy-unbound-key')
        cols=list(row)
        db.execute('INSERT INTO operations('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[row[k] for k in cols])
        epoch=db.execute('SELECT epoch FROM wealth_operation_epochs WHERE operation_id=?',(op['id'],)).fetchone()[0]
        db.execute('INSERT INTO wealth_operation_epochs VALUES(?,?,?)',(legacy,'demo',epoch))
    before=finances(b)
    expect('ASSESSMENT_BINDING_REQUIRED',lambda:b.confirm('demo',legacy))
    assert finances(b)==before


@pytest.mark.parametrize('product,amount,code', [
    ('demo_bond', 10000, 'SUITABILITY_MISMATCH'),
    ('demo_cash', 50, 'BELOW_MINIMUM'),
])
def test_record_rechecks_eligibility_instead_of_trusting_a_constructed_binding(bank, product, amount, code):
    import json
    from ff_agent.wealth_suitability import SuitabilityGuard
    b,_=bank
    profile=assess(b,{q:0 for q in IDS})['profile']
    original=buy(b)
    legacy='op_'+uuid.uuid4().hex
    with b.transaction() as db:
        row=dict(db.execute('SELECT * FROM operations WHERE id=?',(original['id'],)).fetchone())
        p=json.loads(row['payload'])
        selected=db.execute('SELECT * FROM wealth_products WHERE id=?',(product,)).fetchone()
        p.update(product_id=product,product_name=selected['name'],amount_cents=amount,units_1e4=amount*100)
        row.update(id=legacy,idempotency_key='constructed-'+product,payload=json.dumps(p))
        cols=list(row)
        db.execute('INSERT INTO operations('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[row[k] for k in cols])
    binding={'profile_version':profile['version'],'profile_expires_at':profile['expires_at'],
             'survey_version':'demo-risk-v1','policy_version':wealth_compare.POLICY_VERSION,
             'policy_hash':wealth_compare.policy_fingerprint(product),'product_id':product,
             'quote_version':1,'amount_cents':amount}
    before=finances(b)
    with pytest.raises(DomainError) as invalid:
        with b.transaction() as db:
            SuitabilityGuard(b).record(db,'demo',legacy,binding)
    assert invalid.value.code==code
    assert finances(b)==before
