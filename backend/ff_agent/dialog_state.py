"""Small read-only bill clarification state inside the existing session envelope."""
import math
import re
import unicodedata

from .bill_period import parse_bill_period

_BILL = re.compile(r'账单|花销|消费|支出|花在哪|花了|开销|查账')
_ANSWER = re.compile(r'(?:[0-9]{4}(?:年|-[0-9]{1,2}月?)?|[0-9]{1,2}月份?|[〇零一二两三四五六七八九十]{1,3}月份?)')


def valid_bill_pending(value, timestamp):
    if not isinstance(value, dict) or value.get('kind') != 'bill_period':
        return False
    start = value.get('started_at')
    year = value.get('year')
    return (type(start) in (int, float) and math.isfinite(start)
            and 0 <= timestamp - start <= 600
            and (year is None or type(year) is int and 1900 <= year <= 2100))


def start_bill_question(text, now):
    sample = unicodedata.normalize('NFKC', text[:2000])
    if not _BILL.search(sample) or 'clarify' not in parse_bill_period(sample, now):
        return None
    years = {int(y) for y in re.findall(r'(?<![0-9])([0-9]{4})(?=年|-[0-9])', sample)}
    if '今年' in sample:
        years.add(now.year)
    if '去年' in sample:
        years.add(now.year - 1)
    year = next(iter(years)) if len(years) == 1 else None
    if year is not None and not 1900 <= year <= 2100:
        year = None
    return {'kind': 'bill_period', 'started_at': now.timestamp(), 'year': year}


def bill_answer(text, value, now):
    if not valid_bill_pending(value, now.timestamp()):
        return None
    sample = unicodedata.normalize('NFKC', text[:2000]).strip().rstrip('。？！?!').strip()
    if not _ANSWER.fullmatch(sample):
        return None
    if re.fullmatch(r'[0-9]{4}', sample):
        sample += '年'
    elif not re.match(r'[0-9]{4}(?:年|-)', sample) and value.get('year') is not None:
        sample = str(value['year']) + '年' + sample
    return parse_bill_period(sample, now)
