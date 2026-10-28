# Operator review endpoints: separate credentials, never a payment authority.
from typing import Annotated

from fastapi import Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .operator_auth import LIFETIME, OperatorAuth
from .operator_cases import OperatorCases

COOKIE = 'ff_operator'
COOKIE_PATH = '/api/operator'
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')
KEY_PATTERN = r'^[A-Za-z0-9_-]{8,64}$'

CaseId = Annotated[str, Path(min_length=1, max_length=128)]
PlanId = Annotated[str, Path(min_length=1, max_length=128)]
Version = Annotated[StrictInt, Field(ge=0)]
RequestKey = Annotated[StrictStr, Field(min_length=8, max_length=64, pattern=KEY_PATTERN)]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LoginBody(StrictBody):
    access_token: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


class VersionBody(StrictBody):
    expected_version: Version
    request_key: RequestKey


class ReplyBody(VersionBody):
    reply: Annotated[StrictStr, Field(min_length=1, max_length=500)]


class ShareBody(VersionBody):
    confirmed: StrictBool

    @field_validator('confirmed')
    @classmethod
    def must_confirm(cls, value):
        if value is not True:
            raise ValueError('explicit confirmation required')
        return value


def _session_view(data):
    return {'operator_id': data['operator_id'], 'csrf': data['csrf'],
            'expires_at': data['expires_at'], 'simulated': True}


def register_operator_api(app, bank, current_user, check_origin):
    auth = OperatorAuth(bank)
    cases = OperatorCases(bank)
    app.state.operator_auth = auth
    app.state.operator_cases = cases

    def current_operator(request: Request):
        # Only the operator cookie is consulted; the user ff_session never grants operator access.
        write = request.method not in SAFE_METHODS
        if write:
            check_origin(request)
        return auth.session(request.cookies.get(COOKIE, ''), request.headers.get('x-csrf-token'), write=write)

    @app.post('/api/operator/session')
    def operator_login(body: LoginBody, request: Request, response: Response):
        check_origin(request)
        data = auth.login(body.access_token, request.cookies.get(COOKIE, ''))
        response.set_cookie(COOKIE, data['token'], httponly=True, samesite='strict', max_age=LIFETIME,
                            path=COOKIE_PATH, secure=request.url.scheme == 'https')
        return _session_view(data)

    @app.get('/api/operator/session')
    def operator_session(operator=Depends(current_operator)):
        return _session_view(operator)

    @app.delete('/api/operator/session')
    def operator_logout(request: Request, response: Response):
        check_origin(request)
        auth.logout(request.cookies.get(COOKIE, ''), request.headers.get('x-csrf-token'))
        response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=request.url.scheme == 'https',
                               httponly=True, samesite='strict')
        return {'revoked': True, 'simulated': True}

    @app.get('/api/operator/cases')
    def operator_cases(_operator=Depends(current_operator)):
        return {'cases': cases.list_operator()}

    @app.get('/api/operator/cases/{case_id}')
    def operator_case(case_id: CaseId, _operator=Depends(current_operator)):
        return cases.get_operator(case_id)

    @app.post('/api/operator/cases/{case_id}/claim')
    def operator_claim(case_id: CaseId, body: VersionBody, _operator=Depends(current_operator)):
        return cases.act_operator(case_id, body.expected_version, 'claim', None, body.request_key)

    @app.post('/api/operator/cases/{case_id}/reply')
    def operator_reply(case_id: CaseId, body: ReplyBody, _operator=Depends(current_operator)):
        return cases.act_operator(case_id, body.expected_version, 'reply', body.reply, body.request_key)

    @app.get('/api/operator-cases')
    def user_cases(session=Depends(current_user)):
        return {'cases': cases.list_user(session['user_id'])}

    @app.get('/api/operator-cases/{case_id}')
    def user_case(case_id: CaseId, session=Depends(current_user)):
        return cases.get_user(session['user_id'], case_id)

    @app.post('/api/operator-cases/{case_id}/withdraw')
    def user_withdraw(case_id: CaseId, body: VersionBody, session=Depends(current_user)):
        return cases.withdraw(session['user_id'], case_id, body.expected_version, body.request_key)

    @app.post('/api/plans/{plan_id}/operator-case')
    def user_share(plan_id: PlanId, body: ShareBody, session=Depends(current_user)):
        return cases.share(session['user_id'], plan_id, body.expected_version, body.request_key,
                           confirmed=body.confirmed)

    @app.get('/api/plans/{plan_id}/operator-case')
    def lookup_case(plan_id: PlanId, session=Depends(current_user)):
        return {'case': cases.get_for_plan(session['user_id'], plan_id)}

    @app.get('/api/plans/{plan_id}/operator-case/preview')
    def share_preview(plan_id: PlanId, session=Depends(current_user)):
        return {'preview': cases.preview(session['user_id'], plan_id)}

    return auth, cases
