from datetime import datetime

import pytest

from ff_agent.bank import Bank, TZ
from ff_agent.card_chat import CardChat
from ff_agent.card_intent import parse_card_intent


@pytest.mark.parametrize('text,target', [('不是那张，是尾号０８２６的卡','0826'),
    ('改成尾号0826那张卡','0826'), ('不是那张是日常那张','日常账户')])
def test_explicit_card_correction_can_name_a_tail_or_either_alias(text,target):
    assert parse_card_intent(text) == [{'type':'card_correct','card':target}]


def test_ambiguous_correction_never_turns_followup_into_an_unrelated_prepare(tmp_path):
    bank = Bank(tmp_path/'correction-target.db', clock=lambda: datetime(2026,10,27,2,tzinfo=TZ))
    chat = CardChat(bank)
    old = bank.prepare_card_status('demo','card_a_daily','report_lost','old',1)
    with bank.transaction() as db:
        db.execute("UPDATE bank_cards SET name='另一张储蓄演示卡' WHERE id='card_a_daily'")
    context={'recent_card':{'operation_id':old['id'],'started_at':old['created_at']}}
    ambiguous=chat.handle('demo',{'type':'card_correct','card':'储蓄账户'},'不是那张是储蓄那张','ambiguous',context)
    assert ambiguous['state'] == 'WAITING_USER'
    assert 'pending_card' not in ambiguous['working']
    assert ambiguous['working']['recent_card']['operation_id'] == old['id']
    assert bank.operation('demo',old['id'])['status'] == 'WAITING_CONFIRMATION'
    tail=next(c['masked_number'][-4:] for c in bank.list_cards('demo') if c['id']=='card_a_saving')
    text='不是那张，是尾号'+tail+'的卡'
    action=parse_card_intent(text)[0]
    corrected=chat.handle('demo',action,text,'specific',ambiguous['working'])
    assert corrected['state']=='WAITING_USER'
    assert bank.operation('demo',old['id'])['status']=='CANCELLED'
    assert len(bank.snapshot('demo')['operations'])==2
