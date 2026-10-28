"""Explicit birthday field extraction. Pure text -> patch; no state, money or storage."""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from ff_agent.wealth_amount import wealth_amount

__all__ = ['AccountOption', 'is_birthday_intent', 'is_other_business', 'extract_birthday_fields']


@dataclass(frozen=True)
class AccountOption:
    id: str
    name: str
    masked_number: str


_KNOWN = ('妈妈', '爸爸', '爱人', '老婆', '老公', '母亲', '父亲', '奶奶', '爷爷', '外婆', '外公',
          '姥姥', '姥爷', '儿子', '女儿', '哥哥', '姐姐', '弟弟', '妹妹', '妻子', '丈夫', '岳母', '岳父', '婆婆', '公公')
_AMBIG = re.compile(r'[她他它吗嘛呢吧啊谁某?？或]|这个|那个|人家')
_BIRTH = re.compile(r'生日|过生|过寿')
_PLAN = re.compile(r'安排|准备|策划|筹备|庆祝|办|订|预订|帮我|计划')
_OTHER = re.compile(r'转账|转\s*[0-9零一二两三四五六七八九十百千万]|汇款|查询?余额|余额|账单|卡片|信用卡|理财|基金|订阅|记住|记一下')
_SEP = r'[，,。；;！!\s]'
_REL = (('大后天', 3), ('今天', 0), ('明天', 1), ('明儿', 1), ('后天', 2))
_REL_RE = re.compile(r'大后天|今天|明天|明儿|后天')
_ISO_RE = re.compile(r'(\d{4})(?:-(\d{1,2})-(\d{1,2})|年(\d{1,2})月(\d{1,2})[日号])')
_NEG = re.compile(r'不是[^，,。；;！!\s]+')
_FIELDISH = re.compile(r'[0-9元块天日号月年]|预算|日期|账户|明儿')
_MONEY = re.compile(r'[0-9.,零一二两三四五六七八九十百千万]+\s*(?:元|块)')


def _norm(text):
    if not isinstance(text, str):
        return ''
    t = unicodedata.normalize('NFKC', text).strip()
    # Explicit written family aliases (not speech recognition).
    return re.sub(r'我爸(?!爸)', '爸爸', re.sub(r'我妈(?!妈)', '妈妈', t))


def is_birthday_intent(text) -> bool:
    t = _norm(text)
    if not _BIRTH.search(t) or re.search(r'你(?:的)?(?:生日|过生)', t):
        return False
    return bool(_PLAN.search(t) or re.search(r'给.{1,8}?(?:过生|过寿|.*生日)', t) or any(k in t for k in _KNOWN))


def is_other_business(text) -> bool:
    return bool(_OTHER.search(_norm(text)))


def _label(raw):
    x = re.sub(r'^(?:我的|我)', '', (raw or '').strip())
    if not x or _AMBIG.search(x):
        return None
    if x in _KNOWN or re.fullmatch(r'[\u4e00-\u9fffA-Za-z·]{1,8}', x):
        return x
    return None


def _recipient(t, asked):
    # 不，是X -> X ; 不是X，是Y -> Y ; bare 不是X -> None. Only for recipient-like values.
    m = re.search(rf'不({_SEP}*)是([^，,。；;！!\s]+)(?:{_SEP}+是([^，,。；;！!\s]+))?', t)
    # Strip birthday/plan predicate tails so '是爸爸过生日' -> '爸爸' before the field guard.
    a = re.sub(r'(?:过生日|生日|过生|过寿|安排|准备)+$', '', m[3]) if m and m[3] else None
    if m and not _FIELDISH.search(m[2]) and not (a and _FIELDISH.search(a)):
        if m[3]:
            return True, _label(a)
        return True, (_label(m[2]) if m[1] else None)
    m = re.search(r'(?:称呼改(?:成|为)|收礼人改(?:成|为)|改成给)([^，,。；;！!\s]+)', t)
    if m:
        return True, _label(m[1])
    m = re.search(r'给(.{1,8}?)(?:安排|准备|策划|筹备|庆祝|办|过生|过寿|过个?)', t)
    if m and _BIRTH.search(t):
        return True, _label(m[1])
    for k in _KNOWN:
        if k in t and _BIRTH.search(t):
            return True, k
    if asked == 'recipient_label' and not re.search(r'[0-9元块账户预算号日月年天]', t):
        return True, _label(re.sub(r'^(?:是|给)', '', t).strip('。！!'))
    return False, None


def _date(t, now):
    t = _NEG.sub('', t)  # negated candidates never count
    found = set()
    for m in _ISO_RE.finditer(t):
        try:
            found.add(date(int(m[1]), int(m[2] or m[4]), int(m[3] or m[5])).isoformat())
        except ValueError:
            return True, None
    rel = dict(_REL)
    found |= {(now.date() + timedelta(days=rel[w])).isoformat() for w in _REL_RE.findall(t)}
    if found:
        return True, (found.pop() if len(found) == 1 else None)
    if re.search(r'日期|\d+\s*[号日]|[一二三四五六七八九十]+[号日]|\d+\s*月|下周|周[一二三四五六日天]|星期', t):
        return True, None
    return False, None


def _budget(t, asked):
    kept = _NEG.sub('', t)
    amounts = set(_MONEY.findall(kept))
    if len(amounts) > 1:
        return True, None  # conflicting candidates: ask again
    m = re.search(rf'预算(?:改成|改为|是|为)?[:：]?\s*([^，,。；;！!]*)', t)
    if m:
        return True, wealth_amount(m[1].strip(), bare=True)
    if '不是' in t and _MONEY.search(t):
        return True, (wealth_amount(amounts.pop(), bare=True) if amounts else None)
    if asked == 'budget_cents':
        return True, wealth_amount(t, bare=True)
    return False, None


def _account(t, owned, asked):
    hits = {a.id for a in owned if a.name and a.name in t or a.id and a.id in t}
    if asked == 'account_id':
        hits |= {a.id for a in owned if a.name.removesuffix('账户') and t.strip('。') == a.name.removesuffix('账户')}
    if len(hits) == 1:
        return True, hits.pop()
    if hits or asked == 'account_id' or re.search(r'(?:用|从|付款)\S{0,8}(?:账户|卡)|账户', t):
        return True, None
    return False, None


def extract_birthday_fields(text, pending, owned, now) -> dict:
    t = _norm(text)
    asked = (pending or {}).get('asked')
    patch = {}
    for field, (hit, value) in (('recipient_label', _recipient(t, asked)), ('event_date', _date(t, now)),
                                ('budget_cents', _budget(t, asked)), ('account_id', _account(t, tuple(owned or ()), asked))):
        if hit:
            patch[field] = value
    return patch
