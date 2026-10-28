import json
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ff_agent import api
from ff_agent import model_settings_api as msa
from ff_agent.api import create_app

FAKE='test_key_'+'x'*24
URL='/api/model-settings'
BASE={'model':'deepseek-flash','mode':'rules','api_key':'','consent':False}
JSON={'Content-Type':'application/json'}


@pytest.fixture
def ctx(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    monkeypatch.setenv('FF_ALLOW_MODEL_SETTINGS','1')
    database=tmp_path/'settings.db'
    with TestClient(create_app(database)) as client:
        monkeypatch.setattr(api,'ROOT',tmp_path)
        client.headers['X-CSRF-Token']=client.post('/api/session',json={'user':'demo'}).json()['csrf']
        yield client,tmp_path,database


def dump(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


def clean(response):
    return FAKE not in response.text and 'x'*24 not in response.text


def test_missing_env_defaults_and_get_never_writes(ctx):
    client,root,_=ctx
    response=client.get(URL)
    assert response.status_code==200
    assert response.json()=={'provider':'deepseek','base_url':'https://api.deepseek.com','model':'deepseek-flash',
                             'mode':'rules','has_key':False,'editable':True,'restart_required':False}
    assert not (root/'.env').exists()


@pytest.mark.parametrize('flag',[None,'0','true',' 1'])
def test_flag_off_rejects_get_and_post(ctx,monkeypatch,flag):
    client,root,_=ctx
    if flag is None:
        monkeypatch.delenv('FF_ALLOW_MODEL_SETTINGS')
    else:
        monkeypatch.setenv('FF_ALLOW_MODEL_SETTINGS',flag)
    assert client.get(URL).status_code==403
    response=client.post(URL,json={**BASE,'mode':'model','api_key':FAKE,'consent':True})
    assert response.status_code==403 and clean(response)
    assert not (root/'.env').exists()


def test_guest_rejected_even_when_flag_on(ctx):
    client,root,_=ctx
    client.headers['X-CSRF-Token']=client.post('/api/session',json={'user':'guest'}).json()['csrf']
    assert client.get(URL).json()['error']['code']=='MODEL_SETTINGS_FORBIDDEN'
    response=client.post(URL,json={**BASE,'api_key':FAKE})
    assert response.status_code==403 and clean(response)
    assert not (root/'.env').exists()


def test_csrf_and_origin_enforced(ctx):
    client,root,_=ctx
    payload={**BASE,'api_key':FAKE}
    bad_csrf=client.post(URL,json=payload,headers={'X-CSRF-Token':'wrong'})
    assert bad_csrf.status_code==403 and bad_csrf.json()['error']['code']=='CSRF_REJECTED'
    evil=client.post(URL,json=payload,headers={'Origin':'http://evil.example'})
    assert evil.status_code==403 and evil.json()['error']['code']=='ORIGIN_REJECTED'
    assert clean(bad_csrf) and clean(evil) and not (root/'.env').exists()


BAD=[{'mode':'real'},{'model':1},{'consent':'true'},{'extra':FAKE},
     {'model':'deepseek\nFF_PLANNER_MODE=model','api_key':FAKE},{'model':'deep seek'},{'model':'m'*101},
     {'api_key':FAKE+'\r\nFF_X=1'},{'api_key':FAKE+'\x00'},{'api_key':FAKE+'"'},{'api_key':FAKE*20},
     {'api_key':'short'},{'api_key':7},{'mode':'model','consent':True},
     {'mode':'model','api_key':FAKE,'consent':False}]
RAW=[json.dumps({**BASE,**case}).encode() for case in BAD]+[
    b'[]',b'{bad',json.dumps({'model':'deepseek-flash','mode':'rules','consent':False}).encode(),
    json.dumps({**BASE,'api_key':FAKE,'model':'a'*5000}).encode()]


@pytest.mark.parametrize('raw',RAW)
def test_invalid_payloads_rejected_without_echo_or_write(ctx,raw):
    client,root,_=ctx
    (root/'.env').write_text('KEEP=1\n',encoding='utf-8')
    response=client.post(URL,content=raw,headers=JSON)
    assert response.status_code==400
    assert response.json()['error']['code']=='MODEL_SETTINGS_INVALID'
    assert clean(response)
    assert (root/'.env').read_text(encoding='utf-8')=='KEEP=1\n'


def test_symlink_env_is_not_followed(ctx,tmp_path):
    client,root,_=ctx
    target=tmp_path/'target.env'
    target.write_text('FF_MODEL_NAME=linked\n',encoding='utf-8')
    try:
        os.symlink(target,root/'.env')
    except (OSError,NotImplementedError):
        pytest.skip('symlinks unavailable')
    assert client.get(URL).status_code==503
    response=client.post(URL,json={**BASE,'api_key':FAKE})
    assert response.status_code==503 and clean(response)
    assert target.read_text(encoding='utf-8')=='FF_MODEL_NAME=linked\n'


def test_save_preserves_unrelated_lines_and_never_returns_key(ctx):
    client,root,_=ctx
    (root/'.env').write_text('# keep comment\nFF_PORT=9000\nFF_MODEL_NAME=old\nFF_MODEL_NAME=dup\nOTHER_SECRET=keepme\n',encoding='utf-8')
    response=client.post(URL,json={'model':'deepseek-chat','mode':'model','api_key':FAKE,'consent':True})
    assert response.status_code==200 and clean(response) and 'keepme' not in response.text
    body=response.json()
    assert body['saved'] is True and body['restart_required'] is True and body['has_key'] is True
    lines=(root/'.env').read_text(encoding='utf-8').splitlines()
    for line in ('# keep comment','FF_PORT=9000','OTHER_SECRET=keepme','FF_MODEL_BASE_URL=https://api.deepseek.com',
                 'FF_MODEL_API_KEY='+FAKE,'FF_PLANNER_MODE=model','FF_MODEL_REASONING_EFFORT='):
        assert lines.count(line)==1
    assert [l for l in lines if l.startswith('FF_MODEL_NAME=')]==['FF_MODEL_NAME=deepseek-chat']
    assert clean(client.get(URL))


def test_same_official_base_keeps_saved_key(ctx):
    client,root,_=ctx
    (root/'.env').write_text(f'FF_MODEL_BASE_URL=https://api.deepseek.com\nFF_MODEL_API_KEY={FAKE}\n',encoding='utf-8')
    response=client.post(URL,json={'model':'deepseek-flash','mode':'model','api_key':'','consent':True})
    assert response.status_code==200 and response.json()['has_key'] is True and clean(response)
    assert 'FF_MODEL_API_KEY='+FAKE in (root/'.env').read_text(encoding='utf-8').splitlines()


def test_foreign_base_key_is_never_reused(ctx):
    client,root,_=ctx
    seed=f'FF_MODEL_BASE_URL=https://other.example\nFF_MODEL_API_KEY={FAKE}\n'
    (root/'.env').write_text(seed,encoding='utf-8')
    denied=client.post(URL,json={'model':'deepseek-flash','mode':'model','api_key':'','consent':True})
    assert denied.status_code==400 and clean(denied)
    assert (root/'.env').read_text(encoding='utf-8')==seed
    saved=client.post(URL,json=BASE)
    assert saved.status_code==200 and saved.json()['has_key'] is False
    text=(root/'.env').read_text(encoding='utf-8')
    assert FAKE not in text and 'other.example' not in text and 'FF_MODEL_API_KEY=' in text.splitlines()


def test_rules_without_key_on_missing_env(ctx):
    client,root,_=ctx
    response=client.post(URL,json=BASE)
    assert response.status_code==200
    assert response.json()['mode']=='rules' and response.json()['has_key'] is False
    assert 'FF_PLANNER_MODE=rules' in (root/'.env').read_text(encoding='utf-8').splitlines()


def test_write_failure_is_generic_and_atomic(ctx,monkeypatch):
    client,root,_=ctx
    (root/'.env').write_text('KEEP=1\n',encoding='utf-8')
    def boom(*args):
        raise OSError(FAKE)
    monkeypatch.setattr(msa.os,'replace',boom)
    response=client.post(URL,json={**BASE,'mode':'model','api_key':FAKE,'consent':True})
    assert response.status_code==503 and clean(response)
    assert response.json()['error']['code']=='MODEL_SETTINGS_UNAVAILABLE'
    assert (root/'.env').read_text(encoding='utf-8')=='KEEP=1\n'
    assert not list(root.glob('.env.*.tmp'))


def test_restart_pending_without_runtime_or_bank_changes(ctx):
    client,root,database=ctx
    environ=dict(os.environ)
    health=client.get('/api/health').json()['planner_mode']
    before=dump(database)
    response=client.post(URL,json={'model':'deepseek-chat','mode':'model','api_key':FAKE,'consent':True})
    assert response.status_code==200
    assert dict(os.environ)==environ
    snapshot=client.get(URL).json()
    assert snapshot['restart_required'] is True and snapshot['mode']=='model' and snapshot['model']=='deepseek-chat'
    assert client.get('/api/health').json()['planner_mode']==health
    assert dump(database)==before
