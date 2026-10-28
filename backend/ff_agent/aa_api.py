"""Personal local AA requests: no messages, collections or bank transfers are sent."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from .aa_requests import AAService, mask_phone

class AAPreviewBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    total_cents: StrictInt = Field(ge=1, le=100000000)
    participant_ids: list[StrictStr] = Field(min_length=1, max_length=20)
    include_self: StrictBool

class AACreateBody(AAPreviewBody):
    request_key: StrictStr = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    confirmed: StrictBool
    @field_validator('confirmed')
    @classmethod
    def consent(cls, value):
        if value is not True:
            raise ValueError('explicit consent required')
        return value

class AACancelBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')

def register_aa_api(app, bank, current_user):
    service = AAService(bank)
    app.state.aa = service

    @app.get('/api/aa')
    def listing(session=Depends(current_user)):
        return {'requests': service.list(session['user_id']), 'simulated': True}

    @app.get('/api/aa/participants')
    def participants(session=Depends(current_user)):
        with bank.transaction() as db:
            bank._user(db, session['user_id'])
            rows = db.execute('SELECT id,name,phone FROM payees WHERE user_id=? ORDER BY rowid', (session['user_id'],)).fetchall()
            return {'participants': [{'id': p['id'], 'name': p['name'], 'masked_phone': mask_phone(p['phone'])} for p in rows], 'simulated': True}

    @app.post('/api/aa/preview')
    def preview(body: AAPreviewBody, session=Depends(current_user)):
        return service.preview(session['user_id'], body.total_cents, body.participant_ids, body.include_self)

    @app.post('/api/aa')
    def create(body: AACreateBody, session=Depends(current_user)):
        return service.create(session['user_id'], body.total_cents, body.participant_ids, body.include_self, body.request_key, body.confirmed)

    @app.get('/api/aa/{request_id}')
    def detail(request_id: str, session=Depends(current_user)):
        return service.get(session['user_id'], request_id)

    @app.post('/api/aa/{request_id}/cancel')
    def cancel(request_id: str, body: AACancelBody, session=Depends(current_user)):
        return service.cancel(session['user_id'], request_id, body.expected_version, body.request_key)
