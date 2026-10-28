# B2 周期扣费候选：只读分析本人流水。候选不等于真实代扣关系，不写订阅、不自动取消、不做外部调用。
import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
MIN_GAP_DAYS = 25
MAX_GAP_DAYS = 35
WINDOW_DAYS = 365
RULE = '同商户同金额，相邻扣费间隔25—35天且跨月'


def _invalid():
    from .bank import DomainError
    return DomainError('INVALID_BILL_DATA', '账单数据异常，暂时无法分析', 503)


def _field(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        raise _invalid() from None


def _to_shanghai(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise _invalid() from None
    else:
        raise _invalid()
    if dt.tzinfo is None or dt.utcoffset() is None:
        # legacy naive 记录按上海本地时间解释
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def _normalize(row):
    rid = _field(row, 'id')
    kind = _field(row, 'kind')
    merchant = _field(row, 'merchant')
    amount = _field(row, 'amount_cents')
    if not isinstance(rid, str) or not rid:
        raise _invalid()
    if not isinstance(kind, str) or not isinstance(merchant, str):
        raise _invalid()
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise _invalid()
    return {'id': rid, 'kind': kind, 'merchant': merchant, 'amount_cents': amount,
            'at': _to_shanghai(_field(row, 'occurred_at'))}


def _monthly_step(prev, cur):
    gap = (cur['at'].date() - prev['at'].date()).days
    other_month = (prev['at'].year, prev['at'].month) != (cur['at'].year, cur['at'].month)
    return MIN_GAP_DAYS <= gap <= MAX_GAP_DAYS and other_month


def _runs(items):
    # 只比较排序后直接相邻的两条；中间任何同价记录都会断开，不能跳行凑月度
    run = items[:1]
    for prev, cur in zip(items, items[1:]):
        if _monthly_step(prev, cur):
            run.append(cur)
        else:
            if len(run) >= 2:
                yield run
            run = [cur]
    if len(run) >= 2:
        yield run


def detect_recurring(rows):
    # 纯函数：不读库、不写库、不调用外部服务；坏数据抛 INVALID_BILL_DATA
    groups = defaultdict(list)
    for row in rows:
        item = _normalize(row)
        if item['kind'] == 'EXPENSE':
            groups[(item['merchant'], item['amount_cents'])].append(item)
    picked = []
    for (merchant, amount), items in groups.items():
        items.sort(key=lambda it: (it['at'], it['id']))
        best = max(_runs(items), key=lambda r: (r[-1]['at'], len(r)), default=None)
        if best is None:
            continue
        last = best[-1]['at']
        picked.append((last, {
            'merchant': merchant,
            'amount_cents': amount,
            'occurrences': len(best),
            'evidence_ids': [it['id'] for it in best],
            'last_seen': last.isoformat(),
            'rule': RULE,
            'verified': False,
        }))
    picked.sort(key=lambda p: (-p[0].timestamp(), p[1]['merchant'], p[1]['amount_cents']))
    return [candidate for _, candidate in picked]


def recurring_insights(bank, user):
    with bank.transaction() as db:
        bank._user(db, user)
        now = _to_shanghai(bank.now())
        start = now - timedelta(days=WINDOW_DAYS)
        rows = db.execute(
            'SELECT id,kind,amount_cents,merchant,occurred_at FROM transactions '
            'WHERE user_id=? ORDER BY occurred_at,id', (user,)).fetchall()
        recent = []
        for row in rows:
            # 全部本人流水先校验，坏记录失败关闭，事务回滚且不写审计
            item = _normalize(row)
            if start <= item['at'] <= now:
                recent.append(row)
        candidates = detect_recurring(recent)
        bank._event(db, user, None, 'RECURRING_ANALYZED',
                    json.dumps({'scanned': len(recent), 'candidates': len(candidates)}))
    return {'simulated': True, 'as_of': now.date().isoformat(), 'candidates': candidates}
