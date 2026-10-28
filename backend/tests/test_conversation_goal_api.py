"""The new conversation mode preserves the old chat contract and trusted financial gates."""
from datetime import datetime
import json
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app

TEXT='给妈妈安排明天生日，预算500，用日常账户，只要花不要蛋糕'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=create_app(tmp_path/'goal-api.db');app.state.bank.clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00+08:00')
    with TestClient(app) as c:
        z=c.post('/api/session',json={'user':'demo'});assert z.status_code==200;c.headers['X-CSRF-Token']=z.json()['csrf'];yield app,c


def chat(e,text,key,**extra):return e[1].post('/api/chat',json={'text':text,'request_key':key,'goal_mode':True,**extra})

def current(e):
    z=e[1].get('/api/goals/current');assert z.status_code==200,z.text;return z.json()['goal']

def prepared(e):
    z=chat(e,TEXT,'goal-api-first');assert z.status_code==200,z.text;g=current(e)
    z=chat(e,'第一个','goal-api-choice',goal_id=g['id'],goal_version=g['version']);assert z.status_code==200,z.text
    g=current(e);ops=e[1].get('/api/state').json()['operations'];op=next(x for x in ops if x['type']=='birthday_budget_reserve');return g,op


def test_goal_mode_reads_catalog_but_legacy_mode_still_needs_metadata_start(env):
    legacy=env[1].post('/api/chat',json={'text':'帮我给妈妈安排明天的生日，预算500元，用日常账户','request_key':'legacy-bday-summary'})
    assert legacy.status_code==200 and env[1].get('/api/birthday/flows').json()['flows']==[]
    z=chat(env,TEXT,'new-bday-goal');assert z.status_code==200,z.text;g=current(env)
    assert g['phase']=='CHOOSING' and g['choices'][0]['id']=='flowers_simple'
    assert env[1].get('/api/state').json()['operations']==[]


def test_chat_mode_is_bound_to_request_identity_not_just_message(env):
    z=chat(env,TEXT,'mode-bound-key');assert z.status_code==200,z.text
    z=env[1].post('/api/chat',json={'text':TEXT,'request_key':'mode-bound-key'})
    assert z.status_code==409 and z.json()['error']['code']=='IDEMPOTENCY_CONFLICT'


def test_ui_choice_version_is_checked_and_mode_fields_are_strict(env):
    assert chat(env,TEXT,'goal-first-version').status_code==200;g=current(env)
    z=chat(env,'第一个','goal-old-choice',goal_id=g['id'],goal_version=g['version']-1)
    assert z.status_code in (409,422) and env[1].get('/api/state').json()['operations']==[]
    for extra in [{'goal_mode':'true'},{'goal_id':g['id']},{'goal_version':True}]:
        z=chat(env,'第一个','bad-fields-'+str(len(extra))+str(list(extra)),**extra)
        assert z.status_code in (400,422)


def test_only_original_operation_confirmation_can_authorize_budget(env):
    g,op=prepared(env);z=chat(env,'好的确认','goal-text-confirm')
    assert z.status_code==200 and env[1].get('/api/operations/'+op['id']).json()['status']=='WAITING_CONFIRMATION'
    assert chat(env,'查询余额','goal-read-interruption').status_code==200 and current(env)['id']==g['id']


def test_continue_prepares_next_card_only_after_authoritative_budget_success(env):
    g,op=prepared(env);z=env[1].post('/api/operations/'+op['id']+'/confirm',json={});assert z.status_code==200
    if z.json()['status']=='WAITING_VERIFICATION':assert env[1].post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).status_code==200
    latest=current(env);z=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':latest['version'],'request_key':'goal-continue-budget'})
    assert z.status_code==200,z.text
    ops=env[1].get('/api/state').json()['operations'];orders=[x for x in ops if x['type']=='birthday_order']
    assert len(orders)==1 and orders[0]['status']=='WAITING_CONFIRMATION'
    again=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':latest['version'],'request_key':'goal-continue-budget'})
    assert again.status_code==200 and len([x for x in env[1].get('/api/state').json()['operations'] if x['type']=='birthday_order'])==1


def test_goal_get_is_pure_read_and_has_no_claim_tokens(env):
    g,_=prepared(env)
    with env[0].state.bank.transaction() as db:before=list(db.iterdump())
    public=current(env);assert set(public['steps'][0])<= {'label','state'}
    assert 'effect_key' not in json.dumps(public) and 'token_hash' not in json.dumps(public)
    with env[0].state.bank.transaction() as db:assert list(db.iterdump())==before


def test_foreign_or_new_session_cannot_continue_or_adopt_goal(env):
    g,_=prepared(env)
    for user in ['guest','demo']:
        login=env[1].post('/api/session',json={'user':user});env[1].headers['X-CSRF-Token']=login.json()['csrf']
        assert current(env) is None
        assert env[1].get('/api/goals/'+g['id']).status_code==404
        assert env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':g['version'],'request_key':'foreign-goal-continue'}).status_code==404


def test_continue_requires_csrf_and_does_not_treat_get_as_authorization(env):
    g,_=prepared(env);env[1].headers.pop('X-CSRF-Token')
    z=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':g['version'],'request_key':'goal-missing-csrf'})
    assert z.status_code==403
    assert env[1].get('/api/goals/current').status_code==200
    assert env[1].get('/api/goals/'+g['id']+'/continue').status_code in (404,405)


def test_continuous_renewal_goal_uses_real_api_tool_guards_and_both_confirmation_gates(env):
    z=chat(env,'看看这个月花哪儿了，再帮我处理不需要的续费','renewal-api-start')
    assert z.status_code==200,z.text
    g=current(env);assert g and g['kind']=='spending_subscriptions'
    query=next(c['operation'] for c in z.json()['cards'] if c.get('type')=='operation')
    assert query['type']=='subscription_query' and query['status']=='WAITING_CONFIRMATION'
    assert env[1].post('/api/operations/'+query['id']+'/confirm',json={}).status_code==200
    g=current(env)
    z=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':g['version'],'request_key':'renewal-api-query'})
    assert z.status_code==200,z.text
    g=current(env);assert g['phase']=='CHOOSING'
    z=chat(env,'第一个','renewal-api-choice',goal_id=g['id'],goal_version=g['version'])
    assert z.status_code==200,z.text
    op=next(c['operation'] for c in z.json()['cards'] if c.get('type')=='operation' and c['operation']['type']=='subscription_cancel')
    assert env[1].post('/api/operations/'+op['id']+'/confirm',json={}).status_code==200
    g=current(env)
    z=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':g['version'],'request_key':'renewal-api-cancel'})
    assert z.status_code==200,z.text
    assert z.json()['goal']['status']=='COMPLETED'
    with env[0].state.bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    repeat=env[1].post('/api/goals/'+g['id']+'/continue',json={'expected_version':g['version'],'request_key':'renewal-api-cancel'})
    assert repeat.status_code==200 and repeat.json()['goal']['status']=='UNKNOWN'
    assert '已停止' not in repeat.json()['message'] and '完成' not in repeat.json()['message']
