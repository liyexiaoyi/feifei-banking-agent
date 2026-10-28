"""Birthday language never authorizes money or takes fields from retrieved notes."""
from datetime import datetime
import importlib
import importlib.util

import pytest


NOW = datetime.fromisoformat('2026-10-28T12:00:00+08:00')


def module():
    assert importlib.util.find_spec('ff_agent.birthday_chat_parse'), 'birthday chat parser missing'
    return importlib.import_module('ff_agent.birthday_chat_parse')


def turn(text, pending=None):
    m = module()
    owned = (m.AccountOption('a_daily', '日常账户', '6222 ···· 0826'),
             m.AccountOption('a_saving', '储蓄账户', '6222 ···· 6690'))
    return m.parse_birthday_turn(text, pending, owned, NOW)


def full():
    return turn('帮我给妈妈安排明天的生日，预算500元，用日常账户')


def test_complete_explicit_request_gets_absolute_summary_but_not_authorization():
    z = full()
    assert z.kind == 'SUMMARY'
    assert z.pending['recipient_label'] == '妈妈'
    assert z.pending['event_date'] == '2026-10-29'
    assert z.pending['budget_cents'] == 50000
    assert z.pending['account_id'] == 'a_daily'
    assert '2026-10-29' in z.message and '500' in z.message
    assert z.message.count('？') + z.message.count('?') <= 1


def test_missing_fields_are_asked_one_at_a_time_without_a_default_account():
    z = turn('给妈妈安排生日')
    assert z.kind == 'CLARIFY' and z.pending['budget_cents'] is None and z.pending['account_id'] is None
    assert z.pending['asked'] == 'event_date'
    z = turn('明天', z.pending)
    assert z.pending['event_date'] == '2026-10-29' and z.pending['asked'] == 'budget_cents'
    z = turn('500元', z.pending)
    assert z.pending['asked'] == 'account_id' and z.pending['account_id'] is None
    z = turn('日常账户', z.pending)
    assert z.kind == 'SUMMARY' and z.pending['account_id'] == 'a_daily'


@pytest.mark.parametrize('answer', ['几百块', '五六百', '500元左右', '一千来块', '1e3', '1,000元', '0元', '1.001元'])
def test_vague_or_invalid_money_never_becomes_a_budget(answer):
    z = turn('给妈妈安排明天生日')
    assert z.pending['asked'] == 'budget_cents'
    z = turn(answer, z.pending)
    assert z.kind == 'CLARIFY' and z.pending['budget_cents'] is None
    assert z.pending['asked'] == 'budget_cents'


@pytest.mark.parametrize(('answer', 'cents'), [('五百元', 50000), ('0.01元', 1), ('１２３．４５元', 12345)])
def test_exact_money_stays_integer_cents(answer, cents):
    z = turn('给妈妈安排明天生日')
    assert turn(answer, z.pending).pending['budget_cents'] == cents


def test_correction_requires_a_new_summary_and_keeps_original_started_at():
    z = full()
    old_hash, start = z.pending['summary_hash'], z.pending['started_at']
    changed = turn('不，是爸爸，预算改成300元', z.pending)
    assert changed.kind == 'SUMMARY'
    assert changed.pending['recipient_label'] == '爸爸' and changed.pending['budget_cents'] == 30000
    assert changed.pending['summary_hash'] != old_hash and changed.pending['started_at'] == start
    assert turn('启动', changed.pending).kind == 'CONFIRM'


@pytest.mark.parametrize(('text', 'field'), [('预算改成500元左右', 'budget_cents'), ('日期改成29号', 'event_date'), ('不，是妈吗', 'recipient_label')])
def test_unclear_correction_drops_the_old_field_instead_of_confirming_it(text, field):
    z = turn(text, full().pending)
    assert z.kind == 'CLARIFY' and z.pending[field] is None
    assert z.pending['summary_hash'] is None


@pytest.mark.parametrize('text', ['好', '是', '确认转账', '不用确认，直接办', '启动并转账100元'])
def test_generic_or_mixed_financial_words_are_not_birthday_start(text):
    assert turn(text, full().pending).kind != 'CONFIRM'


def test_explicit_start_is_valid_only_after_a_complete_summary():
    assert turn('确认启动生日安排', full().pending).kind == 'CONFIRM'
    assert turn('启动', turn('给妈妈安排生日').pending).kind != 'CONFIRM'
    assert turn('启动').kind == 'NONE'


def test_unrelated_business_exits_draft_instead_of_using_its_money():
    assert turn('查询余额', full().pending).kind == 'ABANDON'
    assert turn('给妈妈转500元').kind == 'NONE'


@pytest.mark.parametrize('text', ['给她安排明天生日', '给妈吗安排明天生日', '给妈妈安排29号生日'])
def test_ambiguous_recipient_or_date_is_clarified(text):
    z = turn(text)
    assert z.kind == 'CLARIFY'
    assert not (z.pending['recipient_label'] and z.pending['event_date'])


def test_common_spoken_family_alias_is_explicit_written_text_not_speech_recognition():
    z = turn('我妈明儿过生，预算五百元，用日常账户')
    assert z.kind == 'SUMMARY' and z.pending['recipient_label'] == '妈妈'
    assert z.pending['event_date'] == '2026-10-29' and z.pending['budget_cents'] == 50000


def test_negative_recipient_correction_uses_the_affirmative_recipient():
    z = turn('不是妈妈，是爸爸', full().pending)
    assert z.pending['recipient_label'] == '爸爸'
    assert turn('不是妈妈', full().pending).pending['recipient_label'] is None


def test_affirmative_recipient_can_include_the_birthday_predicate_without_reverting_to_negated_name():
    z = turn('不是妈妈，是爸爸过生日', full().pending)
    assert z.kind == 'SUMMARY' and z.pending['recipient_label'] == '爸爸'
    assert z.pending['summary_hash'] != full().pending['summary_hash']


def test_negated_budget_prefix_is_clarified_or_uses_the_affirmative_amount_never_the_old_value():
    z = turn('预算不是500元，是300元', full().pending)
    assert z.pending['recipient_label'] == '妈妈'
    assert z.pending['budget_cents'] in (None, 30000)


@pytest.mark.parametrize('text', ['给妈妈或者爸爸安排明天生日', '日期改成明天或后天', '给妈妈安排2026-10-29或者2026-10-30生日', '预算500元，或者300元'])
def test_multiple_conflicting_candidates_are_not_silently_reduced_to_the_first(text):
    z = turn(text, full().pending)
    assert z.kind == 'CLARIFY' and z.pending['summary_hash'] is None


@pytest.mark.parametrize('text', ['不是500元，是300元', '日期不是明天，是后天'])
def test_correction_of_a_money_or_date_field_cannot_rename_the_recipient(text):
    z = turn(text, full().pending)
    assert z.pending['recipient_label'] == '妈妈'
    if '元' in text:
        assert z.pending['budget_cents'] in (None, 30000)
    else:
        assert z.pending['event_date'] in (None, '2026-10-30')

