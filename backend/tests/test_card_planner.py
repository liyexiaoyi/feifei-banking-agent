from copy import deepcopy
import json

import httpx
import pytest

from ff_agent.planner import Planner, PlannerError, validate_plan


@pytest.mark.parametrize('action', [
    {'type': 'cards'},
    {'type': 'card_status', 'card': '0826', 'action': 'report_lost'},
    {'type': 'card_status', 'card': '储蓄账户', 'action': 'restore'},
    {'type': 'card_stop', 'card': '全部', 'action': 'any'},
    {'type': 'card_correct', 'card': '储蓄账户'},
])
def test_card_action_contract_accepts_only_bounded_suggestions(action):
    assert validate_plan({'actions': [action]}) == [action]


@pytest.mark.parametrize('action', [
    {'type': 'card_status', 'card': '0826', 'action': 'any'},
    {'type': 'card_status', 'card': '0826', 'action': 'report_loss'},
    {'type': 'card_status', 'card': '0826', 'action': 'restore', 'confirmed': True},
    {'type': 'card_status', 'card_id': 'card_a_daily', 'action': 'report_lost'},
    {'type': 'card_status', 'card': {'tail': '0826'}, 'action': 'report_lost'},
    {'type': 'card_stop', 'card': '0826', 'action': 'execute'},
    {'type': 'card_prepare', 'card': '0826', 'action': 'report_lost'},
    {'type': 'cards', 'version': 1},
])
def test_model_card_action_cannot_add_authority_or_change_enums(action):
    with pytest.raises(PlannerError):
        validate_plan({'actions': [action]})


def test_six_action_plan_limit_applies_across_card_and_old_business():
    with pytest.raises(PlannerError):
        validate_plan({'actions': [{'type': 'cards'}] * 7})


@pytest.mark.parametrize('mode', ['rules', 'model'])
@pytest.mark.parametrize('text,expected', [
    ('看看我的银行卡', {'type': 'cards'}),
    ('挂失尾号０８２６', {'type': 'card_status', 'card': '0826', 'action': 'report_lost'}),
    ('把储蓄那张解挂', {'type': 'card_status', 'card': '储蓄账户', 'action': 'restore'}),
    ('先不要挂失', {'type': 'card_stop', 'card': '未指定', 'action': 'report_lost'}),
    ('不是那张是储蓄那张', {'type': 'card_correct', 'card': '储蓄账户'}),
])
def test_explicit_card_intents_use_same_deterministic_parser_in_both_modes(monkeypatch, mode, text, expected):
    monkeypatch.setenv('FF_PLANNER_MODE', mode)
    # No model client or credential: these exact bounded intents need no HTTP.
    assert Planner().plan(text, {}) == [expected]


@pytest.mark.parametrize('mode,kind', [('prepare', 'card_status'), ('stop', 'card_stop')])
def test_card_followup_precedes_transfer_numeric_filling_without_mutating_context(mode, kind):
    context = {'pending_card': {'mode': mode, 'action': 'report_lost'}}
    original = deepcopy(context)
    assert Planner().plan('0826', context) == [{'type': kind, 'card': '0826', 'action': 'report_lost'}]
    assert context == original


def test_conflicting_legacy_working_slots_do_not_guess_tail_or_amount():
    context = {'pending_card': {'mode': 'prepare', 'action': 'report_lost'},
               'pending_transfer': {'payee': '妈妈'}}
    original = deepcopy(context)
    assert Planner().plan('0826', context)[0]['type'] == 'clarify'
    assert context == original


@pytest.mark.parametrize('text', ['要不要挂失', '挂失会退钱吗', '取消挂失'])
def test_card_process_question_does_not_generate_mutation(text):
    actions = Planner().plan(text, {})
    assert len(actions) == 1 and actions[0]['type'] == 'clarify'


@pytest.mark.parametrize('mode', ['rules', 'model'])
def test_compound_query_and_card_prepare_keeps_both_tasks(monkeypatch, mode):
    monkeypatch.setenv('FF_PLANNER_MODE', mode)
    actions = Planner().plan('查询余额，挂失日常那张银行卡', {})
    assert {'type': 'balance'} in actions
    assert {'type': 'card_status', 'card': '日常账户', 'action': 'report_lost'} in actions


def test_nonbank_card_does_not_become_a_card_operation():
    assert all(a['type'] not in ('cards', 'card_status', 'card_stop', 'card_correct')
               for a in Planner().plan('我的门禁卡丢了', {}))


def test_model_receives_only_bounded_card_purpose_without_internal_operation_or_authority(monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'model')
    monkeypatch.setenv('FF_MODEL_API_KEY', 'test-only-key')
    monkeypatch.setenv('FF_MODEL_BASE_URL', 'https://model.example/v1')
    monkeypatch.setenv('FF_MODEL_NAME', 'test-model')
    captured = []
    def transport(request):
        captured.append(json.loads(json.loads(request.content)['messages'][1]['content'])['context'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"actions":[{"type":"clarify","message":"请明确需求"}]}'}}]})
    context = {'pending_card': {'mode': 'stop', 'action': 'restore', 'operation_watermark': 99, 'started_at': 100},
               'recent_card': {'operation_id': 'internal-operation', 'confirmed': True}}
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        Planner(client=client).plan('请说明一下', context)
    assert captured[0]['pending_card'] == {'mode': 'stop', 'action': 'restore'}
    assert 'internal-operation' not in json.dumps(captured)
