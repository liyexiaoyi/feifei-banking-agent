"""Independent reproductions from the current-source review; no real services."""
import uuid
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.bank import DomainError
from ff_agent.clarification import render_clarify, sanitize_legacy_reply
from ff_agent.planner import Planner


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path/'current-audit.db')
    with TestClient(app) as c:
        c.headers['X-CSRF-Token'] = c.post('/api/session', json={'user': 'demo'}).json()['csrf']
        yield app, c


def query(client):
    z = client[1].post('/api/chat', json={'text': '取消流光视频的自动续费', 'request_key': uuid.uuid4().hex})
    assert z.status_code == 200
    op = z.json()['cards'][0]['operation']
    assert op['type'] == 'subscription_query'
    return op


def facts(bank):
    with bank.transaction() as db:
        tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: [tuple(x) for x in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in ('accounts', 'operations', 'transactions', 'tool_calls') if name in tables}


def test_successful_query_replay_cannot_retry_a_failed_continuation(client, monkeypatch):
    app, c = client
    op = query(client)
    def fail(user):
        raise DomainError('INJECTED_READ_FAULT', '测试只读失败', 503)
    with monkeypatch.context() as m:
        m.setattr(app.state.bank, 'subscriptions', fail)
        first = c.post('/api/operations/'+op['id']+'/confirm', json={})
    assert first.status_code == 200 and first.json()['status'] == 'SUCCEEDED'
    assert 'next_operation' not in first.json()
    before = facts(app.state.bank)
    replay = c.post('/api/operations/'+op['id']+'/confirm', json={})
    assert replay.status_code == 200 and replay.json()['status'] == 'SUCCEEDED'
    assert facts(app.state.bank) == before
    assert 'next_operation' not in replay.json()


@pytest.mark.parametrize('cancel_child', [False, True])
def test_successful_query_replay_only_reads_original_child(client, cancel_child):
    app, c = client
    op = query(client)
    first = c.post('/api/operations/'+op['id']+'/confirm', json={})
    assert first.status_code == 200
    child = first.json()['next_operation']
    if cancel_child:
        assert c.post('/api/operations/'+child['id']+'/cancel', json={}).status_code == 200
    before = facts(app.state.bank)
    replay = c.post('/api/operations/'+op['id']+'/confirm', json={})
    assert replay.status_code == 200
    assert replay.json()['next_operation']['id'] == child['id']
    assert replay.json()['next_operation']['status'] == ('CANCELLED' if cancel_child else 'WAITING_CONFIRMATION')
    assert facts(app.state.bank) == before


@pytest.mark.parametrize('observed', ['EXPIRED', 'UNKNOWN'])
def test_query_replay_preserves_child_observation_without_bookkeeping(client, observed):
    app, c = client
    parent = query(client)
    child = c.post('/api/operations/'+parent['id']+'/confirm', json={}).json()['next_operation']
    if observed == 'EXPIRED':
        now = app.state.bank.now()
        app.state.bank.clock = lambda: now+timedelta(seconds=1900)
    else:
        with app.state.bank.transaction() as db:
            db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (child['id'],))
    before = facts(app.state.bank)
    replay = c.post('/api/operations/'+parent['id']+'/confirm', json={})
    assert replay.status_code == 200
    assert replay.json()['next_operation']['id'] == child['id']
    assert replay.json()['next_operation']['status'] == observed
    assert facts(app.state.bank) == before


@pytest.mark.parametrize('message', [
    '好的，已向妈妈转出100元。', '您的账户还有¥5000可用',
    '我已经向爸爸汇出1000元。', '账户里还剩五千元。',
    '我已经给妈妈转了100块。', '已向妈妈转账100', '您账户里还剩5000可用',
    '您账户里还剩五千可用', '我已经给妈妈转了三百',
    '已经给妈妈转账了3000', '已经给妈妈转账了三千',
])
def test_model_smalltalk_cannot_assert_new_money_facts(message):
    text, kind = render_clarify(message, '你好', {}, ())
    assert kind != 'model_chat'
    assert text != message


@pytest.mark.parametrize('message', ['你好，祝你元旦快乐。', '欢迎回来，请告诉菲菲你想办什么。',
                                     '他向左转了三圈。', '给自己转了三个圈。',
                                     '我可以帮你转账和查询余额，请告诉菲菲你的需求。'])
def test_harmless_greeting_still_uses_model_chat(message):
    assert render_clarify(message, '你好', {}, ()) == (message, 'model_chat')


@pytest.mark.parametrize('source', ['model_chat', 'rules_chat'])
@pytest.mark.parametrize('message', ['您的账户还有¥5000可用', '您账户里还剩5000可用', '我已经给妈妈转了100块。'])
def test_cached_chat_rechecks_new_untrusted_bank_fact_variants(source, message):
    value = {'message': message, 'cards': [],
             'tasks': [{'type': 'clarify', 'reply_source': source}]}
    cleaned = sanitize_legacy_reply(value, '你好')
    assert cleaned['message'] != value['message']
    assert cleaned['tasks'][0]['reply_source'] == 'guarded'
    assert value['message'] == message


@pytest.mark.parametrize('text,expected', [
    ('给妈妈转3千元', 300000), ('给妈妈转1万元', 1000000),
    ('给爸爸转5百块', 50000), ('给妈妈转1.5万元', 1500000),
    ('给妈妈转0.5千元', 50000),
])
def test_exact_arabic_chinese_unit_amount_is_not_silently_truncated(client, text, expected):
    plan = Planner().plan(text, {})
    assert [x.get('amount_cents') for x in plan if x['type'] == 'transfer'] == [expected]
    app, c = client
    before = facts(app.state.bank)
    z = c.post('/api/chat', json={'text': text, 'request_key': uuid.uuid4().hex})
    assert z.status_code == 200
    operations = [x['operation'] for x in z.json()['cards'] if x.get('type') == 'operation']
    assert len(operations) == 1 and operations[0]['payload']['amount_cents'] == expected
    assert operations[0]['status'] == 'WAITING_CONFIRMATION'
    after = facts(app.state.bank)
    assert after['accounts'] == before['accounts'] and after['transactions'] == before['transactions']


@pytest.mark.parametrize('text', ['给妈妈转3千2元', '给妈妈转三千二元', '给妈妈转五五元', '给妈妈转两万五元'])
def test_ambiguous_or_multiple_scale_amount_still_needs_clarification(text):
    assert all(x['type'] != 'transfer' for x in Planner().plan(text, {}))


def test_flower_requote_after_cake_selection_resets_choice_kind(tmp_path, monkeypatch):
    from test_conversation_birthday import make, talk, pay, cont, TEXT
    import ff_agent.conversation_goal_loop as module
    e = make(tmp_path)
    talk(e, TEXT.replace('预算500', '预算1000').replace('只要花不要蛋糕', '也要蛋糕'), 'audit-birthday-start')
    chosen = talk(e, '第一个', 'audit-flower')
    op = next(x['operation'] for x in chosen['cards'] if x.get('type') == 'operation')
    pay(e, op)
    cont(e, chosen['goal'], 'audit-cake-options')
    selected = talk(e, '第一个', 'audit-cake')
    original = module.catalog_choices
    monkeypatch.setattr(module, 'catalog_choices', lambda bank, budget: [
        {**x, 'quote_version': x['quote_version']+1} for x in original(bank, budget)])
    rechoose = cont(e, selected['goal'], 'audit-flower-requote')
    assert rechoose['goal']['phase'] == 'CHOOSING'
    before = facts(e[0])
    talk(e, '第一个', 'audit-rechoose-flower')
    stored = e[1].get('demo', 'room-a', '', selected['goal']['id'])
    assert stored['observations'].get('choices_gift') != 'cake'
    assert stored['observations']['chosen']['quote_version'] == 2
    assert not stored['observations'].get('chosen_cake')
    assert facts(e[0]) == before


def test_real_catalog_requote_refreshes_source_selection_before_order_prepare(tmp_path, monkeypatch):
    from copy import deepcopy
    from test_conversation_birthday import make, talk, pay, cont, TEXT
    import ff_agent.birthday_orders as orders
    e = make(tmp_path)
    talk(e, TEXT.replace('预算500', '预算1000').replace('只要花不要蛋糕', '也要蛋糕'), 'real-requote-start')
    chosen = talk(e, '第一个', 'real-first-flower')
    pay(e, next(x['operation'] for x in chosen['cards'] if x.get('type') == 'operation'))
    cont(e, chosen['goal'], 'real-initial-cake-options')
    selected = talk(e, '第一个', 'real-initial-cake')
    before = facts(e[0])
    catalog = deepcopy(orders.CATALOG)
    catalog['flowers_simple'].update(quote_version=2, price_cents=32000)
    monkeypatch.setattr(orders, 'CATALOG', catalog)
    rechoose = cont(e, selected['goal'], 'real-flower-price-change')
    assert rechoose['goal']['phase'] == 'CHOOSING'
    new_flower = talk(e, '第一个', 'real-new-flower')
    cake_options = cont(e, new_flower['goal'], 'real-reprepare-cake-options')
    assert cake_options['goal']['phase'] == 'CHOOSING'
    new_cake = talk(e, '第一个', 'real-new-cake')
    prepared = cont(e, new_cake['goal'], 'real-prepare-repriced-orders')
    assert prepared['goal']['phase'] == 'WAITING_CONFIRMATION'
    with e[0].transaction() as db:
        rows = db.execute("SELECT payload,status FROM operations WHERE type='birthday_order'").fetchall()
        assert db.execute('SELECT count(*) FROM birthday_order_ledger').fetchone()[0] == 0
    import json
    payloads = [json.loads(row['payload']) for row in rows]
    assert sorted(x['amount_cents'] for x in payloads) == [20000, 32000]
    flower = next(x for x in payloads if x['gift'] == 'flowers')
    assert flower['quote_version'] == 2
    assert all(row['status'] == 'WAITING_CONFIRMATION' for row in rows)
    after = facts(e[0])
    assert after['accounts'] == before['accounts'] and after['transactions'] == before['transactions']


def test_cake_quote_change_requires_new_choice_before_preparation(tmp_path, monkeypatch):
    from copy import deepcopy
    from test_conversation_birthday import make, talk, pay, cont, TEXT
    import ff_agent.birthday_orders as orders
    e = make(tmp_path)
    talk(e, TEXT.replace('预算500', '预算1000').replace('只要花不要蛋糕', '也要蛋糕'), 'cake-requote-start')
    chosen = talk(e, '第一个', 'cake-requote-flower')
    pay(e, next(x['operation'] for x in chosen['cards'] if x.get('type') == 'operation'))
    cont(e, chosen['goal'], 'cake-requote-options')
    selected = talk(e, '第一个', 'cake-requote-pick')
    before = facts(e[0])
    catalog = deepcopy(orders.CATALOG)
    catalog['cake_small'].update(quote_version=2, price_cents=25000)
    monkeypatch.setattr(orders, 'CATALOG', catalog)
    rechoose = cont(e, selected['goal'], 'cake-requote-detect')
    assert rechoose['goal']['phase'] == 'CHOOSING'
    assert any(x['id'] == 'cake_small' and x['price_cents'] == 25000 for x in rechoose['goal']['choices'])
    assert facts(e[0]) == before
    fresh = talk(e, '第一个', 'cake-requote-new-pick')
    prepared = cont(e, fresh['goal'], 'cake-requote-new-orders')
    assert prepared['goal']['phase'] == 'WAITING_CONFIRMATION'
    import json
    with e[0].transaction() as db:
        rows = db.execute("SELECT payload FROM operations WHERE type='birthday_order'").fetchall()
        assert db.execute('SELECT count(*) FROM birthday_order_ledger').fetchone()[0] == 0
    payloads = [json.loads(x['payload']) for x in rows]
    assert sorted(x['amount_cents'] for x in payloads) == [25000, 30000]
    cake = next(x for x in payloads if x['gift'] == 'cake')
    assert cake['quote_version'] == 2
    after = facts(e[0])
    assert after['accounts'] == before['accounts'] and after['transactions'] == before['transactions']
