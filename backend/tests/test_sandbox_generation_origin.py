"""A generation cannot adopt a direct run or execute a reserved source twice."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.sandbox_service import SandboxService

WAT = '(module (func (export "run") (result i64) i64.const 713))'
GEN = 'cgen_' + 'a' * 32


class Runner:
    def __init__(self):
        self.calls = 0
    def run(self, wat, args):
        self.calls += 1
        return {'ok': True, 'result': 713, 'fuel_used': 2}


@pytest.fixture
def env(tmp_path):
    bank = Bank(tmp_path / 'generated.db'); runner = Runner()
    return bank, runner, SandboxService(bank, runner)


def reserve(env, key='generated-key-001', gen=GEN):
    bank, _, service = env
    assert hasattr(service, 'reserve_generated'), 'atomic generation origin reservation missing'
    with bank.transaction() as db:
        return service.reserve_generated(db, 'demo', WAT, [], key, gen)


def execute(env, receipt, **kwargs):
    _, _, service = env
    return service.execute_generated(kwargs.get('user', 'demo'), receipt['run_id'],
        kwargs.get('token', receipt['token']), kwargs.get('wat', WAT), [], kwargs.get('generation_id', GEN))


def test_origin_reservation_rolls_back_with_callers_transaction(env):
    bank, runner, service = env
    assert hasattr(service, 'reserve_generated'), 'atomic generation origin reservation missing'
    with pytest.raises(RuntimeError):
        with bank.transaction() as db:
            service.reserve_generated(db, 'demo', WAT, [], 'generated-key-rollback', GEN)
            raise RuntimeError('rollback transaction')
    with bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM sandbox_runs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM sandbox_generation_links').fetchone()[0] == 0
    assert runner.calls == 0


def test_one_internal_origin_capability_runs_once_without_persisting_token(env):
    bank, runner, service = env; cap = reserve(env)
    assert set(cap) == {'run_id', 'token'} and len(cap['token']) >= 64
    with bank.transaction() as db:
        row = db.execute('SELECT * FROM sandbox_generation_links').fetchone()
        assert row['token_sha256'] == hashlib.sha256(cap['token'].encode()).hexdigest()
        assert cap['token'] not in str(tuple(row)) and row['execution_claimed'] == 0
    a = execute(env, cap); b = execute(env, cap)
    assert a == b and a['status'] == 'SUCCEEDED' and runner.calls == 1
    assert cap['token'] not in str(service.list('demo'))


@pytest.mark.parametrize('kwargs', [{'user': 'guest'}, {'token': 'wrong'},
    {'wat': WAT + ' '}, {'generation_id': 'cgen_' + 'b' * 32}])
def test_owner_token_source_and_generation_must_all_match(env, kwargs):
    _, runner, _ = env; cap = reserve(env)
    with pytest.raises(DomainError) as error:
        execute(env, cap, **kwargs)
    assert error.value.status in (404, 409) and runner.calls == 0
    assert execute(env, cap)['status'] == 'SUCCEEDED' and runner.calls == 1


def test_generation_never_adopts_existing_direct_receipt_even_with_identical_frame(env):
    _, runner, service = env
    service.run('demo', WAT, [], 'generated-key-001')
    with pytest.raises(DomainError) as error:
        reserve(env)
    assert error.value.status == 409 and runner.calls == 1


def test_reserved_origin_already_claimed_before_crash_cannot_spawn_again(env):
    bank, runner, service = env; cap = reserve(env)
    with bank.transaction() as db:
        db.execute('UPDATE sandbox_generation_links SET execution_claimed=1')
        db.execute('UPDATE sandbox_runs SET created_epoch=?', (bank.now().timestamp()-120,))
    assert execute(env, cap)['status'] == 'UNKNOWN' and runner.calls == 0
    assert service.get('demo', cap['run_id'])['status'] == 'UNKNOWN'


def test_concurrent_internal_capability_only_one_runner_and_no_db_lock_while_running(env):
    _, runner, _ = env; cap = reserve(env)
    entered = threading.Event(); release = threading.Event(); original = runner.run
    def slow(wat, args):
        entered.set(); assert release.wait(3)
        return original(wat, args)
    runner.run = slow
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(execute, env, cap); assert entered.wait(2)
        try:
            second = pool.submit(execute, env, cap).result(1)
            assert second['status'] == 'RUNNING'
        finally:
            release.set()
        assert first.result(2)['status'] == 'SUCCEEDED'
    assert runner.calls == 1


def test_reservation_rejects_autocommit_connection(env):
    import sqlite3
    bank, runner, service = env
    assert hasattr(service, 'reserve_generated'), 'atomic generation origin reservation missing'
    with sqlite3.connect(bank.path, isolation_level=None) as db:
        with pytest.raises((DomainError, RuntimeError, ValueError)):
            service.reserve_generated(db, 'demo', WAT, [], 'generated-key-autocommit', GEN)
    assert runner.calls == 0 and service.list('demo') == []
