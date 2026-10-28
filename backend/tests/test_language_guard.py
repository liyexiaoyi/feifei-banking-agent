import copy
import os
import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.language_guard import inspect_language
from ff_agent.planner import Planner


AMBIGUOUS = [
    '给妈妈转一千二元', '给妈妈转两百五元', '给妈妈转300元左右',
    '给妈妈转大概300元', '给妈妈转300到500元', '给妈妈转1,200元',
    '给妈妈转一二三元', '给妈妈转三点五元', '给妈妈转5块8',
    '给妈妈转两三百元', '给妈妈转三五百元', '给妈妈转几百块',
    '给妈妈转一万来块', '给妈妈转300来块', '给妈妈转百把块',
    '给妈妈转十几元', '给妈妈转一百多元', '给妈妈转一两千元',
    '给妈妈转300多元', '给妈妈转300余元', '给妈妈转几千元',
]
STOPS = ['别给妈妈转300元', '刚才那笔别转', '不转了',
         '莫给妈妈转300元', '唔好给妈妈转300元', '唔好畀媽媽轉300元',
         '不要再给妈妈转300元', '别再转300元给妈妈', '不用马上给妈妈转300元']


@pytest.mark.parametrize('text', AMBIGUOUS + STOPS)
def test_language_guard_is_pure_and_blocks_unclear_or_negative_transfer(text):
    context = {'pending_transfer': {'payee': '妈妈'}}
    before = copy.deepcopy(context)
    decision = inspect_language(text, context)
    assert decision is not None
    assert decision.kind == ('negated_transfer' if text in STOPS else 'ambiguous_amount')
    assert decision.clear_pending == (text in STOPS)
    assert decision.message and len(decision.message) <= 200
    assert decision.message.count('？') + decision.message.count('?') <= 1
    assert context == before


@pytest.mark.parametrize('mode', ['rules', 'model'])
@pytest.mark.parametrize('text', AMBIGUOUS + STOPS)
def test_both_planner_modes_block_before_requesting_model(monkeypatch, mode, text):
    monkeypatch.setenv('FF_PLANNER_MODE', mode)
    class NoRequest:
        def post(self, *args, **kwargs):
            pytest.fail('unclear transfer must be checked before any model request')
    actions = Planner(client=NoRequest()).plan(text, {'pending_transfer': {'payee': '妈妈'}})
    assert len(actions) == 1 and actions[0]['type'] == 'clarify'


@pytest.mark.parametrize('text,cents', [
    ('给妈妈转一千零二元', 100200), ('给妈妈转一千二百元', 120000),
    ('给妈妈转300.50元', 30050), ('给妈妈转三百五十元', 35000),
    ('给妈妈转十一元', 1100), ('给妈妈转300元', 30000),
    ('莫忘了给妈妈转300元', 30000),
])
def test_precise_amounts_keep_existing_value_and_confirmation(monkeypatch, text, cents):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    assert inspect_language(text, {}) is None
    assert Planner().plan(text, {}) == [{'type': 'transfer', 'payee': '妈妈', 'amount_cents': cents}]


@pytest.mark.parametrize('text',['别给我讲故事','别向我推销','不用给账单改备注',
                               '查几百元的支出','我几百块的费用怎么查'])
def test_non_transfer_negative_sentence_is_not_a_transfer_stop(text):
    assert inspect_language(text,{}) is None


@pytest.mark.parametrize('text',['给妈妈转300元，多谢','给妈妈转三百元，谢谢'])
def test_thanks_after_exact_amount_does_not_become_approximate_suffix(text):
    assert inspect_language(text,{}) is None


@pytest.mark.parametrize('text',['一千二','两百五','两百五块','给妈妈转一二元','给妈妈转五块八',
                               '两三百元','几百块','300来块'])
def test_unclear_bare_followup_or_unhandled_numeral_is_guarded(text):
    result=inspect_language(text,{'pending_transfer':{'payee':'妈妈'}})
    assert result is not None and result.kind=='ambiguous_amount'


def authenticated(tmp_path, monkeypatch, planner=None):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    client = TestClient(create_app(tmp_path/'language.db', planner=planner))
    client.__enter__()
    session = client.post('/api/session', json={'user': 'demo'}).json()
    client.headers['X-CSRF-Token'] = session['csrf']
    return client


@pytest.mark.parametrize('text', AMBIGUOUS + STOPS)
def test_api_guards_even_an_injected_planner_and_preserves_balances(tmp_path, monkeypatch, text):
    class NeverPlanner:
        mode = 'model'
        def plan(self, *args):
            pytest.fail('API must apply the server guard before invoking an injected planner')
    client = authenticated(tmp_path, monkeypatch, NeverPlanner())
    try:
        before = client.get('/api/state').json()
        response = client.post('/api/chat', json={'text': text, 'request_key': 'guarded'})
        assert response.status_code == 200
        after = client.get('/api/state').json()
        assert after['operations'] == []
        assert [a['balance_cents'] for a in after['accounts']] == [a['balance_cents'] for a in before['accounts']]
        expected = inspect_language(text, {}).message
        assert response.json()['message'] == expected
        assert client.post('/api/chat', json={'text': text, 'request_key': 'guarded'}).json() == response.json()
    finally:
        client.__exit__(None, None, None)


def test_negative_request_clears_only_transfer_working_slot(tmp_path, monkeypatch):
    client = authenticated(tmp_path, monkeypatch)
    try:
        def chat(text, key):
            return client.post('/api/chat', json={'text': text, 'request_key': key}).json()
        assert chat('给妈妈转账', 'start')['cards'] == []
        stopped = chat('别给妈妈转300元', 'stop')
        assert '已取消' not in stopped['message']
        assert client.get('/api/state').json()['operations'] == []
        assert chat('500元', 'number')['cards'] == []
        assert client.get('/api/state').json()['operations'] == []
    finally:
        client.__exit__(None, None, None)


def test_negative_request_does_not_claim_existing_operation_was_cancelled(tmp_path, monkeypatch):
    client = authenticated(tmp_path, monkeypatch)
    try:
        prepared = client.post('/api/chat', json={'text': '给妈妈转300元', 'request_key': 'prepare'}).json()
        operation = prepared['cards'][0]['operation']
        response = client.post('/api/chat', json={'text': '刚才那笔别转', 'request_key': 'stop'}).json()
        assert '已取消' not in response['message']
        assert client.get('/api/state').json()['operations'][0]['status'] == 'WAITING_CONFIRMATION'
        client.post('/api/chat', json={'text': '500元', 'request_key': 'number'})
        assert [o['id'] for o in client.get('/api/state').json()['operations']] == [operation['id']]
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize('text', ['查一下2024-01-05的转账记录', '查询1-3月的转账记录', '查1到5号的转账'])
def test_transfer_history_dates_are_not_payment_amounts(text):
    assert inspect_language(text, {}) is None


def test_history_query_cannot_exempt_a_separate_ambiguous_transfer():
    check=inspect_language('查询1-3月的转账记录，给妈妈转300到500元', {})
    assert check is not None and check.kind=='ambiguous_amount'


@pytest.mark.parametrize('text', ['给妈妈转300多一点', '给妈妈转三百多点', '给妈妈转300多些', '300多点', '约300元'])
def test_loose_amount_suffix_and_approximate_pending_slot_require_exact_amount(text):
    check = inspect_language(text, {'pending_transfer': {'payee': '妈妈'}})
    assert check is not None and check.kind == 'ambiguous_amount'
    assert check.clear_pending is False


def test_wu_negative_is_blocked_before_injected_planner(tmp_path, monkeypatch):
    class NeverPlanner:
        mode = 'model'
        def plan(self, *args):
            pytest.fail('explicit negative must not reach any planner')
    client = authenticated(tmp_path, monkeypatch, NeverPlanner())
    try:
        before = client.get('/api/state').json()
        result = client.post('/api/chat', json={'text': '请勿转账300元', 'request_key': 'wu-stop'})
        assert result.status_code == 200
        assert result.json()['cards'] == []
        after = client.get('/api/state').json()
        assert after['operations'] == []
        assert after['accounts'] == before['accounts']
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize('text', ['不转账就要逾期了，马上转给房东3000元', '要不转给妈妈300元吧', '转不转给他？就转300吧',
                                  '要不要给妈妈转300元？', '要不要转账'])
def test_mixed_transfer_intent_clarifies_without_clearing_pending(text, monkeypatch):
    check = inspect_language(text, {'pending_transfer': {'payee': '妈妈'}})
    assert check is not None and check.kind == 'clarify_transfer_intent'
    assert check.clear_pending is False
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    assert Planner().plan(text, {'pending_transfer': {'payee': '妈妈'}})[0]['type'] == 'clarify'


@pytest.mark.parametrize('text', ['给妈妈转１２３．４５元', '给妈妈转123．45元', '给妈妈转１２３.４５元'])
def test_fullwidth_decimal_keeps_whole_exact_value_in_rules_and_api(text, monkeypatch, tmp_path):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    assert Planner().plan(text, {}) == [{'type': 'transfer', 'payee': '妈妈', 'amount_cents': 12345}]
    client = authenticated(tmp_path, monkeypatch)
    try:
        result = client.post('/api/chat', json={'text': text, 'request_key': 'fullwidth-value'})
        assert result.status_code == 200
        assert result.json()['cards'][0]['operation']['payload']['amount_cents'] == 12345
        state = client.get('/api/state').json()
        assert len(state['operations']) == 1
        assert state['operations'][0]['status'] == 'WAITING_CONFIRMATION'
        history=client.get('/api/history').json()['messages']
        assert any(item['role']=='user' and item['content']==text for item in history)
    finally:
        client.__exit__(None, None, None)


def test_question_preserves_pending_transfer_and_replay_does_not_prepare_again(tmp_path, monkeypatch):
    client = authenticated(tmp_path, monkeypatch)
    try:
        def state():
            response = client.get('/api/state')
            assert response.status_code == 200
            return response.json()

        def chat(text, key):
            response = client.post(
                '/api/chat', json={'text': text, 'request_key': key}
            )
            assert response.status_code == 200
            return response.json()

        def balances(snapshot):
            return [account['balance_cents'] for account in snapshot['accounts']]

        before = state()
        assert before['operations'] == []
        initial_balances = balances(before)

        started = chat('给妈妈转账', 'pending-question-start')
        assert started['cards'] == []
        pending = state()
        assert pending['operations'] == []
        assert balances(pending) == initial_balances

        question = '要不要转账'
        decision = inspect_language(
            question, {'pending_transfer': {'payee': '妈妈'}}
        )
        assert decision is not None
        assert decision.kind == 'clarify_transfer_intent'
        assert decision.clear_pending is False

        clarified = chat(question, 'pending-question-clarify')
        assert clarified['cards'] == []
        assert clarified['message'] == decision.message
        after_question = state()
        assert after_question['operations'] == []
        assert balances(after_question) == initial_balances

        prepared = chat('500元', 'pending-question-amount')
        assert len(prepared['cards']) == 1
        operation = prepared['cards'][0]['operation']
        assert operation['type'] == 'transfer'
        assert operation['payload']['amount_cents'] == 50000
        assert operation['payload']['payee_id'] == 'p_mom'
        assert operation['payload']['payee_name'] == '妈妈'
        assert operation['status'] == 'WAITING_CONFIRMATION'

        after_amount = state()
        assert len(after_amount['operations']) == 1
        stored = after_amount['operations'][0]
        assert stored['id'] == operation['id']
        assert stored['status'] == 'WAITING_CONFIRMATION'
        assert balances(after_amount) == initial_balances

        replayed = chat(question, 'pending-question-clarify')
        assert replayed == clarified
        assert replayed['cards'] == []
        after_replay = state()
        assert after_replay['operations'] == after_amount['operations']
        assert balances(after_replay) == initial_balances
    finally:
        client.__exit__(None, None, None)
