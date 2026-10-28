from copy import deepcopy
from datetime import datetime

from ff_agent.bank import Bank,TZ
from ff_agent.virtual_dialogue import VirtualDialogue


def test_virtual_dialogue_wraps_a_pending_operation_and_uses_bank_clock_for_missing_fields(tmp_path):
    now=datetime(2026,10,27,3,tzinfo=TZ)
    bank=Bank(tmp_path/'virtual-dialogue.db',clock=lambda:now)
    service=VirtualDialogue(bank)
    missing=service.handle('demo','未指定','missing',{})
    assert missing['working']['pending_virtual_card']['started_at']==now.timestamp()
    assert missing['state']=='WAITING_USER'
    result=service.handle('demo','储蓄账户','apply',{})
    assert result['state']=='WAITING_USER'
    assert result['cards'][0]['type']=='operation'
    assert result['cards'][0]['operation']['status']=='WAITING_CONFIRMATION'


def test_virtual_dialogue_does_not_read_nested_unvalidated_working_data(tmp_path):
    now=datetime(2026,10,27,3,tzinfo=TZ)
    bank=Bank(tmp_path/'virtual-dialogue.db',clock=lambda:now)
    context={'working':{'pending_virtual_card':{'account':'未指定','started_at':123,'operation_watermark':99}}}
    original=deepcopy(context)
    result=VirtualDialogue(bank).handle('demo','未指定','missing',context)
    assert result['working']['pending_virtual_card']['started_at']==now.timestamp()
    assert result['working']['pending_virtual_card']['operation_watermark']==0
    assert context==original
