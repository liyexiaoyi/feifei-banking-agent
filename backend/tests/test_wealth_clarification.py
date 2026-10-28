import pytest
from ff_agent.clarification import render_clarify,sanitize_legacy_reply

@pytest.mark.parametrize('prompt',['模拟理财怎么申购','赎回有什么规则','查询理财持仓'])
def test_implemented_wealth_is_not_described_as_missing(prompt):
    text,_=render_clarify('已申购成功',prompt,{})
    assert '尚未实现' not in text and '暂不支持' not in text

@pytest.mark.parametrize('claim',['申购成功','已赎回成功','持仓已增加','已买入演示产品甲','收益已到账'])
def test_plain_chat_and_legacy_cache_cannot_invent_wealth_success(claim):
    text,source=render_clarify(claim,'谢谢',{})
    assert text!=claim and source=='guarded'
    old={'message':claim,'cards':[],'tasks':[{'type':'clarify','state':'WAITING_USER','reply_source':'model_chat'}]}
    assert sanitize_legacy_reply(old,'谢谢')['message']!=claim
