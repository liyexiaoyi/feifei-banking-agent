"""Card controls and LOCAL SIMULATED card spend preparation routes.

POST routes only prepare operations; confirmation/verification use the existing
common operation endpoints. No bank or merchant is contacted.
"""
from typing import Union
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

KEY = dict(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


class ControlPrepareBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: StrictStr = Field(min_length=1, max_length=64)
    value: Union[StrictBool, StrictInt]
    expected_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(**KEY)


class SpendPrepareBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    merchant_id: StrictStr = Field(min_length=1, max_length=64)
    amount_cents: StrictInt = Field(ge=1, le=100000000)
    expected_card_version: StrictInt = Field(ge=1)
    expected_control_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(**KEY)


def register_card_spend_api(app, bank, current_user, run_tool, record_operation):
    from .card_controls import CardControls
    from .card_spend import CardSpend
    controls_service = CardControls(bank)
    spend_service = CardSpend(bank)
    app.state.card_controls = controls_service
    app.state.card_spend = spend_service

    @app.get('/api/card-controls')
    def card_controls_listing(session=Depends(current_user)):
        return {'controls': controls_service.list_controls(session['user_id']), 'simulated': True}

    @app.get('/api/cards/{card_id}/controls')
    def card_controls_view(card_id: str, session=Depends(current_user)):
        return controls_service.get_controls(session['user_id'], card_id)

    @app.post('/api/cards/{card_id}/controls')
    def card_controls_prepare_route(card_id: str, body: ControlPrepareBody, session=Depends(current_user)):
        user = session['user_id']
        operation = run_tool(user, 'cards', lambda: controls_service.prepare_control(user, card_id, body.action, body.value, body.expected_version, body.request_key))
        record_operation(user, operation)
        return operation

    @app.get('/api/card-spend/merchants')
    def card_spend_merchants_route(session=Depends(current_user)):
        return {'merchants': spend_service.merchants(), 'simulated': True}

    @app.post('/api/cards/{card_id}/spend')
    def card_spend_prepare_route(card_id: str, body: SpendPrepareBody, session=Depends(current_user)):
        user = session['user_id']
        operation = run_tool(user, 'cards', lambda: spend_service.prepare(user, card_id, body.merchant_id, body.amount_cents, body.expected_card_version, body.expected_control_version, body.request_key))
        record_operation(user, operation)
        return operation
