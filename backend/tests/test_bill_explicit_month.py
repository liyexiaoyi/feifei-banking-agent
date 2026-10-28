from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ff_agent.planner import Planner


@pytest.mark.parametrize('text,period', [
    ('看看3月账单', '2026-03'),
    ('3月花了多少', '2026-03'),
    ('二月份的消费', '2026-02'),
    ('十二月账单', '2026-12'),
    ('去年二月账单', '2025-02'),
    ('2025年三月账单', '2025-03'),
    ('今年11月份消费', '2026-11'),
])
def test_explicit_month_is_never_silently_replaced_with_current_month(text, period):
    planner = Planner(clock=lambda: datetime(2026, 10, 27, tzinfo=ZoneInfo('Asia/Shanghai')))
    assert planner.plan(text, {}) == [{'type': 'bill', 'period': period}]


@pytest.mark.parametrize('text', [
    '2026年13月账单', '2026年0月账单', '1800年3月账单',
    '2101年3月账单', '3月和5月消费', '三月到五月账单',
])
def test_invalid_or_multiple_periods_ask_instead_of_guessing(text):
    planner = Planner(clock=lambda: datetime(2026, 10, 27, tzinfo=ZoneInfo('Asia/Shanghai')))
    assert planner.plan(text, {})[0]['type'] == 'clarify'
