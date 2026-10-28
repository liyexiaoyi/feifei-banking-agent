"""Offline source-audit counterexamples; no real model or network."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import pytest
import httpx
from ff_agent.codegen_provider import RuntimeWatGenerator
from ff_agent.codegen_types import CalcIntent,GenerationError
from ff_agent.clarification import sanitize_legacy_reply,GUARDED
CFG={'provider':{'base':'https://example.invalid','key':'test-only-credential','model':'provider-opus-test','protocol':'anthropic'}}

@pytest.mark.parametrize('kind',[httpx.ReadError,httpx.StreamClosed])
def test_response_stream_failure_after_dispatch_stays_unknown(kind):
    calls=[]
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            if False:yield b''
            raise kind('closed') if kind is httpx.ReadError else kind()
    async def handler(request):
        calls.append(request.method)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
    def mock_client(**options):
        # A configured local proxy must never bypass this test's offline MockTransport.
        options.pop('proxy',None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),**options)
    gen=RuntimeWatGenerator(ROOT,config=CFG,client_factory=mock_client)
    with pytest.raises(GenerationError) as error:gen.generate(CalcIntent('返回零',()))
    assert calls==['POST']
    assert error.value.outcome=='SENT_UNKNOWN',(kind.__name__,error.value.outcome,error.value.code)

def test_english_legacy_payment_claim_is_guarded():
    value={'message':'Transfer completed','tasks':[{'type':'clarify','reply_source':'model_chat'}]}
    result=sanitize_legacy_reply(value)
    assert result['message']==GUARDED,result

def test_benign_legacy_chat_control():
    value={'message':'Hello there','tasks':[{'type':'clarify','reply_source':'model_chat'}]}
    assert sanitize_legacy_reply(value)==value
