"""Pure bill-period parsing for the rule planner.

No I/O, no bank access, no state. Returns either {'period': 'YYYY' | 'YYYY-MM'}
or {'clarify': message, 'reason': code}. The reason code lets a later
pending_bill follow-up know what is missing; this module does not store it.
A month without a year uses the current business year even if that month is
still in the future (the read-only bill is simply empty); it never rolls back.
"""
import re
from datetime import timedelta

YEAR_MIN, YEAR_MAX = 1900, 2100

MSG_MULTI_PERIOD = '一次先分析一个月份或一个年份，请问要看哪一个？'
MSG_MULTI_YEAR = '一次只能分析一个年份的账单，请问要看哪一年？'
MSG_YEAR_RANGE = '账单仅支持1900到2100年，请换一个年份。'
MSG_MONTH_RANGE = '请提供1到12月中的一个月份。'

_CN_DIGITS = {'〇': 0, '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
# Year tokens: a 4-digit year followed by 年 or a -MM suffix, or a relative year.
_YEAR = re.compile(r'(?<![0-9])[0-9]{4}(?=\s*年|-[0-9])|今年|去年')
_DASH_MONTH = re.compile(r'(?<![0-9])[0-9]{4}-([0-9]{1,2})(?![0-9])')
_NUM_MONTH = re.compile(r'(?<![0-9-])([0-9]{1,2})\s*月')
_CN_MONTH = re.compile(r'([〇零一二两三四五六七八九十]{1,3})\s*月')
# Abbreviated ranges/lists such as 1至3月, 1~3月, 一到三月, 1、3月. The numeric
# start must not follow a digit so a year like 2026 is never a month start.
_MONTH_SPAN = re.compile(
    r'(?:(?<![0-9])[0-9]{1,2}|[〇零一二两三四五六七八九十]{1,3})\s*月?\s*'
    r'(?:到|至|-|~|～|、|和|与|及|,|，)\s*'
    r'(?:[0-9]{1,2}|[〇零一二两三四五六七八九十]{1,3})\s*月')


def chinese_month(word):
    """Convert 一..九 / 十 / 十一 / 十二 style words; None if malformed."""
    if '十' not in word:
        return _CN_DIGITS[word] if len(word) == 1 else None
    head, _, tail = word.partition('十')
    if len(head) > 1 or len(tail) > 1 or '十' in tail:
        return None
    return (_CN_DIGITS[head] if head else 1) * 10 + (_CN_DIGITS[tail] if tail else 0)


def _clarify(reason, message):
    return {'clarify': message, 'reason': reason}


def parse_bill_period(text, now):
    """Resolve one bill period from text relative to business time `now`."""
    if _MONTH_SPAN.search(text):
        return _clarify('multiple_periods', MSG_MULTI_PERIOD)
    months = ([int(m) for m in _DASH_MONTH.findall(text)]
              + [int(m) for m in _NUM_MONTH.findall(text)]
              + [chinese_month(w) for w in _CN_MONTH.findall(text)])
    # Ranges or lists of months are never collapsed to the first one.
    if len(months) > 1:
        return _clarify('multiple_periods', MSG_MULTI_PERIOD)
    tokens = {m[0] for m in _YEAR.finditer(text)}
    if len(tokens) > 1:
        return _clarify('multiple_years', MSG_MULTI_YEAR)
    token = next(iter(tokens), None)
    year = (now.year if token == '今年' else now.year - 1 if token == '去年'
            else int(token) if token else None)
    if year is not None and not YEAR_MIN <= year <= YEAR_MAX:
        return _clarify('year_out_of_range', MSG_YEAR_RANGE)
    if months:
        month = months[0]
        if month is None or not 1 <= month <= 12:
            return _clarify('month_out_of_range', MSG_MONTH_RANGE)
        return {'period': f'{now.year if year is None else year}-{month:02d}'}
    if year is not None:
        return {'period': str(year)}
    if re.search(r'年度|全年', text):
        return {'period': str(now.year)}
    if '上月' in text or '上个月' in text:
        return {'period': (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')}
    return {'period': now.strftime('%Y-%m')}
