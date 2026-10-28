import pytest

from ff_agent.clarification import render_clarify,sanitize_legacy_reply


@pytest.mark.parametrize('text',['虚拟卡怎么申请','给我申请张虚拟卡'])
def test_supported_virtual_card_queries_do_not_claim_the_feature_is_missing(text):
    message,source=render_clarify('虚拟卡已申请成功',text,{})
    assert '尚未实现' not in message and '已申请成功' not in message
    assert '确认' in message and source=='template'


@pytest.mark.parametrize('message',['虚拟卡申请成功','虚拟卡已开通','已经生成虚拟卡'])
def test_model_virtual_card_success_claim_in_greeting_is_guarded(message):
    result,source=render_clarify(message,'你好',{})
    assert result!=message and source=='guarded'
    old={'message':message,'cards':[],'tasks':[{'type':'clarify','reply_source':'model_chat'}]}
    assert sanitize_legacy_reply(old,'你好')['message']!=message
