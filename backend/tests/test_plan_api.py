"""Fixed chat plans reuse exact actions; control never confirms money."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from ff_agent.api import create_app
from ff_agent.bank import DomainError


class OrderedPlanner:
    mode="model"
    def __init__(self):self.calls=0
    def plan(self,text,context):
        self.calls+=1
        return [{"type":"transfer","payee":"妈妈","amount_cents":30000},{"type":"bill","period":"2026-10"}]


def login(client,user="demo"):
    client.headers["X-CSRF-Token"]=client.post("/api/session",json={"user":user}).json()["csrf"]


def chat(client):
    return client.post("/api/chat",json={"text":"先准备转账再看账单","request_key":"fixed"})


def test_failed_action_retry_reuses_frozen_actions_across_restart(tmp_path,monkeypatch):
    path=tmp_path/"plan.db";planner=OrderedPlanner();app=create_app(path,planner=planner)
    monkeypatch.setattr(app.state.bank,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("outage")))
    with TestClient(app) as client:
        login(client);assert chat(client).status_code==503
        plans=client.get("/api/plans").json()["plans"]
        assert len(plans)==1 and plans[0]["status"]=="PAUSED"
        assert [n["state"] for n in plans[0]["nodes"]]==["PREPARED","FAILED"]
        operation=client.get("/api/state").json()["operations"][0]
        assert "token" not in plans[0]["nodes"][0] and "action" not in plans[0]["nodes"][0]
    reopened=create_app(path,planner=planner)
    with TestClient(reopened) as client:
        login(client);result=chat(client)
        assert result.status_code==200
        assert planner.calls==1
        assert len(result.json()["cards"])==2
        state=client.get("/api/state").json()
        assert [o["id"] for o in state["operations"]]==[operation["id"]]
        assert sum(a["balance_cents"] for a in state["accounts"])==2800000
        assert client.get("/api/plans").json()["plans"][0]["status"]=="DONE"


def test_pause_wins_against_late_prepare_inside_bank_transaction(tmp_path,monkeypatch):
    app=create_app(tmp_path/"pause.db",planner=OrderedPlanner())
    entered=Event();release=Event();original=app.state.bank.prepare_transfer
    def delayed(*args,**kwargs):
        entered.set();assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(app.state.bank,"prepare_transfer",delayed)
    with TestClient(app) as client,ThreadPoolExecutor() as pool:
        login(client);future=pool.submit(chat,client)
        try:
            assert entered.wait(5)
            plan=client.get("/api/plans").json()["plans"][0]
            paused=client.post("/api/plans/"+plan["id"]+"/pause",json={"expected_version":plan["version"]})
            assert paused.status_code==200
            stale=client.post("/api/plans/"+plan["id"]+"/resume",json={"expected_version":plan["version"]})
            assert stale.status_code==409
        finally:release.set()
        assert future.result().status_code in (200,409)
        assert client.get("/api/state").json()["operations"]==[]
        plan=client.get("/api/plans").json()["plans"][0]
        assert plan["status"]=="PAUSED"
        assert plan["nodes"][1]["state"] in ("PENDING","BLOCKED")


def test_handoff_is_persistent_local_summary_with_csrf_and_ownership(tmp_path,monkeypatch):
    app=create_app(tmp_path/"handoff.db",planner=OrderedPlanner())
    monkeypatch.setattr(app.state.bank,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("private failure")))
    with TestClient(app) as client:
        login(client);assert chat(client).status_code==503
        plan=client.get("/api/plans").json()["plans"][0]
        token=client.headers.pop("X-CSRF-Token")
        url="/api/plans/"+plan["id"]+"/handoff"
        assert client.post(url,json={"expected_version":plan["version"]}).status_code==403
        client.headers["X-CSRF-Token"]=token
        result=client.post(url,json={"expected_version":plan["version"]})
        assert result.status_code==200
        summary=result.json()["handoff"]
        assert summary["simulated"] is True and summary["contacted_human"] is False
        assert summary["operations"][0]["status"]=="CANCELLED"
        assert "private failure" not in str(summary)
        assert client.get("/api/plans/"+plan["id"]+"/handoff").json()==summary
        assert client.get("/api/state").json()["operations"][0]["status"]=="CANCELLED"
        login(client,"guest")
        assert client.get("/api/plans/"+plan["id"]).status_code==404
        assert client.get("/api/plans/"+plan["id"]+"/handoff").status_code==404


def test_bank_prepare_rejects_forged_plan_ticket(tmp_path):
    app=create_app(tmp_path/"bank-ticket.db");bank=app.state.bank
    with pytest.raises(DomainError) as error:
        bank.prepare_transfer("demo","p_mom",30000,"forged",plan_ticket={"id":"missing","idx":0,"token":"bad"})
    assert error.value.code=="PLAN_STOPPED"
    assert bank.snapshot("demo")["operations"]==[]


def test_old_ambiguous_payee_card_cannot_prepare_after_plan_pause(tmp_path,monkeypatch):
    class AmbiguousPlanner:
        mode="rules"
        def plan(self,*args):return [{"type":"transfer","payee":"林晓","amount_cents":30000},{"type":"bill","period":"2026-10"}]
    app=create_app(tmp_path/"selection.db",planner=AmbiguousPlanner())
    monkeypatch.setattr(app.state.bank,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("outage")))
    with TestClient(app) as client:
        login(client);assert chat(client).status_code==503
        plan=app.state.plans.list("demo")[0]
        assert plan["status"]=="PAUSED"
        choice=plan["nodes"][0]["result"]["cards"][0]
        denied=client.post("/api/transfers/prepare",json={"payee_id":"p_lin_1","amount_cents":30000,"request_key":"select-"+choice["selection_key"],"safety_epoch":choice["safety_epoch"]})
        assert denied.status_code==409 and denied.json()["error"]["code"]=="PLAN_STOPPED"
        assert client.get("/api/state").json()["operations"]==[]


def test_resume_endpoint_runs_only_failed_original_step(tmp_path,monkeypatch):
    planner=OrderedPlanner();app=create_app(tmp_path/"resume.db",planner=planner)
    original=app.state.bank.analyze
    monkeypatch.setattr(app.state.bank,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("outage")))
    with TestClient(app) as client:
        login(client);assert chat(client).status_code==503
        plan=client.get("/api/plans").json()["plans"][0]
        operation=client.get("/api/state").json()["operations"][0]
        monkeypatch.setattr(app.state.bank,"analyze",original)
        result=client.post("/api/plans/"+plan["id"]+"/resume",json={"expected_version":plan["version"]})
        assert result.status_code==200
        assert result.json()["plan"]["status"]=="DONE" and planner.calls==1
        assert client.get("/api/state").json()["operations"][0]["id"]==operation["id"]


def test_plan_handoff_cancels_selected_payee_operation(tmp_path,monkeypatch):
    class AmbiguousPlanner:
        mode="rules"
        def plan(self,*args):return [{"type":"transfer","payee":"林晓","amount_cents":30000},{"type":"bill","period":"2026-10"}]
    app=create_app(tmp_path/"selected.db",planner=AmbiguousPlanner())
    plans=app.state.plans
    plan,_=plans.reserve("demo","request","text","")
    plan=plans.freeze("demo",plan["id"],plan["version"],[{"idx":0,"action":{"type":"transfer","payee":"林晓","amount_cents":30000},"deps":[]},{"idx":1,"action":{"type":"bill","period":"2026-10"},"deps":[0]}])
    plan,token=plans.claim("demo",plan["id"],0,plan["version"],"")
    selection_key=plan["nodes"][0]["effect_key"]
    plan=plans.settle("demo",plan["id"],0,token,"PREPARED",{"cards":[{"type":"payees","selection_key":selection_key,"candidates":[{"id":"p_lin_1"}]}]})
    with TestClient(app) as client:
        login(client)
        prepared=client.post("/api/transfers/prepare",json={"payee_id":"p_lin_1","amount_cents":30000,"request_key":"select-"+selection_key})
        assert prepared.status_code==200
        result=client.post("/api/plans/"+plan["id"]+"/handoff",json={"expected_version":plan["version"]})
        assert result.status_code==200
        assert result.json()["handoff"]["operations"][0]["status"]=="CANCELLED"
        assert client.get("/api/state").json()["operations"][0]["status"]=="CANCELLED"


def test_confirmed_subscription_query_does_not_advance_paused_plan(tmp_path,monkeypatch):
    class CancelPlanner:
        mode="rules"
        def plan(self,*args):return [{"type":"cancel_subscription","merchant":"流光视频"},{"type":"bill","period":"2026-10"}]
    app=create_app(tmp_path/"continuation.db",planner=CancelPlanner())
    monkeypatch.setattr(app.state.bank,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("outage")))
    with TestClient(app) as client:
        login(client);assert chat(client).status_code==503
        op=client.get("/api/state").json()["operations"][0]
        assert op["type"]=="subscription_query"
        confirmed=client.post("/api/operations/"+op["id"]+"/confirm",json={})
        assert confirmed.status_code==200 and confirmed.json()["status"]=="SUCCEEDED"
        assert "next_operation" not in confirmed.json()
        assert "notice" in confirmed.json()
        state=client.get("/api/state").json()
        assert len(state["operations"])==1
        assert all(s["status"]=="ACTIVE" for s in client.get("/api/subscriptions").json()["subscriptions"])


def failed_plan_without_chat(app):
    plans=app.state.plans
    plan,_=plans.reserve("demo","manual-no-row","text","")
    plan=plans.freeze("demo",plan["id"],plan["version"],[{"idx":0,"action":{"type":"balance"},"deps":[]}])
    plan,token=plans.claim("demo",plan["id"],0,plan["version"],"")
    return plans.settle("demo",plan["id"],0,token,"FAILED",error_code="TOOL_UNAVAILABLE")


def test_missing_original_request_rolls_back_entire_resume(tmp_path):
    app=create_app(tmp_path/"missing-row.db")
    before=failed_plan_without_chat(app)
    with TestClient(app) as client:
        login(client)
        response=client.post("/api/plans/"+before["id"]+"/resume",json={"expected_version":before["version"]})
        assert response.status_code==404
        assert app.state.plans.get("demo",before["id"])==before


def test_old_processing_request_never_treated_as_dead_or_reclaimed(tmp_path):
    app=create_app(tmp_path/"old-processing.db")
    before=failed_plan_without_chat(app)
    with app.state.bank.transaction() as db:
        db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'PROCESSING',0)",("demo","manual-no-row","text"))
        db.execute("INSERT INTO chat_request_epochs VALUES(?,?,?)",("demo","manual-no-row",""))
    with TestClient(app) as client:
        login(client)
        response=client.post("/api/plans/"+before["id"]+"/resume",json={"expected_version":before["version"]})
        assert response.status_code==409
        assert response.json()["error"]["code"]=="REQUEST_PENDING"
        assert app.state.plans.get("demo",before["id"])==before
        retry=client.post("/api/chat",json={"text":"text","request_key":"manual-no-row"})
        assert retry.status_code==409 and retry.json()["error"]["code"]=="REQUEST_PENDING"
        with app.state.bank.transaction() as db:
            assert db.execute("SELECT status FROM chat_requests WHERE user_id='demo' AND request_key='manual-no-row'").fetchone()[0]=="PROCESSING"



def test_restart_checks_inflight_planner_and_rejects_late_freeze(tmp_path):
    entered=Event();release=Event()
    class SlowPlanner:
        mode='model'
        calls=0
        def plan(self,*args):
            self.calls+=1;entered.set();assert release.wait(6)
            return [{'type':'transfer','payee':'妈妈','amount_cents':30000}]
    class FreshPlanner:
        mode='model'
        calls=0
        def plan(self,*args):
            self.calls+=1
            return [{'type':'balance'}]
    path=tmp_path/'planning-http-restart.db';slow=SlowPlanner();new=FreshPlanner()
    old_app=create_app(path,planner=slow)
    with TestClient(old_app) as original,ThreadPoolExecutor() as pool:
        login(original);future=pool.submit(chat,original)
        try:
            assert entered.wait(5)
            fresh_app=create_app(path,planner=new)
            with TestClient(fresh_app) as client:
                login(client)
                plan=client.get('/api/plans').json()['plans'][0]
                assert plan['status']=='PAUSED' and plan['reason']=='PROCESS_INTERRUPTED' and plan['nodes']==[]
                tool=next(t for t in client.get('/api/state').json()['tools'] if t['group']=='planner')
                assert tool['state']=='CHECK_REQUIRED' and tool['needs_handoff']
                retry=chat(client)
                assert retry.status_code==409 and new.calls==0
                assert client.post('/api/plans/'+plan['id']+'/resume',json={'expected_version':plan['version']}).status_code==409
                release.set()
                assert future.result().status_code==409
                assert slow.calls==1 and new.calls==0
                assert client.get('/api/state').json()['operations']==[]
                current=client.get('/api/plans/'+plan['id']).json()
                assert current['status']=='PAUSED' and current['nodes']==[]
        finally:release.set()
