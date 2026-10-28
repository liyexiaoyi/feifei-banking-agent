"""Chat removal resolves explicit IDs and full-set ambiguity without authorizing money."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.bank import Bank, DomainError, TZ
from ff_agent.memory import Memory
from ff_agent import memory as memory_module
from ff_agent.planner import Planner


@pytest.fixture
def notes(tmp_path):
    bank = Bank(tmp_path / 'memory-resolution.db', clock=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    return Memory(bank), bank


def removed_count(bank):
    with bank.transaction() as db:
        return db.execute("SELECT COUNT(*) FROM audit WHERE event='MEMORY_REMOVED'").fetchone()[0]


@pytest.fixture
def non_secret_note_ids(monkeypatch):
    # Quoted IDs are note contents in these fixtures. A random UUID can contain
    # a phone/card-shaped digit run, which the unchanged privacy rule rejects.
    ids=iter(('a'*32,'b'*32,'c'*32))
    monkeypatch.setattr(memory_module,'uuid',SimpleNamespace(uuid4=lambda:SimpleNamespace(hex=next(ids))))


@pytest.mark.parametrize('verb', ['忘记 ', '忘掉：', '移出记忆 '])
def test_exact_own_note_id_removes_without_content_recall(notes, verb):
    memory, bank = notes
    note = memory.remember('demo', '回复用简短中文', 'save')
    before = bank.snapshot('demo')
    reply = memory.handle('demo', verb + note['id'], 'remove')
    assert reply['tasks'][0]['state'] == 'COMPLETED'
    assert memory.count('demo') == 0
    assert removed_count(bank) == 1
    assert bank.snapshot('demo')['accounts'] == before['accounts']


def test_exact_id_does_not_remove_a_different_note_mentioning_it(notes, non_secret_note_ids):
    memory, _ = notes
    target = memory.remember('demo', '回答简短', 'save-target')
    other = memory.remember('demo', '我的参考编号是 ' + target['id'], 'save-mention')
    memory.handle('demo', '忘记 ' + target['id'], 'remove')
    assert memory.count('demo') == 1
    assert memory.get('demo', other['id'])['content'].startswith('我的参考编号')
    with pytest.raises(DomainError):
        memory.get('demo', target['id'])


@pytest.mark.parametrize('query', ['mem_' + 'f' * 32, 'mem_not_a_real_identifier'])
def test_missing_id_shaped_query_never_falls_back_to_content(notes, query):
    memory, bank = notes
    note = memory.remember('demo', '备注编号 ' + query, 'save')
    memory.handle('demo', '忘记 ' + query, 'remove')
    assert memory.get('demo', note['id'])['content'] == note['content']
    assert removed_count(bank) == 0


def test_foreign_id_and_unknown_id_have_same_nonrevealing_result(notes, non_secret_note_ids):
    memory, bank = notes
    foreign = memory.remember('guest', '回复正式一些', 'guest-note')
    own = memory.remember('demo', '参考编号 ' + foreign['id'], 'own-note')
    foreign_reply = memory.handle('demo', '忘记 ' + foreign['id'], 'foreign')
    missing_reply = memory.handle('demo', '忘记 mem_' + 'f' * 32, 'missing')
    assert foreign_reply == missing_reply
    assert memory.get('demo', own['id'])['content'] == own['content']
    assert memory.get('guest', foreign['id'])['content'] == foreign['content']
    assert foreign['content'] not in str(foreign_reply)
    assert removed_count(bank) == 0


def test_already_removed_id_replay_and_restart_cannot_restore_note(notes):
    memory, bank = notes
    note = memory.remember('demo', '账单按周查看', 'save')
    memory.handle('demo', '忘记 ' + note['id'], 'first')
    again = Memory(Bank(bank.path, clock=bank.clock))
    reply = again.handle('demo', '忘记 ' + note['id'], 'again')
    assert note['content'] not in str(reply)
    assert again.count('demo') == 0
    assert removed_count(bank) == 1


def test_short_content_fragment_can_match_without_recall_tokens(notes):
    memory, _ = notes
    memory.remember('demo', '我喜欢喝茶', 'save')
    assert memory.recall('demo', '茶') == []
    reply = memory.handle('demo', '忘记茶', 'remove')
    assert reply['tasks'][0]['state'] == 'COMPLETED'
    assert memory.count('demo') == 0


def test_ambiguous_content_checks_beyond_recall_top_fifty(notes):
    memory, bank = notes
    first = memory.remember('demo', '账单按周第一条', 'first')
    for i in range(50):
        memory.remember('demo', '账单和单按规则，按周回复，偏好' + str(i), 'distractor-' + str(i))
    last = memory.remember('demo', '账单按周另一条', 'last')
    recalled = memory.recall('demo', '账单按周')
    assert len(recalled) == 50
    assert len([n for n in recalled if '账单按周' in n['content']]) == 1
    reply = memory.handle('demo', '忘记账单按周', 'remove')
    assert reply['tasks'][0]['state'] == 'WAITING_USER'
    assert memory.get('demo', first['id'])['content'] == first['content']
    assert memory.get('demo', last['id'])['content'] == last['content']
    assert removed_count(bank) == 0


def test_ambiguous_display_is_bounded_but_never_deletes(notes):
    memory, bank = notes
    for i in range(55):
        memory.remember('demo', '账单按周偏好' + str(i), 'save-' + str(i))
    reply = memory.handle('demo', '忘记账单按周', 'remove')
    assert reply['tasks'][0]['state'] == 'WAITING_USER'
    assert len(reply['cards'][0]['notes']) <= 50
    assert memory.count('demo') == 55
    assert removed_count(bank) == 0


def test_concurrent_exact_removal_is_idempotent(notes):
    memory, bank = notes
    note = memory.remember('demo', '回复简短', 'save')
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda n: memory.handle('demo', '忘记 ' + note['id'], str(n)), range(2)))
    assert all(isinstance(r, dict) for r in replies)
    assert memory.count('demo') == 0
    assert removed_count(bank) == 1


def test_version_conflict_requests_refresh_and_rolls_back_removal(notes, monkeypatch):
    memory, bank = notes
    note = memory.remember('demo', '回复简短', 'save')
    original = memory._forget_in
    def stale_version(db, user, note_id, version):
        db.execute('UPDATE memory_notes SET version=version+1 WHERE id=?', (note_id,))
        return original(db, user, note_id, version)
    monkeypatch.setattr(memory, '_forget_in', stale_version)
    reply = memory.handle('demo', '忘记 ' + note['id'], 'remove')
    assert reply['tasks'][0]['state'] == 'WAITING_USER'
    assert '刷新' in reply['message']
    assert memory.get('demo', note['id']) == note
    assert removed_count(bank) == 0


def test_other_domain_failures_are_not_reported_as_safe_success(notes, monkeypatch):
    memory, _ = notes
    note = memory.remember('demo', '回复简短', 'save')
    def failure(*_):
        raise DomainError('STORAGE_ERROR', 'fixture storage failure', 500)
    monkeypatch.setattr(memory, '_forget_in', failure)
    with pytest.raises(DomainError) as error:
        memory.handle('demo', '忘记 ' + note['id'], 'remove')
    assert error.value.code == 'STORAGE_ERROR'


def test_api_exact_id_removal_refreshes_history_and_same_key(tmp_path):
    planner = Planner()
    planner.mode = 'rules'
    with TestClient(create_app(tmp_path / 'memory-id-api.db', planner=planner)) as client:
        session = client.post('/api/session', json={'user': 'demo'})
        client.headers['X-CSRF-Token'] = session.json()['csrf']
        saved = client.post('/api/chat', json={'text': '记住：回复简洁一点', 'request_key': 'save'}).json()
        note = saved['cards'][0]['notes'][0]
        before = client.get('/api/state').json()
        removed = client.post('/api/chat', json={'text': '忘记 ' + note['id'], 'request_key': 'remove'})
        assert removed.status_code == 200
        assert removed.json()['tasks'][0]['state'] == 'COMPLETED'
        assert client.get('/api/state').json()['memory_count'] == 0
        replay = client.post('/api/chat', json={'text': '记住：回复简洁一点', 'request_key': 'save'})
        assert replay.status_code == 200
        assert replay.json()['cards'][0]['notes'] == []
        history = client.get('/api/history').json()
        assert not any(c.get('notes') for m in history['messages'] if isinstance(m.get('content'), dict)
                       for c in m['content'].get('cards', []) if c.get('type') == 'memory')
        assert client.get('/api/state').json()['accounts'] == before['accounts']


@pytest.mark.parametrize(('utterance','content'), [
    ('忘记了','我开通了大字模式'),
    ('忘记怎么查账单','我喜欢详细说明怎么查账单'),
    ('忘记了怎么查账单','我喜欢详细说明了怎么查账单'),
    ('忘掉如何看账单','我喜欢详细说明如何看账单'),
    ('忘记 怎么查账单','我喜欢详细说明怎么查账单'),
    ('忘记了，怎么查账单','我喜欢详细说明了，怎么查账单'),
    ('忘掉：如何看账单','我喜欢详细说明如何看账单'),
    ('忘记 了','我开通了大字模式'),
])
def test_forgetting_how_to_do_something_is_not_note_deletion(notes, utterance, content):
    memory,bank=notes
    note=memory.remember('demo',content,'save')
    before=bank.snapshot('demo')
    reply=memory.handle('demo',utterance,'help')
    assert reply['tasks'][0]['state']=='WAITING_USER'
    assert memory.get('demo',note['id'])==note
    assert removed_count(bank)==0
    assert bank.snapshot('demo')['accounts']==before['accounts']


def test_api_forgetting_how_to_query_does_not_remove_overlapping_note(tmp_path):
    planner=Planner();planner.mode='rules'
    with TestClient(create_app(tmp_path/'forget-help.db',planner=planner)) as client:
        session=client.post('/api/session',json={'user':'demo'})
        client.headers['X-CSRF-Token']=session.json()['csrf']
        saved=client.post('/api/chat',json={'text':'记住：我喜欢详细说明怎么查账单','request_key':'save'})
        assert saved.status_code==200
        before=client.get('/api/state').json()
        reply=client.post('/api/chat',json={'text':'忘记怎么查账单','request_key':'help'})
        assert reply.status_code==200
        assert reply.json()['tasks'][0]['state']=='WAITING_USER'
        after=client.get('/api/state').json()
        assert after['memory_count']==before['memory_count']==1
        assert after['accounts']==before['accounts']


@pytest.mark.parametrize(('utterance','content'), [
    ('移出记忆。','回复简洁一点。'),
    ('移出记忆，','回复，简洁'),
    ('移出记忆？','真的吗？要简洁'),
    ('移出记忆怎么操作','怎么操作都要简洁'),
    ('忘记---','偏好用---分隔'),
])
def test_unspecified_note_or_help_never_deletes_a_unique_punctuation_match(notes,utterance,content):
    memory,bank=notes;note=memory.remember('demo',content,'save');before=bank.snapshot('demo')
    reply=memory.handle('demo',utterance,'ambiguous-remove')
    assert reply['tasks'][0]['state']=='WAITING_USER'
    assert memory.get('demo',note['id'])==note
    assert removed_count(bank)==0
    assert bank.snapshot('demo')['accounts']==before['accounts']


@pytest.mark.parametrize('content',['ありがとう','مرحبا'])
def test_explicit_non_ascii_letter_fragment_can_still_remove_note(notes,content):
    memory,bank=notes;memory.remember('demo',content,'save')
    reply=memory.handle('demo','移出记忆 '+content,'remove')
    assert reply['tasks'][0]['state']=='COMPLETED'
    assert memory.count('demo')==0
    assert removed_count(bank)==1


def test_api_unspecified_punctuation_does_not_remove_only_note(tmp_path):
    planner=Planner();planner.mode='rules'
    with TestClient(create_app(tmp_path/'unspecified-note.db',planner=planner)) as client:
        session=client.post('/api/session',json={'user':'demo'})
        client.headers['X-CSRF-Token']=session.json()['csrf']
        assert client.post('/api/chat',json={'text':'记住：回复简洁一点。','request_key':'save'}).status_code==200
        before=client.get('/api/state').json()
        reply=client.post('/api/chat',json={'text':'移出记忆。','request_key':'remove'})
        assert reply.status_code==200
        assert reply.json()['tasks'][0]['state']=='WAITING_USER'
        after=client.get('/api/state').json()
        assert after['memory_count']==before['memory_count']==1
        assert after['accounts']==before['accounts']
