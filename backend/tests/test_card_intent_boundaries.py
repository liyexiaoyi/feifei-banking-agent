import pytest

from ff_agent.card_intent import parse_card_intent


@pytest.mark.parametrize('text', ['不要解挂', '先别解挂', '别解除挂失', '撤回本次解挂', '取消本次解除挂失'])
def test_explicit_restore_withdrawal_cannot_prepare_restore(text):
    assert parse_card_intent(text) == [{'type': 'card_stop', 'card': '未指定', 'action': 'restore'}]


@pytest.mark.parametrize('text', ['挂失多少钱', '挂失的费用', '挂失是不是退款',
    '我没说要挂失', '挂失不是退款', '先挂失再解挂', '不要挂失而是解挂',
    '挂失日常那张或者刚才那张', '挂失1234和那张'])
def test_questions_negated_intent_and_competing_requests_require_clarification(text):
    result = parse_card_intent(text)
    assert result and result[0]['type'] == 'clarify'
    assert len(result) == 1


@pytest.mark.parametrize('mode,kind', [('prepare', 'card_status'), ('stop', 'card_stop')])
@pytest.mark.parametrize('text,card', [('就日常那张', '日常账户'), ('尾号0826', '0826')])
def test_selection_followup_preserves_the_pending_action_without_broad_inference(mode, kind, text, card):
    context = {'pending_card': {'mode': mode, 'action': 'restore'}}
    assert parse_card_intent(text, context) == [{'type': kind, 'card': card, 'action': 'restore'}]
    assert parse_card_intent(text) is None
