"""Independent contract checks for fixed plans; all bank effects remain mocked."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from ff_agent.task_plan import PlanStore, PlanError


def nodes():
    return [
        {"idx":0,"action":{"type":"balance"},"deps":[]},
        {"idx":1,"action":{"type":"transfer","payee":"妈妈","amount_cents":30000},"deps":[0]},
    ]


def fixed(store,key="request",epoch="epoch-one"):
    plan,created=store.reserve("demo",key,"给妈妈生活费",epoch)
    assert created
    return store.freeze("demo",plan["id"],plan["version"],nodes())


def test_reservation_concurrency_has_one_owner_and_keeps_hash(tmp_path):
    store=PlanStore(tmp_path/"plans.db")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results=list(pool.map(lambda _:store.reserve("demo","one","same input","epoch-one"),range(10)))
    assert sum(created for _,created in results)==1
    assert len({plan["id"] for plan,_ in results})==1
    with pytest.raises(PlanError) as err:
        store.reserve("demo","one","different input","epoch-one")
    assert err.value.code=="IDEMPOTENCY_CONFLICT"
    assert store.reserve("demo","one","same input","new-epoch")[0]["risk_epoch"]=="epoch-one"


def test_plan_is_fixed_and_effect_keys_survive_reopen(tmp_path):
    path=tmp_path/"plans.db"
    store=PlanStore(path)
    plan=fixed(store)
    assert [node["effect_key"] for node in plan["nodes"]]==[f"task-{plan['id']}-0",f"task-{plan['id']}-1"]
    reopened=PlanStore(path)
    saved,created=reopened.reserve("demo","request","给妈妈生活费","epoch-one")
    assert not created and saved["nodes"]==plan["nodes"]
    with pytest.raises(PlanError):
        reopened.freeze("demo",plan["id"],saved["version"],nodes())


@pytest.mark.parametrize("bad",[
    [{"idx":0,"action":{"type":"balance"},"deps":[1]},{"idx":1,"action":{"type":"balance"},"deps":[0]}],
    [{"idx":0,"action":{"type":"balance"},"deps":[0]}],
    [{"idx":0,"action":{"type":"balance"},"deps":[9]}],
    [{"idx":0,"action":{"type":"balance"},"deps":[]},{"idx":1,"action":{"type":"balance"},"deps":[0,0]}],
    [{"idx":i,"action":{"type":"balance"},"deps":[]} for i in range(7)],
])
def test_invalid_graph_is_rejected_before_execution(tmp_path,bad):
    store=PlanStore(tmp_path/"plans.db")
    plan,_=store.reserve("demo","one","text","")
    with pytest.raises(PlanError):
        store.freeze("demo",plan["id"],plan["version"],bad)
    assert store.get("demo",plan["id"])["status"]=="PLANNING"
    assert store.get("demo",plan["id"])["nodes"]==[]


def test_dependencies_version_epoch_and_user_are_enforced(tmp_path):
    store=PlanStore(tmp_path/"plans.db")
    plan=fixed(store)
    for idx,version,epoch in [(1,plan["version"],"epoch-one"),(0,999,"epoch-one"),(0,plan["version"],"new")]:
        with pytest.raises(PlanError):
            store.claim("demo",plan["id"],idx,version,epoch)
    with pytest.raises(PlanError) as err:
        store.get("guest",plan["id"])
    assert err.value.status==404
    assert store.list("guest")==[]
    running,token=store.claim("demo",plan["id"],0,plan["version"],"epoch-one")
    with pytest.raises(PlanError):
        store.claim("demo",plan["id"],0,running["version"],"epoch-one")
    result=store.settle("demo",plan["id"],0,token,"DONE",{"message":"已读取"})
    assert result["nodes"][0]["state"]=="DONE"
    assert store.settle("demo",plan["id"],0,token,"DONE",{"message":"duplicate"})==result
    running,token=store.claim("demo",plan["id"],1,result["version"],"epoch-one")
    done=store.settle("demo",plan["id"],1,token,"PREPARED",{"operation_id":"mock-only"})
    assert done["status"]=="DONE"
    assert done["nodes"][1]["state"]=="PREPARED"


def test_pause_during_call_and_late_settle_never_resume_plan(tmp_path):
    store=PlanStore(tmp_path/"plans.db")
    plan=fixed(store)
    running,token=store.claim("demo",plan["id"],0,plan["version"],"epoch-one")
    stopped=store.control("demo",plan["id"],running["version"],"pause","epoch-one")
    with pytest.raises(PlanError):
        store.control("demo",plan["id"],stopped["version"],"resume","epoch-one")
    late=store.settle("demo",plan["id"],0,token,"DONE",{"message":"late read"})
    assert late["status"]=="PAUSED"
    assert late["nodes"][1]["state"]=="PENDING"
    with pytest.raises(PlanError):
        store.control("demo",plan["id"],late["version"],"resume","changed-epoch")
    resumed=store.control("demo",plan["id"],late["version"],"resume","epoch-one")
    assert resumed["status"]=="RUNNING"


def test_failed_node_resumes_original_key_without_rerunning_predecessor(tmp_path):
    store=PlanStore(tmp_path/"plans.db")
    plan=fixed(store)
    plan,token=store.claim("demo",plan["id"],0,plan["version"],"epoch-one")
    plan=store.settle("demo",plan["id"],0,token,"DONE",{"message":"saved"})
    plan,token=store.claim("demo",plan["id"],1,plan["version"],"epoch-one")
    failed=store.settle("demo",plan["id"],1,token,"FAILED",error_code="TOOL_UNAVAILABLE")
    assert failed["status"]=="PAUSED"
    resumed=store.control("demo",plan["id"],failed["version"],"resume","epoch-one")
    assert resumed["nodes"][0]["result"]=={"message":"saved"}
    assert resumed["nodes"][1]["effect_key"]==f"task-{plan['id']}-1"
    assert resumed["nodes"][1]["state"]=="PENDING"


def test_restart_orphan_is_visible_and_blocks_replay(tmp_path):
    path=tmp_path/"plans.db";store=PlanStore(path)
    plan=fixed(store)
    running,token=store.claim("demo",plan["id"],0,plan["version"],"epoch-one")
    restarted=PlanStore(path)
    restarted.mark_orphans()
    orphan=restarted.get("demo",plan["id"])
    assert orphan["status"]=="PAUSED" and orphan["reason"]=="PROCESS_INTERRUPTED"
    assert orphan["nodes"][0]["state"]=="ORPHAN"
    with pytest.raises(PlanError):
        restarted.control("demo",plan["id"],orphan["version"],"resume","epoch-one")
    late=store.settle("demo",plan["id"],0,token,"DONE",{"message":"late"})
    assert late["status"]=="PAUSED"


def test_stop_callback_is_atomic_and_handoff_is_local_terminal(tmp_path):
    path=tmp_path/"plans.db";store=PlanStore(path)
    plan=fixed(store)
    def broken(db,current):
        raise RuntimeError("injected callback fault")
    with pytest.raises(PlanError):
        store.control("demo",plan["id"],plan["version"],"pause","epoch-one",on_stop=broken)
    assert store.get("demo",plan["id"])==plan
    callbacks=[]
    result=store.control("demo",plan["id"],plan["version"],"handoff","epoch-one",on_stop=lambda db,p:callbacks.append(p["id"]))
    assert callbacks==[plan["id"]] and result["status"]=="HANDOFF"
    with pytest.raises(PlanError):
        store.control("demo",plan["id"],result["version"],"resume","epoch-one")
    with sqlite3.connect(path) as db:
        before="\n".join(db.iterdump())
        assert store.get("demo",plan["id"])["status"]=="HANDOFF"
        assert "\n".join(db.iterdump())==before


def test_blocked_predecessor_blocks_dependents_instead_of_hanging(tmp_path):
    store=PlanStore(tmp_path/"plans.db");plan=fixed(store)
    plan,token=store.claim("demo",plan["id"],0,plan["version"],"epoch-one")
    done=store.settle("demo",plan["id"],0,token,"BLOCKED",error_code="DENIED")
    assert done["status"]=="DONE"
    assert done["nodes"][1]["state"]=="BLOCKED"
    assert done["nodes"][1]["error_code"]=="DEPENDENCY_BLOCKED"


def test_known_planner_failure_can_explicitly_retry_but_unknown_planning_cannot(tmp_path):
    store=PlanStore(tmp_path/"plans.db")
    plan,_=store.reserve("demo","request","text","")
    with pytest.raises(PlanError):
        store.control("demo",plan["id"],plan["version"],"resume","")
    failed=store.planning_failed("demo",plan["id"],plan["version"])
    assert failed["reason"]=="PLANNING_FAILED"
    retry=store.control("demo",plan["id"],failed["version"],"resume","")
    assert retry["status"]=="PLANNING" and not retry["nodes"]



def test_restart_during_planning_pauses_zero_node_plan_without_replanning(tmp_path):
    path=tmp_path/'planning.db';old=PlanStore(path)
    initial,_=old.reserve('demo','planning-one','same text','')
    fresh=PlanStore(path);fresh.mark_orphans()
    stopped=fresh.get('demo',initial['id'])
    assert stopped['status']=='PAUSED' and stopped['reason']=='PROCESS_INTERRUPTED'
    assert stopped['nodes']==[]
    same,created=fresh.reserve('demo','planning-one','same text','')
    assert not created and same['id']==initial['id']
    with pytest.raises(PlanError):fresh.control('demo',stopped['id'],stopped['version'],'resume','')
    with pytest.raises(PlanError):old.freeze('demo',stopped['id'],stopped['version'],nodes())
    with pytest.raises(PlanError):old.planning_failed('demo',stopped['id'],stopped['version'])
    old.close();fresh.close()


def test_planning_retry_belongs_to_new_session_and_rejects_old_writer(tmp_path):
    path=tmp_path/'planning-owner.db';old=PlanStore(path)
    initial,_=old.reserve('demo','planning-one','same text','')
    failed=old.planning_failed('demo',initial['id'],initial['version'])
    fresh=PlanStore(path);fresh.mark_orphans()
    current=fresh.get('demo',initial['id'])
    assert current['reason']=='PLANNING_FAILED'
    resumed=fresh.control('demo',initial['id'],current['version'],'resume','')
    with pytest.raises(PlanError):old.freeze('demo',resumed['id'],resumed['version'],nodes())
    with pytest.raises(PlanError):old.planning_failed('demo',resumed['id'],resumed['version'])
    frozen=fresh.freeze('demo',resumed['id'],resumed['version'],nodes())
    assert frozen['status']=='RUNNING'
    old.close();fresh.close()


def test_legacy_planning_row_null_owner_is_preserved_and_quarantined(tmp_path):
    import hashlib
    path=tmp_path/'legacy-planning.db'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE plan_store_plans(id TEXT PRIMARY KEY,user TEXT NOT NULL,request_key TEXT NOT NULL,text_hash TEXT NOT NULL,risk_epoch TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT,frozen INTEGER NOT NULL,created_at REAL NOT NULL,UNIQUE(user,request_key))")
        db.execute("INSERT INTO plan_store_plans VALUES(?,?,?,?,?,1,'PLANNING',NULL,0,1000)",('legacy','demo','key',hashlib.sha256(b'text').hexdigest(),''))
    fresh=PlanStore(path)
    assert fresh.get('demo','legacy')['status']=='PLANNING'
    fresh.mark_orphans()
    result=fresh.get('demo','legacy')
    assert result['status']=='PAUSED' and result['reason']=='PROCESS_INTERRUPTED'
    assert result['id']=='legacy' and result['nodes']==[]
    fresh.close()
