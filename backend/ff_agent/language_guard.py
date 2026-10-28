"""Pure, bounded checks for unsafe transfer wording."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class LanguageCheck:
    kind: str
    message: str
    clear_pending: bool


_DIGITS = '一二两三四五六七八九'
_CHINESE = '零一二两三四五六七八九十百千万'
_AMOUNT = rf'(?:\d+(?:\.\d+)?|[{_CHINESE}]+)'
_TRANSFER = re.compile(r'转账|转给|(?:给|向).{0,20}转|(?:再转|转)\s*' + _AMOUNT)
_NEGATIVE = re.compile(r'(?:不要|不用|不|别|取消|停止|莫|勿|唔好)\s*(?:(?:再|马上|现在|继续|先)\s*)?(?:(?:给|向|畀)[^，。！？,;；!?\n]{0,20}?)?[转轉](?:账|帳|给|畀)?|(?:那笔|这笔)[^，。！？,;；!?\n]{0,8}别转')
_TENTATIVE = re.compile(r'(?:要不要|要不)\s*(?:(?:给|向|畀)[^，。！？,;；!?\n]{0,20}?)?[转轉](?:账|帳|给|畀)?|[转轉]\s*不\s*[转轉]')
_QUERY_ONLY = re.compile(rf'\s*(?:请\s*)?(?:查询|查一下|查)\s*[0-9{_CHINESE}年月日号从到至的~～/—\s-]*转账(?:记录)?\s*[。.!！?？]*\s*')
_SHORT_STOP = re.compile(r'^(?:算了|不用了)[。.!！\s]*$')
_RANGE = re.compile(rf'{_AMOUNT}\s*(?:到|至|~|～|\-|—)\s*{_AMOUNT}\s*[元块]?')
_APPROX = re.compile(rf'(?:大概|大约|约|差不多)\s*{_AMOUNT}|{_AMOUNT}\s*(?:元|块)?\s*(?:左右|上下|大概|大约)')
_TRAILING_DIGIT = re.compile(rf'(?<![{_CHINESE}])[{_CHINESE}]*[百千万][{_DIGITS}](?![{_CHINESE}])')
_RUN_DIGITS = re.compile(rf'(?<![{_CHINESE}])[{_DIGITS}]{{2,}}(?![{_CHINESE}])')
_DECIMAL_CHINESE = re.compile(rf'[{_CHINESE}]+点[{_CHINESE}]+元')
_COMMA_AMOUNT = re.compile(r'\d{1,3}(?:,\d{3})+\s*[元块]?')
_MALFORMED_DECIMAL = re.compile(r'\d+(?:\s+\.\s*\d*|\.\s+\d*|\.(?!\d))')
_FRACTIONAL_KUAI = re.compile(rf'(?:\d+|[{_CHINESE}]+)块(?:\d+|[{_CHINESE}]+)(?![\d{_CHINESE}])')
_NUMERIC_FOLLOWUP = re.compile(rf'\s*(?:{_AMOUNT}|(?:\d+|[{_CHINESE}]+)块(?:\d+|[{_CHINESE}]+))\s*[元块]?\s*')
_LOOSE_AMOUNT = re.compile(
    rf'[{_DIGITS}]{{2}}[十百千万]|几[十百千万]|[十百千万]几|[百千万]把|'
    rf'{_AMOUNT}\s*[元块]?\s*(?:来|多|余)(?:一点|一些|点|些)?(?=\s*(?:元|块|$|[，。！？,.!?；;]))')
_LOOSE_FOLLOWUP = re.compile(
    rf'\s*(?:[\d{_CHINESE}几来多余把.]+\s*[元块]?|'
    rf'(?:大概|大约|约|差不多)\s*{_AMOUNT}\s*[元块]?|'
    rf'{_AMOUNT}\s*[元块]?\s*(?:左右|上下|大概|大约|(?:来|多|余)(?:一点|一些|点|些)?)\s*[元块]?)\s*')


def inspect_language(text, context=None):
    """Inspect at most 2000 normalized characters without changing caller data."""
    if not isinstance(text, str):
        return None
    sample = unicodedata.normalize('NFKC', text[:2000])[:2000]
    pending = isinstance(context, dict) and bool(context.get('pending_transfer'))
    negative = bool(_NEGATIVE.search(sample))
    # Preserve clause boundaries when checking for a separate positive instruction.
    remainder = _NEGATIVE.sub('。', sample) if negative else sample
    positive = negative and any(
        _TRANSFER.search(clause)
        for clause in re.split(r'[，。！？,;；!?\n]+', remainder)
    )
    if _TENTATIVE.search(sample) or positive:
        return LanguageCheck('clarify_transfer_intent', '请确认：现在是否要发起转账？', False)
    if negative or (pending and _SHORT_STOP.fullmatch(sample.strip())):
        return LanguageCheck('negated_transfer', '不会发起新的转账；若已有待确认卡片，请在卡片中选择“暂不操作”。', True)
    if _QUERY_ONLY.fullmatch(sample):
        return None
    active = bool(_TRANSFER.search(sample)) or (pending and bool(
        _NUMERIC_FOLLOWUP.fullmatch(sample) or _LOOSE_FOLLOWUP.fullmatch(sample)))
    if active and any(pattern.search(sample) for pattern in (
        _RANGE, _APPROX, _TRAILING_DIGIT, _RUN_DIGITS,
        _DECIMAL_CHINESE, _COMMA_AMOUNT, _MALFORMED_DECIMAL, _FRACTIONAL_KUAI, _LOOSE_AMOUNT,
    )):
        return LanguageCheck('ambiguous_amount', '请提供准确的数字金额（元）？', False)
    return None
