"""Credential derivation must not run on the ASGI event loop thread."""
import asyncio
import threading

import httpx
from ff_agent.api import create_app
from ff_agent.bank import DomainError


def test_credential_prepare_uses_worker_thread_after_async_body_validation(tmp_path,monkeypatch):
    app=create_app(tmp_path/'worker.db')
    called=[]
    def probe(*args,**kwargs):
        called.append(threading.get_ident())
        raise DomainError('THREAD_PROBE','诊断已停止。',400)
    monkeypatch.setattr(app.state.card_credentials,'prepare',probe)

    async def exercise():
        loop_thread=threading.get_ident()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                session=await client.post('/api/session',json={'user':'demo'})
                assert session.status_code==200
                response=await client.post('/api/cards/card_a_daily/credential',
                    headers={'X-CSRF-Token':session.json()['csrf']},
                    json={'new_secret':'OnlyDemo_new-583902','expected_version':0,'request_key':'loop-probe-01'})
                assert response.status_code==400 and response.json()['error']['code']=='THREAD_PROBE'
        assert len(called)==1 and called[0]!=loop_thread,'CPU-bound credential work blocked the ASGI event loop'
    asyncio.run(exercise())
