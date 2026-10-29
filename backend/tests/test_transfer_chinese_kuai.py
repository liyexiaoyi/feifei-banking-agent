"""Precise Chinese integer + 元/块/块钱 transfers; vague amounts still clarify."""
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.planner import Planner


def acts(result):
    return result['actions'] if isinstance(result, dict) else result


@pytest.mark.parametrize('text,cents', [
    ('给妈妈转三百块', 30000),
    ('给妈妈转三百块钱', 30000),
    ('给妈妈转三百元', 30000),
])
def test_precise_chinese_amount_transfers(monkeypatch, text, cents):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    a = acts(Planner()._rules(text, {}))
    assert a == [{'type': 'transfer', 'payee': '妈妈', 'amount_cents': cents}]


@pytest.mark.parametrize('text', [
    '给妈妈转三百五', '给妈妈转五六百', '给妈妈转三百多', '给妈妈转大概三百', '给妈妈转三块五',
])
def test_vague_chinese_amount_clarifies(monkeypatch, text):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    a = acts(Planner()._rules(text, {}))
    assert all(x['type'] != 'transfer' for x in a)
    assert a and a[0]['type'] == 'clarify'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path / 'kuai.db')
    app.state.bank.clock = lambda: datetime.fromisoformat('2026-10-28T12:00:00+08:00')
    with TestClient(app) as c:
        z = c.post('/api/session', json={'user': 'demo'})
        assert z.status_code == 200
        c.headers['X-CSRF-Token'] = z.json()['csrf']
        yield c


def bal(s):
    return {a['id']: a['balance_cents'] for a in s['accounts']}


def reserved(s):
    return sum(a.get('reserved_cents', 0) for a in s['accounts'])


def transfers(c):
    s = c.get('/api/state').json()
    return s, [o for o in s['operations'] if o['type'] == 'transfer']


def test_kuai_transfer_waits_then_replacement(client):
    before, _ = transfers(client)
    z = client.post('/api/chat', json={'text': '给妈妈转三百块', 'request_key': 'kuai-1'})
    assert z.status_code == 200, z.text
    s, ops = transfers(client)
    waiting = [o for o in ops if o['status'] == 'WAITING_CONFIRMATION']
    assert len(waiting) == 1 and waiting[0]['payload']['amount_cents'] == 30000
    old_id = waiting[0]['id']
    assert bal(s) == bal(before)
    assert reserved(s) - reserved(before) == 30000

    z = client.post('/api/chat', json={'text': '改成两百块', 'request_key': 'kuai-2'})
    assert z.status_code == 200, z.text
    s, ops = transfers(client)
    waiting = [o for o in ops if o['status'] == 'WAITING_CONFIRMATION']
    assert len(waiting) == 1 and waiting[0]['payload']['amount_cents'] == 20000
    assert waiting[0]['id'] != old_id
    old = next(o for o in ops if o['id'] == old_id)
    assert old['status'] == 'CANCELLED'
    assert waiting[0]['status'] == 'WAITING_CONFIRMATION'
    assert bal(s) == bal(before)
    assert reserved(s) - reserved(before) == 20000


def test_unsafe_kuai_forms_never_transfer(client):
    _, start = transfers(client)
    for i, t in enumerate(['给妈妈转三百块美元', '给妈妈转三百块，再加两百块', '给妈妈转三百块或五百块',
                           '给妈妈转负三百块', '给妈妈转百百块']):
        z = client.post('/api/chat', json={'text': t, 'request_key': f'kuai-bad-{i}'})
        assert z.status_code == 200, z.text
        _, ops = transfers(client)
        assert len(ops) == len(start), t
