"""Contract tests independent of the generated implementation."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def make_guard(tmp_path):
    from ff_agent.tool_guard import ToolGuard
    clock=[1000.0]
    guard=ToolGuard(tmp_path/'guard.db',clock=lambda:clock[0])
    return guard,clock


def fail(guard,user='demo',group='planner'):
    ticket=guard.start(user,group)
    guard.finish(ticket,'tech')


def view(guard,user='demo',group='planner'):
    return next(s for s in guard.snapshot(user) if s['group']==group)


def test_three_technical_failures_pause_without_calling_function(tmp_path):
    from ff_agent.tool_guard import ToolPaused
    guard,clock=make_guard(tmp_path)
    for _ in range(3): fail(guard)
    calls=[]
    with pytest.raises(ToolPaused): guard.run('demo','planner',lambda:calls.append(1))
    assert calls==[]
    assert view(guard)['state']=='PAUSED'
    assert view(guard)['retry_after_seconds']==30
    clock[0]+=30
    assert guard.run('demo','planner',lambda:'ready')=='ready'
    assert view(guard)['state']=='READY'


def test_business_rejection_breaks_consecutive_failures(tmp_path):
    guard,_=make_guard(tmp_path)
    fail(guard);fail(guard)
    ticket=guard.start('demo','planner');guard.finish(ticket,'business')
    fail(guard)
    assert view(guard)['consecutive_failures']==1
    assert view(guard)['state']=='READY'


def test_pause_persists_and_isolated_by_user_and_group(tmp_path):
    from ff_agent.tool_guard import ToolGuard,ToolPaused
    guard,clock=make_guard(tmp_path)
    for _ in range(3): fail(guard)
    reopened=ToolGuard(tmp_path/'guard.db',clock=lambda:clock[0])
    with pytest.raises(ToolPaused): reopened.start('demo','planner')
    assert reopened.run('guest','planner',lambda:1)==1
    assert reopened.run('demo','bill',lambda:2)==2


def test_only_one_probe_and_expired_lease_does_not_overlap(tmp_path):
    from ff_agent.tool_guard import ToolPaused
    guard,clock=make_guard(tmp_path)
    for _ in range(3): fail(guard)
    clock[0]+=30
    ticket=guard.start('demo','planner')
    with pytest.raises(ToolPaused): guard.start('demo','planner')
    clock[0]+=36
    with pytest.raises(ToolPaused) as error: guard.start('demo','planner')
    assert error.value.needs_handoff is True
    with pytest.raises(ToolPaused): guard.recover('demo','planner')
    guard.finish(ticket,'ok')
    assert view(guard)['state']=='CHECK_REQUIRED'
    guard.recover('demo','planner')
    assert guard.run('demo','planner',lambda:1)==1


def test_late_result_and_duplicate_settlement_cannot_clear_new_pause(tmp_path):
    guard,_=make_guard(tmp_path)
    old=guard.start('demo','planner')
    for _ in range(3): fail(guard)
    guard.finish(old,'ok');guard.finish(old,'ok')
    assert view(guard)['state']=='PAUSED'
    assert view(guard)['consecutive_failures']==3


def test_run_redacts_technical_error_and_preserves_business_rejection(tmp_path):
    from ff_agent.tool_guard import ToolFailure
    guard,_=make_guard(tmp_path)
    def broken(): raise RuntimeError('private-provider-value')
    with pytest.raises(ToolFailure) as error: guard.run('demo','planner',broken)
    assert 'private-provider-value' not in str(error.value)
    assert error.value.__cause__ is None
    class Rejected(Exception): pass
    def rejected(): raise Rejected('expected business refusal')
    with pytest.raises(Rejected): guard.run('demo','planner',rejected,business_error_predicate=lambda e:isinstance(e,Rejected))
    assert view(guard)['consecutive_failures']==0


def test_concurrent_probe_acquisition_is_atomic(tmp_path):
    from ff_agent.tool_guard import ToolPaused
    guard,clock=make_guard(tmp_path)
    for _ in range(3): fail(guard)
    clock[0]+=30
    barrier=threading.Barrier(5)
    def start():
        barrier.wait()
        try: return guard.start('demo','planner')
        except ToolPaused: return None
    with ThreadPoolExecutor(max_workers=5) as pool: tickets=list(pool.map(lambda _:start(),range(5)))
    assert sum(t is not None for t in tickets)==1


def test_explicit_restart_orphan_quarantine_is_readonly_visible_and_fail_closed(tmp_path):
    import sqlite3
    from ff_agent.tool_guard import ToolGuard, ToolPaused
    path=tmp_path/"orphan.db"
    old=ToolGuard(path,clock=lambda:100)
    ticket=old.start("demo","bill")
    fresh=ToolGuard(path,clock=lambda:200)
    assert fresh.snapshot("demo")[1]["state"]=="READY"
    assert fresh.mark_orphans()==1
    assert fresh.mark_orphans()==0
    health={item["group"]:item for item in fresh.snapshot("demo")}["bill"]
    assert health["state"]=="CHECK_REQUIRED" and health["needs_handoff"]
    with pytest.raises(ToolPaused):fresh.start("demo","bill")
    with pytest.raises(ToolPaused):fresh.recover("demo","bill")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT settled_at FROM tool_calls WHERE id=?",(ticket.id,)).fetchone()[0] is None
        before="\n".join(db.iterdump())
        fresh.snapshot("demo")
        assert "\n".join(db.iterdump())==before
    old.finish(ticket,"ok")
    health={item["group"]:item for item in fresh.snapshot("demo")}["bill"]
    assert health["state"]=="CHECK_REQUIRED"
    assert fresh.recover("demo","bill")["probe_available"]
    fresh.close();old.close()


def test_cooldown_with_unsettled_call_requires_review_instead_of_waiting_zero(tmp_path):
    from ff_agent.tool_guard import ToolGuard, ToolPaused
    now=[0];guard=ToolGuard(tmp_path/"pending.db",clock=lambda:now[0])
    pending=guard.start("demo","bill")
    for _ in range(3):guard.finish(guard.start("demo","bill"),"tech")
    now[0]=31
    health={item["group"]:item for item in guard.snapshot("demo")}["bill"]
    assert health["state"]=="CHECK_REQUIRED" and health["needs_handoff"]
    with pytest.raises(ToolPaused):guard.start("demo","bill")
    guard.finish(pending,"ok");guard.close()
