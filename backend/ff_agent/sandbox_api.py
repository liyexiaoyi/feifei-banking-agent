"""Sandbox WASM runs over HTTP: guest code has no bank, network or file capabilities."""
import sys
import sysconfig
from pathlib import Path

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .bank import DomainError
from .sandbox_process import SandboxError, SandboxRunner
from .sandbox_service import SandboxService


class SandboxRunBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    wat: StrictStr = Field(max_length=32768)
    args: list[StrictInt] = Field(max_length=8)
    request_key: StrictStr = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


def _call(fn, *args):
    try:
        return fn(*args)
    except SandboxError as error:
        raise DomainError(error.code, error.message, 400) from error


def register_sandbox_api(app, bank, current_user):
    runner = SandboxRunner(
        Path(sys._base_executable),
        Path(__file__).with_name('sandbox_worker.py'),
        bank.path.parent / 'sandbox-work',
        runtime_site=Path(sysconfig.get_paths()['purelib']),
    )
    service = SandboxService(bank, runner)
    app.state.sandbox = service

    @app.get('/api/sandbox/runs')
    def sandbox_list(session=Depends(current_user)):
        return {'runs': service.list(session['user_id']), 'simulated': True}

    @app.get('/api/sandbox/by-key/{request_key}')
    def sandbox_by_key(request_key: str, session=Depends(current_user)):
        return _call(service.by_key, session['user_id'], request_key)

    @app.get('/api/sandbox/runs/{run_id}')
    def sandbox_get(run_id: str, session=Depends(current_user)):
        return _call(service.get, session['user_id'], run_id)

    @app.post('/api/sandbox/runs')
    def sandbox_run(body: SandboxRunBody, session=Depends(current_user)):
        return _call(service.run, session['user_id'], body.wat, list(body.args), body.request_key)

    return service
