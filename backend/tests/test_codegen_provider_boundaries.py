import asyncio
import importlib
import time
import httpx
import pytest
from pathlib import Path
from ff_agent.codegen_provider import RuntimeWatGenerator
from ff_agent.codegen_types import CalcIntent, GenerationError
ROOT=Path(__file__).resolve().parents[2]
CFG={'provider':{'base':'https://example.invalid','key':'test-only-credential','model':'provider-opus-test','protocol':'anthropic'}}

@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):monkeypatch.setenv('FF_RUNTIME_PROXY_URL','')

def test_default_settings_use_actual_registry_entrypoint_without_http(monkeypatch):
    import sys
    sys.path.insert(0,str(ROOT/'scripts'));reg=importlib.import_module('model_runtime.registry');calls=[]
    class Registry:
        def transport_config(self):return CFG
    def loader(env,path):calls.append(path);return Registry()
    monkeypatch.setattr(reg,'load_registry',loader)
    gen=RuntimeWatGenerator(ROOT)
    assert gen.available and calls==[ROOT/'runtime-models.json']

@pytest.mark.parametrize('base',['https://example.invalid?credential=x','https://example.invalid#fragment','http://example.invalid','https://u:p@example.invalid'])
def test_invalid_endpoint_is_unavailable_before_network(base):
    config={'provider':{**CFG['provider'],'base':base}}
    assert not RuntimeWatGenerator(ROOT,config=config).available

def test_wall_timeout_is_unknown_with_one_request_and_no_retry():
    calls=[]
    async def handler(request):calls.append(request);await asyncio.sleep(.25);return httpx.Response(200,json={})
    gen=RuntimeWatGenerator(ROOT,config=CFG,client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))
    started=time.monotonic()
    with pytest.raises(GenerationError) as error:gen.generate(CalcIntent('返回零',()),timeout_s=.02)
    assert error.value.outcome=='SENT_UNKNOWN' and len(calls)==1 and time.monotonic()-started<.2

def test_global_runner_lock_rejects_paid_call_without_opening_client():
    gen=RuntimeWatGenerator(ROOT,config=CFG);local=gen._modules()['local']
    with local.single_runner(ROOT/'.cache/model-runtime/run.lock'):
        with pytest.raises(GenerationError) as error:gen.generate(CalcIntent('返回零',()))
    assert error.value.outcome=='FAILED' and error.value.code=='WORKROOM_BUSY'
