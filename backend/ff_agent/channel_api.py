# Local demo channel HTTP surface (FF_LOCAL_DEMO_V1).
#
# The POST receiver authenticates transport only via the existing HMAC check.
# It never reads cookies, never creates a session and grants no bank privilege.
# Owner identity for reads comes only from the existing current_user session.
# No message text, signature, raw frame or secret is logged or echoed.
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from .bank import DomainError
from .channel_bridge import ChannelBridge
from .channel_ingress import MAX_RAW, PROTOCOL, ChannelIngress

SIGNATURE_HEADER = 'x-ff-demo-signature'


def _settings(config):
    # Server-side configuration only; disabled unless enabled is exactly True.
    if config is None:
        return False, {}, {}
    if not isinstance(config, dict):
        raise ValueError('invalid channel configuration')
    if config.get('enabled') is not True:
        return False, {}, {}
    return True, config.get('keys', {}), config.get('identities', {})


def _owner(session):
    user = None
    if isinstance(session, dict):
        user = session.get('user_id')
        if user is None:
            user = session.get('user')
    if not isinstance(user, str) or not user:
        raise DomainError('SESSION_REQUIRED', '请先进入模拟体验。', 401)
    return user


def register_channel_api(app, bank, current_user, check_origin, config=None):
    enabled, keys, identities = _settings(config)
    ingress = ChannelIngress(bank, keys, identities, enabled)
    bridge = ChannelBridge(ingress)
    app.state.channel_bridge = bridge

    @app.get('/api/channel/status')
    def channel_status(session=Depends(current_user)):
        return {
            'enabled': ingress.enabled is True,
            'protocol': PROTOCOL,
            'simulated': True,
            'official_connected': False,
        }

    @app.get('/api/channel/inbox')
    def channel_inbox(session=Depends(current_user)):
        return bridge.list(_owner(session))

    @app.get('/api/channel/inbox/{inbox_id}')
    def channel_message(inbox_id: str, session=Depends(current_user)):
        return bridge.get(_owner(session), inbox_id)

    if not ingress.enabled:
        # No receiving route; the existing static mount may answer 404/405.
        return bridge

    @app.post('/api/channel/demo')
    async def channel_demo(request: Request):
        # Origin must pass the original check when present; no cookie auth here.
        check_origin(request)
        total = 0
        chunks = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_RAW:
                raise DomainError('INGRESS_SIZE', '消息过大，已拒绝。', 413)
            chunks.append(chunk)
        raw = b''.join(chunks)
        signature = request.headers.get(SIGNATURE_HEADER)
        result = await run_in_threadpool(bridge.deliver, raw, signature)
        # Re-shape to the exact receipt contract; never echo user/text/secrets.
        return {
            'inbox_id': result['inbox_id'],
            'delivery': result['delivery'],
            'untrusted_text': True,
            'effect_authorized': False,
            'simulated': True,
            'official_connected': False,
        }

    return bridge
