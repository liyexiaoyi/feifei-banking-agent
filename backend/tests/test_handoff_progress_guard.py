from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier

import pytest
from fastapi.testclient import TestClient


from ff_agent.api import create_app
from test_handoff_progress_api import HandoffPlanner, login, prepare


@pytest.fixture
def guard(tmp_path):
    app=create_app(tmp_path/'guard.db',planner=HandoffPlanner())
    with TestClient(app) as client:
        login(client)
        _,summary=prepare(client)
        service=app.state.handoff_progress
        error=import_module(type(service).__module__).DomainError
        yield service,error,'demo',summary['plan_id']


def assert_error(error, code, status, action):
    with pytest.raises(error) as caught:
        action()
    assert caught.value.code == code
    assert caught.value.status == status


def test_atomic_rollback_if_audit_throws(guard, monkeypatch):
    service, _, user, plan = guard
    before = service.get(user, plan)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service.bank, "_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.update(user, plan, 0, "IN_REVIEW", "rollback-key")
    with service.bank.transaction() as db:
        for table in ("handoff_progress", "handoff_events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    after = service.get(user, plan)
    assert after == before
    assert (after["status"], after["version"]) == ("OPEN", 0)


def test_same_status_does_not_reserve_key(guard):
    service, error, user, plan = guard
    service.update(user, plan, 0, "IN_REVIEW", "initial-key")
    assert_error(error, "INVALID_TRANSITION", 409, lambda:
                 service.update(user, plan, 1, "IN_REVIEW", "corrected-key"))
    with service.bank.transaction() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM handoff_events WHERE request_key = ?",
            ("corrected-key",),
        ).fetchone()[0] == 0
    view = service.update(user, plan, 1, "CLOSED", "corrected-key")
    assert (view["status"], view["version"], view["replayed"]) == ("CLOSED", 2, False)
    assert len(view["events"]) == 2


def test_missing_and_foreign_plan_precede_known_key_mismatch(guard):
    service, error, user, plan = guard
    service.update(user, plan, 0, "IN_REVIEW", "known-key")
    assert_error(error, "IDEMPOTENCY_MISMATCH", 409, lambda:
                 service.update(user, plan, 0, "CLOSED", "known-key"))
    assert_error(error, "NOT_FOUND", 404, lambda:
                 service.update(user, "missing-plan", 0, "CLOSED", "known-key"))
    # Model a plan belonging to someone else, retaining the caller's known key.
    with service.bank.transaction() as db:
        db.execute("UPDATE plan_store_plans SET user = ? WHERE id = ?",
                   ("guest", plan))
        db.execute("UPDATE local_handoffs SET user_id = ? WHERE plan_id = ?",
                   ("guest", plan))
    assert_error(error, "NOT_FOUND", 404, lambda:
                 service.update(user, plan, 0, "CLOSED", "known-key"))


def test_parallel_duplicate_body_returns_two_views_one_event(guard):
    service, _, user, plan = guard
    barrier = Barrier(2)

    def update():
        barrier.wait(timeout=10)
        return service.update(user, plan, 0, "IN_REVIEW", "parallel-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update) for _ in range(2)]
        views = [future.result(timeout=15) for future in futures]
    assert sorted(view["replayed"] for view in views) == [False, True]
    assert views[0]["receipt"] == views[1]["receipt"]
    for view in views:
        assert (view["status"], view["version"]) == ("IN_REVIEW", 1)
        assert len(view["events"]) == 1
    with service.bank.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM handoff_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM handoff_progress").fetchone()[0] == 1
