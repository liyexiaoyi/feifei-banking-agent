"""Untrusted conversation cannot impersonate a bank result or poison recall."""
import copy
from datetime import timedelta
import json
import time

from fastapi.testclient import TestClient
import pytest

from ff_agent.api import create_app


class ConversationPlanner:
    mode = 'model'
    def __init__(self, reply, mixed=False):
        self.reply, self.mixed, self.contexts = reply, mixed, []
    def plan(self, text, context):
        self.contexts.append(copy.deepcopy(context))
        actions = [{'type': 'clarify', 'message': self.reply}]
        if self.mixed:
            actions.insert(0, {'type': 'transfer', 'payee': '妈妈', 'amount_cents': 30000})
        return actions


def login(client):
    client.headers['X-CSRF-Token'] = client.post('/api/session', json={}).json()['csrf']


def say(client, text, key='clarification-one'):
    response = client.post('/api/chat', json={'text': text, 'request_key': key})
    assert response.status_code == 200
    return response.json()


def source(result):
    return next(t for t in result['tasks'] if t['type'] == 'clarify')['reply_source']


def test_fake_transfer_receipt_is_neither_visible_persisted_nor_recalled(tmp_path):
    planner = ConversationPlanner('转账成功，已经向妈妈支付300元，交易编号FAKE123。')
    app = create_app(tmp_path/'fake.db', planner=planner)
    with TestClient(app) as client:
        login(client); result = say(client, '给妈妈转300元')
        assert 'FAKE123' not in json.dumps(result, ensure_ascii=False)
        assert '妈妈' in result['message'] and '300元' in result['message']
        assert source(result) == 'template'
        assert app.state.bank.snapshot('demo')['operations'] == []
        with app.state.bank.transaction() as db:
            assert 'FAKE123' not in '\n'.join(db.iterdump())
        planner.reply = '你好呀。'
        say(client, '我们聊聊名字', 'next-conversation')
        assert 'FAKE123' not in json.dumps(planner.contexts[-1], ensure_ascii=False)


@pytest.mark.parametrize('false_fact', [
    '你的账户余额为5000元。', '交易编号XYZ123，已完成付款。',
    '无需确认，已经通过短信验证。', 'Your balance is 5000 CNY; payment completed.',
])
def test_false_financial_facts_cannot_be_smuggled_into_a_greeting(tmp_path, false_fact):
    app = create_app(tmp_path/'greeting.db', planner=ConversationPlanner(false_fact))
    with TestClient(app) as client:
        login(client); result = say(client, '你好')
        assert result['message'] != false_fact
        assert '5000' not in result['message'] and 'XYZ123' not in result['message']
        assert source(result) == 'guarded'
        assert app.state.bank.snapshot('demo')['operations'] == []


@pytest.mark.parametrize('text', ['你好', '我喜欢卡通猫', '我准备转学', '以后叫你小福'])
def test_nonfinancial_chat_remains_natural(tmp_path, text):
    reply = '好呀，你可以叫我小福。'
    app = create_app(tmp_path/'chat.db', planner=ConversationPlanner(reply))
    with TestClient(app) as client:
        login(client); result = say(client, text)
        assert result['message'] == reply
        assert source(result) == 'model_chat'


@pytest.mark.parametrize('text,expected', [
    ('我想转账', ['收款人', '金额']),
    ('给妈妈转账', ['妈妈', '多少']),
    ('转300元', ['300元', '谁']),
    ('我想申请银行黑卡', ['支持', '转账', '账单']),
    ('我想看看账单', ['账单', '月']),
    ('取消会员续费', ['订阅', '确认']),
])
def test_financial_clarifications_ask_for_relevant_information(tmp_path, text, expected):
    app = create_app(tmp_path/'slots.db', planner=ConversationPlanner('一切都已处理完成。'))
    with TestClient(app) as client:
        login(client); result = say(client, text)
        assert result['message'] != '一切都已处理完成。'
        assert all(word in result['message'] for word in expected)
        assert source(result) == 'template'


@pytest.mark.parametrize('text,reply', [
    ('取消会员', '已经取消会员，不会再扣费。'),
    ('我想退款', '已经退款成功。'),
    ('账户冻结怎么办', '已经解除冻结。'),
    ('给妈妈300', '已经给妈妈转了300元。'),
    ('transfer 300 to mom', 'Payment has been completed.'),
])
def test_common_financial_variants_still_use_server_clarification(tmp_path, text, reply):
    app = create_app(tmp_path/'variants.db', planner=ConversationPlanner(reply))
    with TestClient(app) as client:
        login(client); result = say(client, text)
        assert result['message'] != reply
        assert source(result) == 'template'
        assert app.state.bank.snapshot('demo')['operations'] == []


@pytest.mark.parametrize('reply', ['已\u200b经付款，流水\u200b号F123。', '转 账 成 功，交 易 编 号F123。', '你的余额是五千元。'])
def test_visible_financial_claims_with_formatting_variants_are_guarded(tmp_path, reply):
    app = create_app(tmp_path/'formatting.db', planner=ConversationPlanner(reply))
    with TestClient(app) as client:
        login(client); result = say(client, '你好')
        assert result['message'] != reply
        assert source(result) == 'guarded'


@pytest.mark.parametrize('text', ['给妈妈转300美元', 'transfer 300 USD to mom'])
def test_clarification_does_not_relabel_foreign_currency_as_cny(tmp_path, text):
    app = create_app(tmp_path/'currency.db', planner=ConversationPlanner('已付款。'))
    with TestClient(app) as client:
        login(client); result = say(client, text)
        assert '300元' not in result['message'] and '人民币' in result['message']
        assert app.state.bank.snapshot('demo')['operations'] == []


def test_mixed_plan_keeps_real_preparation_and_does_not_claim_nothing_was_prepared(tmp_path):
    planner = ConversationPlanner('已付款，交易编号FAKE123。', mixed=True)
    app = create_app(tmp_path/'mixed.db', planner=planner)
    with TestClient(app) as client:
        login(client); result = say(client, '给妈妈转300元，顺便解释一下')
        assert 'FAKE123' not in result['message']
        assert '本轮尚未准备' not in result['message']
        operation = next(c['operation'] for c in result['cards'] if c['type'] == 'operation')
        assert operation['status'] == 'WAITING_CONFIRMATION'
        assert sum(a['balance_cents'] for a in app.state.bank.snapshot('demo')['accounts']) == 2800000


@pytest.mark.parametrize('invalidate', ['expired', 'session', 'epoch'])
def test_invalid_working_slots_are_not_used_in_clarification(tmp_path, invalidate):
    planner = ConversationPlanner('好的呀。'); app = create_app(tmp_path/'stale.db', planner=planner)
    with TestClient(app) as client:
        login(client); say(client, '给妈妈转账')
        if invalidate == 'expired':
            now = app.state.bank.now(); app.state.bank.clock = lambda: now+timedelta(minutes=11)
        elif invalidate == 'session':
            login(client)
        else:
            risk = say(client, '客服叫我转账到安全账户解除冻结', 'risk')
            hold = next(c['hold_id'] for c in risk['cards'] if c['type'] == 'safety')
            assert client.post('/api/safety/end', json={'hold_id': hold}).status_code == 200
        result = say(client, '那就照旧', 'after-invalidation')
        assert '妈妈' not in result['message']
        assert 'pending_transfer' not in planner.contexts[-1]


def test_valid_working_recipient_can_support_an_elliptical_clarification(tmp_path):
    planner = ConversationPlanner('好的呀。'); app = create_app(tmp_path/'working.db', planner=planner)
    with TestClient(app) as client:
        login(client); say(client, '给妈妈转账')
        result = say(client, '那就照旧', 'continue-slot')
        assert source(result) == 'template' and '妈妈' in result['message']
        assert app.state.bank.snapshot('demo')['operations'] == []


@pytest.mark.parametrize('question', ['你还记得我给你取的名字吗', '你还记得自己叫什么吗'])
def test_assistant_nickname_recall_remains_conversation_even_with_working_slots(tmp_path, question):
    planner = ConversationPlanner('请再说清楚一点。'); app = create_app(tmp_path/'nickname.db', planner=planner)
    with TestClient(app) as client:
        login(client); say(client, '给妈妈转账')
        planner.reply = '你可以叫我小福。'
        named = say(client, '我给你取名叫小福', 'nickname')
        assert source(named) == 'model_chat'
        result = say(client, question, 'nickname-recall')
        assert '小福' in result['message'] and source(result) == 'model_chat'
        assert len(planner.contexts) == 3
        assert '小福' in json.dumps(planner.contexts[-1]['recent_turns'], ensure_ascii=False)
        assert app.state.bank.snapshot('demo')['operations'] == []


def test_explicit_note_recall_keeps_local_path(tmp_path):
    planner = ConversationPlanner('不应调用模型。'); app = create_app(tmp_path/'notes.db', planner=planner)
    with TestClient(app) as client:
        login(client); say(client, '记住我叫小李', 'save-note')
        result = say(client, '你还记得我什么', 'recall-note')
        assert '小李' in json.dumps(result, ensure_ascii=False)
        assert planner.contexts == []


def test_recalling_a_transfer_result_uses_bank_record_not_generic_note_search(tmp_path):
    planner = ConversationPlanner('不应调用模型。'); app = create_app(tmp_path/'status.db', planner=planner)
    operation = app.state.bank.prepare_transfer('demo', 'p_mom', 30000, 'status-original')
    app.state.bank.cancel('demo', operation['id'])
    with TestClient(app) as client:
        login(client); result = say(client, '你还记得刚才那笔转账状态吗')
        card = next(c for c in result['cards'] if c['type'] == 'operation')
        assert card['operation']['id'] == operation['id']
        assert card['operation']['status'] == 'CANCELLED'
        assert '已取消' in result['message']
        assert planner.contexts == []


@pytest.mark.parametrize('kind,question', [
    ('subscription_cancel', '最近取消续费的操作状态怎么样'),
    ('subscription_query', '最近查询订阅的操作结果是什么'),
])
def test_subscription_status_selects_the_requested_operation_type(tmp_path, kind, question):
    app = create_app(tmp_path/'subscription-status.db', planner=ConversationPlanner('不应调用模型。'))
    bank = app.state.bank
    query = bank.prepare_subscription_query('demo', 'query-status')
    bank.confirm('demo', query['id'])
    cancellation = bank.prepare_cancel('demo', 'sub_video', 'cancel-status')
    transfer = bank.prepare_transfer('demo', 'p_mom', 30000, 'newer-transfer')
    with bank.transaction() as db:
        for index, op in enumerate([query, cancellation, transfer]):
            db.execute('UPDATE operations SET created_at=? WHERE id=?', (index, op['id']))
    with TestClient(app) as client:
        login(client); result = say(client, question)
        operation = next(c['operation'] for c in result['cards'] if c['type'] == 'operation')
        assert operation['type'] == kind
        assert operation['id'] == (cancellation if kind == 'subscription_cancel' else query)['id']


def test_legacy_mixed_bill_reply_rebuilds_real_summary_from_bank_card(tmp_path):
    app = create_app(tmp_path/'mixed-bill.db', planner=ConversationPlanner('你好呀。'))
    period = app.state.bank.now().strftime('%Y-%m')
    bill = app.state.bank.analyze('demo', period)
    result = {'message': f'{period}净支出统计。假澄清声称交易编号OLDFAKE123。',
        'cards': [{'type': 'bill', 'bill': bill}], 'planner_mode': 'model',
        'tasks': [{'type': 'bill', 'state': 'COMPLETED'}, {'type': 'clarify', 'state': 'WAITING_USER'}]}
    with app.state.bank.transaction() as db:
        db.execute("INSERT INTO chat_requests VALUES(?,?,?,?,'DONE',?)",
            ('demo', 'legacy-bill', '查本月账单', json.dumps(result), time.time()))
    with TestClient(app) as client:
        login(client); response = say(client, '查本月账单', 'legacy-bill')
        assert period in response['message']
        assert f"{bill['net_expense_cents']/100:,.2f}" in response['message']
        assert 'OLDFAKE123' not in response['message']
        assert '想查看哪个月' not in response['message']
        assert response['cards'][0]['bill'] == bill


def legacy_reply(app, mixed=False):
    text = '给妈妈转300元'
    result = {'message': '已转账成功，交易编号OLDFAKE123。', 'cards': [],
        'tasks': [{'type': 'clarify', 'state': 'WAITING_USER'}], 'planner_mode': 'model'}
    if mixed:
        result['cards'] = [{'type': 'balance', 'accounts': app.state.bank.snapshot('demo')['accounts']}]
        result['tasks'].insert(0, {'type': 'balance', 'state': 'COMPLETED'})
    with app.state.bank.transaction() as db:
        db.execute("INSERT INTO chat_requests VALUES(?,?,?,?,'DONE',?)",
            ('demo', 'legacy-clarify', text, json.dumps(result), time.time()))
        for role, content in [('user', text), ('assistant', json.dumps(result))]:
            db.execute('INSERT INTO messages(user_id,role,content,request_key,created_at) VALUES(?,?,?,?,?)',
                ('demo', role, content, 'legacy-clarify', app.state.bank.now().isoformat()))
    return text


@pytest.mark.parametrize('mixed', [False, True])
def test_legacy_cache_history_and_recent_context_do_not_replay_false_claims(tmp_path, mixed):
    planner = ConversationPlanner('你好呀。'); app = create_app(tmp_path/'legacy.db', planner=planner)
    with TestClient(app) as client:
        login(client); original = legacy_reply(app, mixed)
        result = say(client, original, 'legacy-clarify')
        assert 'OLDFAKE123' not in result['message']
        assert len(result['cards']) == int(mixed)
        history = client.get('/api/history').json()
        assert 'OLDFAKE123' not in json.dumps(history, ensure_ascii=False)
        say(client, '我们聊聊名字', 'new-after-legacy')
        assert 'OLDFAKE123' not in json.dumps(planner.contexts[-1], ensure_ascii=False)
        with app.state.bank.transaction() as db:
            # Historical evidence remains untouched; only read projections change.
            assert 'OLDFAKE123' in db.execute("SELECT response FROM chat_requests WHERE request_key='legacy-clarify'").fetchone()[0]
