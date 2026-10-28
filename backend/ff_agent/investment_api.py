"""Personal demo assessments and comparisons; these endpoints never trade."""
from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .bank import DomainError
from .investment_profile import ProfileService
from .wealth_compare import WealthComparison


class AssessmentBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answers: dict[str, StrictInt]
    survey_version: StrictStr = Field(min_length=1, max_length=64)
    expected_version: StrictInt = Field(ge=0)
    request_key: StrictStr = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    confirmed: StrictBool

    @field_validator('confirmed')
    @classmethod
    def require_consent(cls, value):
        if value is not True:
            raise ValueError('explicit consent required')
        return value


def register_investment_api(app, bank, current_user):
    profiles = ProfileService(bank)
    comparison = WealthComparison(bank)
    app.state.investment_profiles = profiles

    @app.get('/api/investment-profile/questionnaire')
    def questionnaire(_session=Depends(current_user)):
        return profiles.questionnaire()

    @app.get('/api/investment-profile')
    def profile(session=Depends(current_user)):
        return profiles.get(session['user_id'])

    @app.post('/api/investment-profile')
    def assess(body: AssessmentBody, session=Depends(current_user)):
        return profiles.submit(session['user_id'], body.answers, body.survey_version,
                               body.expected_version, body.request_key, body.confirmed)

    @app.get('/api/wealth/compare')
    def compare(request: Request, session=Depends(current_user)):
        pairs = request.query_params.multi_items()
        if any(key != 'product' for key, _ in pairs):
            raise DomainError('INVALID_COMPARISON_QUERY', '只支持选择演示产品进行比较。', 422)
        return comparison.compare(session['user_id'], [value for _, value in pairs] if pairs else None)
