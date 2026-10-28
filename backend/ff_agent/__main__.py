import os
from pathlib import Path

import uvicorn

from .api import ROOT, create_app


def load_environment():
    path=ROOT/'.env'
    allowed={'FF_PLANNER_MODE','FF_MODEL_API_KEY','FF_MODEL_BASE_URL','FF_MODEL_NAME','FF_PORT',
             'FF_AUX_MODEL_API_KEY','FF_AUX_MODEL_BASE_URL','FF_AUX_MODEL_NAME',
             'FF_MODEL_REASONING_EFFORT','FF_AUX_MODEL_REASONING_EFFORT',
             'FF_COORD_MODEL_API_KEY','FF_COORD_MODEL_BASE_URL','FF_COORD_MODEL_NAME','FF_COORD_MODEL_REASONING_EFFORT',
             'FF_CODEGEN_MODE','FF_CODEGEN_ROLE','FF_ALLOW_MODEL_SETTINGS'}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                if key.strip() in allowed:
                    os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))


CODEGEN_ROLES=('provider','provider-secondary','provider-pc')


def build_app(db_path=None):
    # Server-side explicit opt-in only; constructing the generator performs no HTTP.
    load_environment()
    mode=os.getenv('FF_CODEGEN_MODE','off').strip()
    if mode not in ('off','provider'):
        raise ValueError('unsupported codegen mode')
    gen=None
    if mode=='provider':
        role=os.getenv('FF_CODEGEN_ROLE','provider').strip()
        if role not in CODEGEN_ROLES:
            raise ValueError('unsupported codegen role')
        from .codegen_provider import RuntimeWatGenerator
        gen=RuntimeWatGenerator(ROOT,role=role)
    return create_app(db_path=db_path,codegen_generator=gen)


if __name__=='__main__':
    # build_app() is evaluated first so .env is loaded before FF_PORT is read.
    uvicorn.run(build_app(),host='127.0.0.1',port=int(os.getenv('FF_PORT','8765')))
