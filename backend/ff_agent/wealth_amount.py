import re
import unicodedata
from decimal import Decimal, InvalidOperation

__all__ = ['wealth_amount']
_DIGITS = '零一二三四五六七八九'
_D = '[一二三四五六七八九]'
_T = rf'{_D}十{_D}?'
_H = rf'{_D}百(?:(?:{_T})|零{_D})?'
_K = rf'{_D}千(?:(?:{_H})|零(?:(?:{_T})|{_D}))?'
_SECTION = rf'(?:{_K}|{_H}|{_T}|十{_D}?|{_D})'
_INTEGER = re.compile(rf'(?:零|{_SECTION}(?:万(?:零?{_SECTION})?)?)\Z')
_TOKEN = r'[0-9零〇一二两三四五六七八九十百千万亿点.]+'
_AMOUNT = re.compile(rf'({_TOKEN})\s*[元块]')
_BARE = re.compile(rf'({_TOKEN})\s*(?:元|块钱|块)?\Z')
_FORBIDDEN = re.compile(r'[+\-−﹣－±负負约約余餘几幾多~～≈]|大概|左右|大约|上下')


def _chinese_integer(text):
    total = section = digit = 0
    for char in text:
        if char in _DIGITS: digit = _DIGITS.index(char)
        elif char == '万':
            total += (section + digit) * 10000
            section = digit = 0
        else:
            section += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
    return total + section + digit


def _cents(token):
    token = token.replace('两', '二').replace('〇', '零')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', token):
        try:
            value = Decimal(token)
            return int(value * 100) if 0 < value <= 1000000 else None
        except InvalidOperation:
            return None
    parts = token.split('点')
    if len(parts) > 2 or not _INTEGER.fullmatch(parts[0]):
        return None
    fraction = parts[1] if len(parts) == 2 else ''
    if len(parts) == 2 and not re.fullmatch(r'[零一二三四五六七八九]{1,2}', fraction):
        return None
    # A final digit after 万 needs an explicit 零; 十 already fixes its place.
    if re.search(r'[百千万][一二三四五六七八九]$', parts[0]):
        return None
    cents = _chinese_integer(parts[0]) * 100
    if fraction:
        cents += int(''.join(str(_DIGITS.index(c)) for c in fraction).ljust(2, '0'))
    return cents if 0 < cents <= 100000000 else None


def wealth_amount(text: str, bare: bool = False) -> int | None:
    if not isinstance(text, str):
        return None
    text = unicodedata.normalize('NFKC', text).strip()
    if _FORBIDDEN.search(text) or re.search(r'[0-9]+\.[0-9]{3,}', text):
        return None
    if (re.search(r'[0-9][,，][0-9]',text)
            or re.search(rf'{_TOKEN}\s*块\s*{_TOKEN}|{_TOKEN}\s*[角分毛]|{_TOKEN}\s*[来來]\s*[元块]',text)):
        return None
    if bare:
        match = _BARE.fullmatch(text)
        return _cents(match[1]) if match else None
    matches = list(_AMOUNT.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.match(rf'\s*(?:钱\s*)?{_TOKEN}', text[match.end():]):
        return None
    remaining=text[:match.start()]+text[match.end():]
    if re.search(r'[0-9]|[零〇一二两三四五六七八九]{2,}|[十百千万亿]',remaining):
        return None
    if match.start() and re.fullmatch(r'[A-Za-z0-9_.]', text[match.start() - 1]):
        return None
    return _cents(match[1])
