"""Generated-computation HTTP: opt-in, authenticated, provenance chosen only by the server."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .bank import DomainError
from .codegen_service import CodegenService
from .codegen_types import GenerationError
from .sandbox_process import SandboxError


class CalcIntentBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['calc']
    description: StrictStr = Field(min_length=1, max_length=300)
    args: list[StrictInt] = Field(max_length=8)


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: CalcIntentBody
    # Exact ASCII key contract shared with the store and sandbox; the store re-validates.
    generation_key: StrictStr = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]{8,100}$')
    consent: StrictBool


def _invalid():
    # Generic public message: raw parser/generator/sandbox text is never echoed.
    return DomainError('INVALID_GENERATION_REQUEST', '计算请求无效，请检查后重试。', 400)


def _call(fn, *args):
    try:
        return fn(*args)
    except DomainError:
        raise
    except (GenerationError, SandboxError, ValueError):
        raise _invalid() from None


def register_codegen_api(app, bank, current_user, sandbox, guard, generator=None, allow_fixture=False):
    # Default generator=None keeps the app healthy with generation unavailable.
    service = CodegenService(bank, sandbox, generator=generator, guard=guard, allow_fixture=allow_fixture)
    app.state.codegen = service

    @app.get('/api/sandbox/generation-status')
    def generation_status(session=Depends(current_user)):
        available = bool(service.available)
        return {
            'available': available,
            'provenance': service._provenance if available else None,
            'simulated': True,
        }

    @app.post('/api/sandbox/generate')
    def generate(body: GenerateBody, session=Depends(current_user)):
        if body.consent is not True:
            raise DomainError('CONSENT_REQUIRED', '请先明确同意后再生成计算。', 400)
        intent = body.intent.model_dump()
        intent['args'] = list(intent['args'])
        return _call(service.generate_and_run, session['user_id'], intent, body.generation_key)

    @app.get('/api/sandbox/generate/by-key/{generation_key}')
    def generation_by_key(generation_key: str, session=Depends(current_user)):
        return _call(service.store.by_key, session['user_id'], generation_key)

    @app.get('/api/sandbox/generations/{generation_id}')
    def generation_get(generation_id: str, session=Depends(current_user)):
        return _call(service.store.get, session['user_id'], generation_id)

    @app.get('/api/sandbox/generations')
    def generation_list(session=Depends(current_user)):
        return {'generations': _call(service.store.list, session['user_id']), 'simulated': True}

    return service
