"""Explicit budget correction in goal mode: same goal, re-filtered catalog, no financial effects."""
from datetime import datetime
import json
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app

TEXT='给妈妈安排明天生日，预算500，用日常账户，只要花不要蛋糕'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=create_app(tmp_path/'budget-fix.db');app.state.bank.clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00+08:00')
    with TestClient(app) as c:
        z=c.post('/api/session',json={'user':'demo'});assert z.status_code==200;c.headers['X-CSRF-Token']=z.json()['csrf'];yield app,c

def chat(e,text,key,g=None):
    extra={'goal_id':g['id'],'goal_version':g['version']} if g else {}
    return e[1].post('/api/chat',json={'text':text,'request_key':key,'goal_mode':True,**extra})

def current(e):
    z=e[1].get('/api/goals/current');assert z.status_code==200,z.text;return z.json()['goal']

def ops(e):return e[1].get('/api/state').json()['operations']

def start(e):
    z=chat(e,TEXT,'bf-first');assert z.status_code==200,z.text;g=current(e);assert g['phase']=='CHOOSING';return g

def test_budget_correction_600_same_goal_refilters_no_ops(env):
    g=start(env)
    z=chat(env,'预算改成600元','bf-600',g);assert z.status_code==200,z.text
    n=current(env);assert n['id']==g['id'] and n['version']>g['version']
    s=json.dumps(n,ensure_ascii=False);assert '60000' in s or '600' in s
    assert n['phase']=='CHOOSING' and ops(env)==[]

def test_budget_correction_200_no_choices_no_ops(env):
    g=start(env)
    z=chat(env,'预算改成200元','bf-200',g);assert z.status_code==200,z.text
    n=current(env);assert n['id']==g['id'] and not n.get('choices')
    assert ops(env)==[]

def test_budget_correction_after_choice_is_read_only(env):
    g=start(env)
    z=chat(env,'第一个','bf-choice',g);assert z.status_code==200,z.text
    g=current(env);st=env[1].get('/api/state').json();before=st['operations']
    assert any(x['type']=='birthday_budget_reserve' for x in before)
    z=chat(env,'预算改成600元','bf-late-600',g);assert z.status_code==200,z.text
    assert '核对' in json.dumps(z.json(),ensure_ascii=False)
    after=env[1].get('/api/state').json()
    assert after['operations']==before and after.get('accounts')==st.get('accounts')
    n=current(env);assert n['id']==g['id'] and n['version']==g['version']

def test_budget_correction_whitespace_after_budget_same_goal_no_ops(env):
    g=start(env)
    z=chat(env,'预算 改成600元','bf-ws-600',g);assert z.status_code==200,z.text
    n=current(env);assert n['id']==g['id'] and n['version']>g['version']
    s=json.dumps(n,ensure_ascii=False);assert '600.00' in s
    assert n['phase']=='CHOOSING' and ops(env)==[]
