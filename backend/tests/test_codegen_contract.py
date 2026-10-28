"""Generated programs stay pure computations; the model sees no argument values."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
WAT = '(module (func (export "run") (param i64) (result i64) local.get 0))'


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    # Explicit HTTPX proxy mounts take precedence over MockTransport. All tests
    # must stay offline even when this machine has a saved workroom proxy.
    monkeypatch.setenv('FF_RUNTIME_PROXY_URL', '')


def module(name):
    assert importlib.util.find_spec('ff_agent.' + name), name + ' must be implemented'
    return importlib.import_module('ff_agent.' + name)


def test_intent_hash_is_canonical_but_binds_values_and_description():
    m = module('codegen_types')
    a = m.parse_intent({'kind': 'calc', 'description': '返回参数', 'args': [713]})
    assert a.args == (713,) and len(m.intent_sha256(a)) == 64
    assert m.intent_sha256(a) == m.intent_sha256(m.CalcIntent('返回参数', (713,)))
    assert m.intent_sha256(a) != m.intent_sha256(m.CalcIntent('返回参数', (714,)))
    with pytest.raises((AttributeError, TypeError)):
        a.description = 'changed'


@pytest.mark.parametrize('data', [
    {'kind': 'transfer', 'description': '转账', 'args': []},
    {'kind': 'calc', 'description': ' ', 'args': []},
    {'kind': 'calc', 'description': 'x' * 301, 'args': []},
    {'kind': 'calc', 'description': '\ud800', 'args': []},
    {'kind': 'calc', 'description': 'x', 'args': [True]},
    {'kind': 'calc', 'description': 'x', 'args': [2 ** 63]},
    {'kind': 'calc', 'description': 'x', 'args': [0] * 9},
    {'kind': 'calc', 'description': 'x', 'args': [], 'amount': 1},
    {'kind': 'calc', 'description': 'x', 'args': ()},
])
def test_invalid_intent_has_no_side_effects(data):
    m = module('codegen_types')
    with pytest.raises((ValueError, m.GenerationError)):
        m.parse_intent(data)


def test_direct_constructor_cannot_bypass_parameter_validation():
    m = module('codegen_types')
    for description, args in [('x', (True,)), ('', ()), ('x', [1]), ('x', (2 ** 63,))]:
        with pytest.raises((ValueError, m.GenerationError)):
            m.CalcIntent(description, args)


@pytest.mark.parametrize('data,count', [
    ({'wat': WAT, 'arg_names': ['x'], 'provenance': 'model'}, 1),
    ({'wat': WAT, 'arg_names': ['x', 'x']}, 2),
    ({'wat': WAT, 'arg_names': ['中文']}, 1),
    ({'wat': WAT, 'arg_names': ['x']}, 0),
    ({'wat': 'x' * 32769, 'arg_names': []}, 0),
    ({'wat': '\ud800', 'arg_names': []}, 0),
])
def test_generated_envelope_rejects_extra_fields_or_invalid_shape(data, count):
    m = module('codegen_types')
    with pytest.raises((ValueError, m.GenerationError)):
        m.parse_generated(data, count)


def test_generated_metadata_is_server_only_and_not_shared():
    m = module('codegen_types')
    a = m.parse_generated({'wat': WAT, 'arg_names': ['x']}, 1, {'http_status': 200})
    b = m.parse_generated({'wat': WAT, 'arg_names': ['x']}, 1)
    a.meta['private'] = 'test'
    assert 'private' not in b.meta and a.arg_names == ('x',)


def provider(handler, role='provider', model='neutral-test-model'):
    m = module('codegen_provider')
    options = []
    def factory(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    cfg = {role: {'base': 'https://example.invalid', 'key': 'dummy-test-credential',
                  'model': model, 'protocol': 'anthropic'}}
    return m.RuntimeWatGenerator(ROOT, role=role, config=cfg, client_factory=factory), options


def message(data):
    return httpx.Response(200, json={'id': 'msg_test', 'type': 'message', 'role': 'assistant',
        'model': 'neutral-test-model', 'content': [{'type': 'text', 'text': json.dumps(data)}],
        'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 20}})


def test_provider_real_transport_once_and_does_not_send_argument_values():
    t = module('codegen_types'); requests = []
    def handler(request):
        requests.append(request)
        return message({'wat': WAT, 'arg_names': ['value']})
    gen, opts = provider(handler)
    assert gen.available and gen.provenance == 'fixture' and not requests
    out = gen.generate(t.CalcIntent('返回第一个整数参数', (918273645123,)))
    assert out.wat == WAT and out.arg_names == ('value',) and len(requests) == 1
    body = requests[0].content.decode()
    assert '918273645123' not in body and 'arg_count' in body
    assert requests[0].url.scheme == 'https'
    assert opts[0]['trust_env'] is False and opts[0]['follow_redirects'] is False
    assert opts[0].get('verify', True) is True


@pytest.mark.parametrize('kind,outcome', [('524', 'SENT_UNKNOWN'), ('read', 'SENT_UNKNOWN'),
                                       ('429', 'FAILED'), ('bad', 'FAILED'), ('redirect', 'FAILED')])
def test_provider_terminal_delivery_classification_and_no_retry(kind, outcome):
    t = module('codegen_types'); calls = []
    def handler(request):
        calls.append(request)
        if kind == 'read':
            raise httpx.ReadTimeout('private-canary', request=request)
        if kind == 'bad':
            return message({'wat': WAT, 'arg_names': [], 'secret': 'private-canary'})
        if kind == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://other.invalid'})
        return httpx.Response(int(kind))
    gen, _ = provider(handler)
    with pytest.raises(t.GenerationError) as error:
        gen.generate(t.CalcIntent('返回零', ()))
    assert error.value.outcome == outcome and len(calls) == 1
    assert 'private-canary' not in str(error.value)


@pytest.mark.parametrize('role,model', [('auxiliary_a', 'neutral-test-model'), ('provider', ' ')])
def test_unauthorized_role_or_blank_model_never_calls_http(role, model):
    t = module('codegen_types'); calls = []
    gen, _ = provider(lambda request: calls.append(request), role, model)
    assert not gen.available
    with pytest.raises(t.GenerationError):
        gen.generate(t.CalcIntent('返回零', ()))
    assert not calls


def test_server_model_id_is_opaque_but_role_and_shape_checked():
    t = module('codegen_types'); calls = []
    def handler(request):
        calls.append(request)
        return message({'wat': WAT, 'arg_names': ['value']})
    gen, _ = provider(handler, 'provider', 'neutral-test-model')
    assert gen.available and not calls
    out = gen.generate(t.CalcIntent('返回第一个整数参数', (7,)))
    assert out.wat == WAT and out.arg_names == ('value',) and len(calls) == 1
    for bad in (None, '', ' ', 'a\nb', 'a\rb', 'm' * 201):
        gen, _ = provider(handler, 'provider', bad)
        assert not gen.available
        with pytest.raises(t.GenerationError):
            gen.generate(t.CalcIntent('返回零', ()))
    assert len(calls) == 1
