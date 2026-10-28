"""The normal launcher opts into Provider codegen without performing a paid call."""
import importlib
from pathlib import Path
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    main = importlib.import_module('ff_agent.__main__')
    provider = importlib.import_module('ff_agent.codegen_provider')
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    for name in ('FF_CODEGEN_MODE', 'FF_CODEGEN_ROLE'):
        monkeypatch.delenv(name, raising=False)
    calls = []; generators = []
    def app(**kwargs):
        calls.append(kwargs)
        return kwargs
    class Generator:
        def __init__(self, root, role='provider'):
            self.root, self.role = Path(root), role
            generators.append(self)
        def generate(self, *args, **kwargs):
            raise AssertionError('launch must not call a model')
    monkeypatch.setattr(main, 'create_app', app)
    monkeypatch.setattr(provider, 'RuntimeWatGenerator', Generator)
    monkeypatch.setattr(main.uvicorn, 'run', lambda *a, **k: pytest.fail('no listener during factory tests'))
    return main, tmp_path, calls, generators


def build(env, **kwargs):
    main = env[0]
    assert hasattr(main, 'build_app'), 'normal launch has no explicit codegen configuration'
    return main.build_app(**kwargs)


def test_default_factory_is_off_and_constructs_no_generator(env):
    _, root, calls, generators = env
    result = build(env, db_path=root / 'demo.db')
    assert result['codegen_generator'] is None and not generators and len(calls) == 1
    assert result['db_path'] == root / 'demo.db' and not result.get('allow_codegen_fixture', False)


@pytest.mark.parametrize('role', ['provider', 'provider-secondary', 'provider-pc'])
def test_server_env_opt_in_uses_only_configured_provider_role_without_sending(env, monkeypatch, role):
    main, root, calls, generators = env
    monkeypatch.setenv('FF_CODEGEN_MODE', 'provider')
    monkeypatch.setenv('FF_CODEGEN_ROLE', role)
    result = build(env)
    assert len(generators) == len(calls) == 1 and result['codegen_generator'] is generators[0]
    assert generators[0].role == role and generators[0].root == root
    assert not result.get('allow_codegen_fixture', False)


@pytest.mark.parametrize('mode,role', [('planner', 'provider'), ('provider', 'auxiliary_a'), ('provider', 'unknown')])
def test_unsupported_mode_or_non_provider_role_fails_before_app_or_http(env, monkeypatch, mode, role):
    _, _, calls, generators = env
    monkeypatch.setenv('FF_CODEGEN_MODE', mode)
    monkeypatch.setenv('FF_CODEGEN_ROLE', role)
    with pytest.raises(ValueError):
        build(env)
    assert not calls and not generators


def test_project_env_loads_codegen_options_but_preserves_process_override(env, monkeypatch):
    main, root, calls, generators = env
    (root / '.env').write_text('FF_CODEGEN_MODE=provider\nFF_CODEGEN_ROLE=provider-secondary\n', encoding='utf8')
    build(env)
    assert generators[0].role == 'provider-secondary'
    monkeypatch.setenv('FF_CODEGEN_MODE', 'off')
    result = build(env)
    assert result['codegen_generator'] is None and len(generators) == 1


@pytest.mark.parametrize('mode', ['off', 'provider'])
def test_real_factory_without_private_configuration_starts_but_does_not_call_model(tmp_path, monkeypatch, mode):
    import os
    from fastapi.testclient import TestClient
    main = importlib.import_module('ff_agent.__main__')
    provider = importlib.import_module('ff_agent.codegen_provider')
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    for name in list(os.environ):
        if name.startswith('FF_'):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    monkeypatch.setenv('FF_CODEGEN_MODE', mode)
    monkeypatch.setattr(provider.RuntimeWatGenerator, 'generate', lambda *a, **k: pytest.fail('startup must not call model'))
    with TestClient(main.build_app(db_path=tmp_path / (mode + '.db'))) as client:
        assert client.get('/api/health').status_code == 200
        assert client.post('/api/session', json={'user': 'demo'}).status_code == 200
        response = client.get('/api/sandbox/generation-status')
        assert response.status_code == 200
        assert response.json() == {'available': False, 'provenance': None, 'simulated': True}
