from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.planner import Planner, PlannerError, validate_plan

NOW = datetime(2026, 10, 27, tzinfo=ZoneInfo('Asia/Shanghai'))


@pytest.mark.parametrize('text,period', [('分析今年账单','2026'),('看看去年的消费','2025'),('分析2025年的账单','2025'),('年度账单','2026'),('今年花了多少','2026'),('全年花销','2026'),('看看2026年2月账单','2026-02'),('这个月的账单','2026-10')])
def test_annual_language_uses_read_only_bill_action(text, period):
    assert Planner(clock=lambda: NOW).plan(text, {}) == [{'type': 'bill', 'period': period}]


@pytest.mark.parametrize('text', ['1899年账单','2101年账单','2025年到2026年的账单'])
def test_out_of_range_or_multiple_years_ask_instead_of_guess(text):
    assert Planner(clock=lambda: NOW).plan(text, {})[0]['type'] == 'clarify'


def test_model_annual_period_is_bounded_and_cannot_authorize():
    assert validate_plan({'actions':[{'type':'bill','period':'2026'}]}) == [{'type':'bill','period':'2026'}]
    for period in ['２０２６','1899','2101','2026-13']:
        with pytest.raises(PlannerError):
            validate_plan({'actions':[{'type':'bill','period':period}]})


def test_chat_annual_evidence_does_not_create_sensitive_operation(tmp_path):
    with TestClient(create_app(tmp_path / 'annual-chat.db')) as client:
        session = client.post('/api/session',json={'user':'demo'}).json()
        client.headers['X-CSRF-Token'] = session['csrf']
        before = client.get('/api/state').json()
        reply = client.post('/api/chat',json={'text':'分析2026年的账单','request_key':'annual-request'}).json()
        assert reply['cards'][0]['type'] == 'bill'
        assert reply['cards'][0]['bill']['year'] == '2026'
        assert len(reply['cards'][0]['bill']['months']) == 12
        after = client.get('/api/state').json()
        assert before['accounts'] == after['accounts'] and before['operations'] == after['operations']
        assert client.post('/api/chat',json={'text':'分析2026年的账单','request_key':'annual-request'}).json() == reply


@pytest.mark.parametrize('text,period', [('今年2月账单','2026-02'),('去年10月消费','2025-10'),('今年的3月花销','2026-03')])
def test_relative_year_with_explicit_month_is_not_mistaken_for_whole_year(text,period):
    assert Planner(clock=lambda: NOW).plan(text,{}) == [{'type':'bill','period':period}]


def test_a_range_with_months_still_asks_for_one_period():
    assert Planner(clock=lambda: NOW).plan('2025年12月到2026年1月的账单',{})[0]['type'] == 'clarify'


@pytest.mark.parametrize('text', ['帮我看2026年1月到3月的账单','今年3月到5月花了多少','对比去年和今年的消费','2025年和2026-10的账单'])
def test_a_period_range_never_silently_becomes_its_first_month_or_year(text):
    assert Planner(clock=lambda: NOW).plan(text,{})[0]['type'] == 'clarify'
