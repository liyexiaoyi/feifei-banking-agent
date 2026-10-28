import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from ff_agent.bank import DomainError
from ff_agent import investment_profile
from ff_agent.investment_profile import ProfileService
from ff_agent.memory import Memory
from test_investment_profile_api import case, body, submit, finances, ANSWERS


def test_concurrent_submissions_with_same_base_version_have_one_winner(case):
    app, _ = case
    service = app.state.investment_profiles
    before = finances(app.state.bank)
    def apply(key):
        try:
            return service.submit('demo', ANSWERS, 'demo-risk-v1', 0, key, True)['profile']['version']
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ('race-profile-key-1', 'race-profile-key-2')))
    assert results.count(1) == 1 and results.count('VERSION_CONFLICT') == 1
    assert finances(app.state.bank) == before


def test_require_rejects_missing_changed_and_expired_profiles(case):
    app, client = case
    profiles = app.state.investment_profiles
    with app.state.bank.transaction() as db:
        with pytest.raises(DomainError) as missing:
            profiles.require(db, 'demo')
        assert missing.value.code == 'ASSESSMENT_REQUIRED'
    submit(client)
    submit(client, body({q: 0 for q in ANSWERS}, 1, 'retake-profile-key'))
    with app.state.bank.transaction() as db:
        with pytest.raises(DomainError) as changed:
            profiles.require(db, 'demo', 1)
        assert changed.value.code == 'PROFILE_CHANGED'
        assert profiles.require(db, 'demo', 2)['level'] == 1
    expires = profiles.get('demo')['profile']['expires_at']
    from ff_agent.bank import TZ
    app.state.bank.clock = lambda: datetime.fromtimestamp(expires, TZ)
    with app.state.bank.transaction() as db:
        with pytest.raises(DomainError) as expired:
            profiles.require(db, 'demo', 2)
        assert expired.value.code == 'ASSESSMENT_EXPIRED'


def test_changed_questionnaire_version_invalidates_old_profile(case, monkeypatch):
    app, client = case
    submit(client)
    monkeypatch.setattr(investment_profile, 'SURVEY_VERSION', 'demo-risk-v2')
    assert client.get('/api/investment-profile').json()['status'] == 'EXPIRED'
    compared = client.get('/api/wealth/compare').json()
    assert all(product['eligible'] is None for product in compared['products'])
    with app.state.bank.transaction() as db:
        with pytest.raises(DomainError) as expired:
            app.state.investment_profiles.require(db, 'demo', 1)
        assert expired.value.code == 'ASSESSMENT_EXPIRED'


def test_profile_submission_audit_has_no_answers_and_replay_has_no_new_event(case):
    app, client = case
    submit(client); submit(client)
    with app.state.bank.transaction() as db:
        rows = db.execute("SELECT detail FROM audit WHERE event='INVESTMENT_PROFILE_SUBMITTED'").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]['detail']) == {'version': 1, 'level': 5, 'survey_version': 'demo-risk-v1'}
    assert not any(question in rows[0]['detail'] for question in ANSWERS)


def test_profile_answers_never_enter_memory_context(case):
    app, client = case
    submit(client)
    context = Memory(app.state.bank).context('demo', '查理财', session_id='profile-privacy')
    encoded = json.dumps(context, ensure_ascii=False)
    assert not any(question in encoded for question in ANSWERS)


def test_short_horizon_and_immediate_liquidity_caps_are_explained(case):
    _, client = case
    short = submit(client, body({**ANSWERS, 'horizon': 0}))
    assert short['profile']['score'] == 15 and short['profile']['level'] == 2
    assert any(cap['id'] == 'horizon' for cap in short['profile']['caps'])
    urgent = submit(client, body({**ANSWERS, 'liquidity': 0}, 1, 'urgent-liquidity-key'))
    assert urgent['profile']['level'] == 1


def test_current_view_is_usable_on_query_only_connection(case):
    app, client = case
    submit(client)
    from ff_agent.db import connect
    db = connect(app.state.bank.path)
    try:
        db.execute('PRAGMA query_only=ON')
        assert ProfileService(app.state.bank).current(db, 'demo')['status'] == 'VALID'
    finally:
        db.close()


def test_binding_snapshot_requires_explicit_version_and_minimizes_answers(case):
    app, client = case
    submit(client)
    profiles = app.state.investment_profiles
    with app.state.bank.transaction() as db:
        for invalid in (None, True, 0):
            with pytest.raises(DomainError):
                profiles.binding_snapshot(db, 'demo', invalid)
        snapshot = profiles.binding_snapshot(db, 'demo', 1)
    assert set(snapshot) == {'version', 'level', 'survey_version', 'expires_at'}
    assert not any(question in json.dumps(snapshot) for question in ANSWERS)


def test_comparison_minimizes_assessment_and_exposes_policy_fingerprint(case):
    _, client = case
    submit(client)
    result = client.get('/api/wealth/compare').json()
    assert set(result['assessment']['profile']) == {'version', 'level', 'survey_version', 'expires_at'}
    serialized = json.dumps(result)
    assert 'answers' not in serialized and 'contributions' not in serialized
    assert len(result['policy_fingerprint']) == 64


def test_comparison_eligibility_fails_closed_for_invalid_assessment_shape():
    from ff_agent.wealth_compare import _eligibility
    assert _eligibility(None, 3)[0] is None
