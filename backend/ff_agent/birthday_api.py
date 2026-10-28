import logging
from typing import Literal, Optional
from fastapi import Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

logger = logging.getLogger(__name__)
CARD_NOTICE = '流程已保存，确认卡暂时无法显示。请核对原流程，不要重复付款。'

KEY_PATTERN = r'^[A-Za-z0-9_.:-]{8,64}$'
KEY = dict(min_length=8, max_length=64, pattern=KEY_PATTERN)
FID = dict(min_length=1, max_length=64, pattern=r'^[0-9a-fA-F]{1,64}$')
VERSION = dict(ge=1)


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CreateBody(_Strict):
    recipient_label: StrictStr = Field(min_length=1, max_length=40)
    event_date: StrictStr = Field(min_length=10, max_length=10)
    account_id: StrictStr = Field(min_length=1, max_length=64)
    budget_cents: StrictInt = Field(ge=1, le=100000000)
    request_key: StrictStr = Field(**KEY)


class VersionBody(_Strict):
    expected_version: StrictInt = Field(**VERSION)
    request_key: StrictStr = Field(**KEY)


class CakeBody(VersionBody):
    cake: StrictBool


class ItemBody(VersionBody):
    gift: Literal['flowers', 'cake']
    item_id: StrictStr = Field(min_length=1, max_length=64)


class ConfirmedBody(VersionBody):
    confirmed: StrictBool

    @model_validator(mode='after')
    def _must_confirm(self):
        if self.confirmed is not True:
            raise ValueError('confirmed must be true')
        return self


def _wrap(flow):
    return {'flow': flow, 'simulated': True, 'merchant_connected': False}


def _field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def register_birthday_api(app, bank, current_user, run_tool, record_operation):
    from .birthday_service import BirthdayService
    service = BirthdayService(bank)
    app.state.birthday_service = service

    def _record_pending(user, flow):
        seen = set()
        for node in (flow or {}).get('nodes') or []:
            opid = node.get('operation_id') if isinstance(node, dict) else None
            if not isinstance(opid, str) or not opid or opid in seen:
                continue
            seen.add(opid)
            receipt = bank.receipt(user, opid)
            if _field(receipt, 'observed_status') == 'WAITING_CONFIRMATION':
                record_operation(user, _field(receipt, 'operation'))

    def _post(user, call):
        def callback():
            return call()
        flow = run_tool(user, 'transfer', callback)
        result = _wrap(flow)
        try:
            _record_pending(user, flow)
        except Exception as exc:
            logger.warning('birthday_confirmation_card_failed type=%s', type(exc).__name__)
            if isinstance(result, dict):
                result = dict(result)
                result['notice'] = CARD_NOTICE
            else:
                result = result.model_copy(update={'notice': CARD_NOTICE})
        return result

    @app.get('/api/birthday/options')
    def birthday_options_route(session=Depends(current_user)):
        user = session['user_id']
        with bank.transaction() as db:
            bank._user(db, user)
            rows = db.execute(
                'SELECT id, name, masked_number FROM accounts WHERE user_id = ? ORDER BY id',
                (user,),
            ).fetchall()
        accounts = [{'id': r[0], 'name': r[1], 'masked_number': r[2]} for r in rows]
        return {'accounts': accounts, 'business_date': bank.now().date().isoformat(), 'simulated': True, 'merchant_connected': False}

    @app.get('/api/birthday/catalog')
    def birthday_catalog_route(gift: Optional[Literal['flowers', 'cake']] = Query(None), session=Depends(current_user)):
        return {'items': service.catalog(gift)}

    @app.get('/api/birthday/flows')
    def birthday_list_route(session=Depends(current_user)):
        return {'flows': service.list(session['user_id']), 'simulated': True, 'merchant_connected': False}

    @app.get('/api/birthday/requests/{key}')
    def birthday_request_route(key: str = Path(**KEY), session=Depends(current_user)):
        return service.lookup_request(session['user_id'], key)

    @app.get('/api/birthday/flows/{flow_id}')
    def birthday_view_route(flow_id: str = Path(**FID), session=Depends(current_user)):
        return _wrap(service.get(session['user_id'], flow_id))

    @app.post('/api/birthday/flows')
    def birthday_create_route(body: CreateBody, session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.create(user, body.recipient_label, body.event_date, body.account_id, body.budget_cents, body.request_key))

    @app.post('/api/birthday/flows/{flow_id}/cake')
    def birthday_cake_route(body: CakeBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.decide_cake(user, flow_id, body.cake, body.expected_version, body.request_key))

    @app.post('/api/birthday/flows/{flow_id}/items')
    def birthday_items_route(body: ItemBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.select_item(user, flow_id, body.gift, body.item_id, body.expected_version, body.request_key))

    @app.post('/api/birthday/flows/{flow_id}/advance')
    def birthday_advance_route(body: VersionBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.advance(user, flow_id, body.expected_version, body.request_key))

    @app.post('/api/birthday/flows/{flow_id}/pause')
    def birthday_pause_route(body: VersionBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.pause(user, flow_id, body.expected_version, body.request_key))

    @app.post('/api/birthday/flows/{flow_id}/resume')
    def birthday_resume_route(body: ConfirmedBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.resume(user, flow_id, body.expected_version, body.request_key, body.confirmed))

    @app.post('/api/birthday/flows/{flow_id}/cancel')
    def birthday_cancel_route(body: ConfirmedBody, flow_id: str = Path(**FID), session=Depends(current_user)):
        user = session['user_id']
        return _post(user, lambda: service.cancel(user, flow_id, body.expected_version, body.request_key, body.confirmed))

    return service
