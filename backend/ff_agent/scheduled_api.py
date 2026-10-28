"""LOCAL SIMULATED scheduled transfer routes (B3).

POST routes only prepare, cancel or explicitly drive due schedules for the
session user. Confirmation/verification use the existing common operation
endpoints; this module never confirms. Reads never trigger due work. No bank,
SMS or IM connection exists.
"""
from typing import Literal
from fastapi import Depends, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

KEY_PATTERN = r'^[A-Za-z0-9_-]{8,64}$'
KEY = dict(min_length=8, max_length=64, pattern=KEY_PATTERN)
ID = dict(min_length=1, max_length=64)


class SchedulePrepareBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    payee_id: StrictStr = Field(**ID)
    amount_cents: StrictInt = Field(ge=1, le=100000000)
    due_at: StrictStr = Field(min_length=1, max_length=64)
    request_key: StrictStr = Field(**KEY)


class ScheduleCancelBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(**KEY)
    confirmed: Literal[True]


class RunDueBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def register_scheduled_api(app, bank, current_user, run_tool, record_operation):
    from .scheduled_transfers import ScheduledTransfers
    from .scheduled_driver import ScheduledDriver
    service = ScheduledTransfers(bank)
    driver = ScheduledDriver(bank)
    app.state.scheduled_transfers = service
    app.state.scheduled_driver = driver

    @app.get('/api/scheduled-transfers')
    def scheduled_list_route(session=Depends(current_user)):
        return {'schedules': service.list(session['user_id']), 'simulated': True}

    @app.post('/api/scheduled-transfers')
    def scheduled_prepare_route(body: SchedulePrepareBody, session=Depends(current_user)):
        user = session['user_id']
        item = run_tool(user, 'transfer', lambda: service.prepare(user, body.payee_id, body.amount_cents, body.due_at, body.request_key))
        record_operation(user, item['operation'])
        return item

    @app.post('/api/scheduled-transfers/run-due')
    def scheduled_run_due_route(body: RunDueBody, session=Depends(current_user)):
        user = session['user_id']
        result = run_tool(user, 'transfer', lambda: driver.run_due(user))
        for entry in result.get('processed', []):
            child_id = entry.get('child_operation_id')
            if not child_id:
                continue
            operation = bank.receipt(user, child_id)['operation']
            if operation and operation.get('status') == 'WAITING_CONFIRMATION':
                record_operation(user, operation)
        return result

    @app.get('/api/scheduled-transfers/requests/{key}')
    def scheduled_request_route(key: str = Path(**KEY), session=Depends(current_user)):
        return service.lookup_request(session['user_id'], key)

    @app.get('/api/scheduled-transfers/{schedule_id}')
    def scheduled_view_route(schedule_id: str = Path(**ID), session=Depends(current_user)):
        return service.get(session['user_id'], schedule_id)

    @app.post('/api/scheduled-transfers/{schedule_id}/cancel')
    def scheduled_cancel_route(body: ScheduleCancelBody, schedule_id: str = Path(**ID), session=Depends(current_user)):
        user = session['user_id']
        return run_tool(user, 'transfer', lambda: service.cancel(user, schedule_id, body.expected_version, body.request_key, body.confirmed))
