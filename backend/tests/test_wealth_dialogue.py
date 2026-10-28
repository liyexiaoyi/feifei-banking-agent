import pytest
from ff_agent.wealth_dialogue import WealthDialogue
from ff_agent.bank import Bank

@pytest.fixture
def bank(tmp_path):return Bank(tmp_path/'dialogue.db')

def action(**kw):return {'type':'wealth_trade','action':'purchase','product':'未指定','account':'未指定','amount_cents':None,**kw}

def test_missing_fields_are_asked_one_at_a_time_and_never_prepare(bank):
    dialogue=WealthDialogue(bank)
    for a,word in [(action(),'产品'),(action(product='演示产品甲'),'账户'),(action(product='演示产品甲',account='日常账户'),'金额')]:
        answer=dialogue.handle('demo',a,'key',{})
        assert answer['state']=='WAITING_USER' and word in answer['lines'][0]
        assert answer['cards'][0]['type']=='wealth_selection'
        assert answer['working']['pending_wealth']['amount_cents'] is None
    assert bank.snapshot('demo')['operations']==[]

def test_complete_dialogue_only_prepares_server_owned_red_operation(assessed_bank):
    bank=assessed_bank
    a=action(product='演示产品甲',account='日常账户',amount_cents=30000)
    answer=WealthDialogue(bank).handle('demo',a,'key',{})
    op=answer['cards'][0]['operation']
    assert op['type']=='wealth_purchase' and op['risk']=='RED' and op['status']=='WAITING_CONFIRMATION'
    assert answer['working']=={} and bank.wealth('demo')['positions']==[]
    assert op['payload']['account_id']=='a_daily' and op['payload']['product_id']=='demo_cash'

def test_dialogue_never_treats_identifiers_or_notes_as_names(bank):
    a=action(product='demo_cash',account='a_daily',amount_cents=30000)
    answer=WealthDialogue(bank).handle('demo',a,'key',{'memories':[action(product='演示产品甲',account='日常账户')]})
    assert answer['state']=='WAITING_USER' and not any(c['type']=='operation' for c in answer['cards'])
    assert bank.snapshot('demo')['operations']==[]


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_bank(bank):
    _consent_test_profile(bank)
    return bank
