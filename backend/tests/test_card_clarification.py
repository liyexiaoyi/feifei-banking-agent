from copy import deepcopy

import pytest

from ff_agent.clarification import render_clarify, sanitize_legacy_reply


@pytest.mark.parametrize('text', ['银行卡怎么挂失', '取消挂失', '看看我的银行卡'])
def test_supported_card_clarifications_do_not_claim_the_feature_is_unimplemented(text):
    message, source = render_clarify('已经挂失成功', text, {})
    assert '尚未实现' not in message
    assert '卡' in message and '已挂失成功' not in message
    assert source == 'template'


@pytest.mark.parametrize('text', ['修改银行卡密码', '我想办银行卡', '申请信用卡额度', '我要申购基金'])
def test_unimplemented_products_are_still_honestly_declined(text):
    message, _ = render_clarify('可以立即办好', text, {})
    assert '尚未实现' in message or '暂不支持' in message


@pytest.mark.parametrize('message', ['已帮你挂失成功', '银行卡已挂失', '已解除挂失', '解挂成功', '已撤回挂失申请'])
def test_a_model_cannot_announce_a_card_result_in_ordinary_chat(message):
    guarded, source = render_clarify(message, '谢谢', {})
    assert guarded != message and source == 'guarded'


def test_old_model_chat_marker_does_not_preserve_a_previously_unrecognized_card_success_claim():
    old = {'message': '已帮你挂失成功', 'cards': [],
           'tasks': [{'type': 'clarify', 'state': 'WAITING_USER', 'reply_source': 'model_chat'}]}
    before = deepcopy(old)
    cleaned = sanitize_legacy_reply(old, '谢谢')
    assert cleaned['message'] != old['message']
    assert cleaned['tasks'][0]['reply_source'] == 'guarded'
    assert old == before
