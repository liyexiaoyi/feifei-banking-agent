"""Annual bill report and explainable anomaly candidates (read-only, simulated data)."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
LARGE_EXPENSE_CENTS = 100000
DUPLICATE_WINDOW_SECONDS = 120
PUBLIC_FIELDS = ('id', 'kind', 'amount_cents', 'category', 'merchant', 'occurred_at')
KIND_FIELDS = {'EXPENSE': 'expense_cents', 'REFUND': 'refund_cents', 'INCOME': 'income_cents'}
_YEAR_RE = re.compile(r'[0-9]{4}')


def _domain_error(code, message, status=400):
    from .bank import DomainError  # lazy import: bank imports this module
    return DomainError(code, message, status)


def validate_year(year):
    # Exact ASCII 4 digits, str only (rejects bool/int/None/full-width), 1900..2100.
    if type(year) is not str or not _YEAR_RE.fullmatch(year) or not 1900 <= int(year) <= 2100:
        raise _domain_error('INVALID_YEAR', '请提供有效年份，例如 2026（1900-2100）。', 422)
    return year


def _yuan(cents):
    # Integer formatting only, no float.
    sign = '-' if cents < 0 else ''
    cents = abs(int(cents))
    return f'{sign}{cents // 100}.{cents % 100:02d}'


def _parse_time(value):
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=TZ)


def anomaly_candidates(rows):
    """Evidence-backed review hints over EXPENSE rows; never a fraud verdict."""
    expenses = [r for r in rows if r['kind'] == 'EXPENSE']
    flags = []
    for r in sorted(expenses, key=lambda r: (r['occurred_at'], r['id'])):
        if int(r['amount_cents']) >= LARGE_EXPENSE_CENTS:
            flags.append({'type': 'large_expense',
                          'message': f'单笔消费 {_yuan(r["amount_cents"])} 元，金额较大，建议核查是否为本人知情消费。',
                          'evidence_ids': [r['id']]})
    groups = {}
    for r in expenses:
        at = _parse_time(r['occurred_at'])
        if at is not None:
            groups.setdefault((r['merchant'], int(r['amount_cents'])), []).append((at.timestamp(), r['id']))
    duplicates = []
    for (merchant, amount), items in groups.items():
        items.sort()
        i = 0
        while i < len(items):
            anchor = items[i][0]
            j = i + 1
            # Window anchored on the group's first entry: no chaining.
            while j < len(items) and items[j][0] - anchor <= DUPLICATE_WINDOW_SECONDS:
                j += 1
            if j - i > 1:
                ids = [tid for _, tid in items[i:j]]
                duplicates.append((anchor, ids, {
                    'type': 'possible_duplicate',
                    'message': f'商户「{merchant}」在 2 分钟内有 {len(ids)} 笔相同金额 {_yuan(amount)} 元的消费，可能重复扣款，建议核对。',
                    'evidence_ids': ids}))
            i = j
    flags.extend(flag for _, _, flag in sorted(duplicates, key=lambda d: (d[0], d[1])))
    return flags


def annual_report(bank, user, year):
    year = validate_year(year)
    previous = str(int(year) - 1)
    with bank.transaction() as db:
        bank._user(db, user)
        # Adjacent lexical years include instants which cross a local year boundary.
        candidates = [dict(r) for r in db.execute(
            'SELECT * FROM transactions WHERE user_id=? AND substr(occurred_at,1,4) BETWEEN ? AND ?',
            (user, str(int(year)-2), str(int(year)+1)))]
        fetched, prior_rows = [], []
        for row in candidates:
            at = _parse_time(row['occurred_at'])
            if (at is None or not re.match(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T', row['occurred_at'])
                    or type(row['amount_cents']) is not int or row['amount_cents'] < 0):
                raise _domain_error('INVALID_BILL_DATA', '部分流水暂时无法核对，未生成年度报告，请稍后重试。', 503)
            local_at = at.astimezone(TZ)
            row['occurred_at'] = local_at.isoformat()
            if str(local_at.year) == year:
                fetched.append(row)
            elif str(local_at.year) == previous:
                prior_rows.append(row)
        fetched.sort(key=lambda r: (r['occurred_at'],r['id']))
        prev = sum(r['amount_cents'] if r['kind']=='EXPENSE' else -r['amount_cents'] if r['kind']=='REFUND' else 0 for r in prior_rows)
        months = {f'{year}-{m:02}': {'period': f'{year}-{m:02}', 'expense_cents': 0, 'refund_cents': 0,
                                       'income_cents': 0, 'net_expense_cents': 0, 'transaction_count': 0}
                  for m in range(1, 13)}
        rows = fetched
        categories = {}
        for r in rows:
            month = months[r['occurred_at'][:7]]
            month['transaction_count'] += 1
            field = KIND_FIELDS.get(r['kind'])
            if field:
                month[field] += int(r['amount_cents'])
            if r['kind'] == 'EXPENSE':
                categories[r['category']] = categories.get(r['category'], 0) + int(r['amount_cents'])
        month_list = list(months.values())
        for month in month_list:
            month['net_expense_cents'] = month['expense_cents'] - month['refund_cents']
        totals = {f: sum(m[f] for m in month_list) for f in KIND_FIELDS.values()}
        bank._event(db, user, None, 'BILL_YEAR_ANALYZED', f'{year} 年度账单，{len(rows)} 条流水')
    return {
        'year': year, 'period': year, 'simulated': True,
        'expense_cents': totals['expense_cents'], 'refund_cents': totals['refund_cents'],
        'income_cents': totals['income_cents'],
        'net_expense_cents': totals['expense_cents'] - totals['refund_cents'],
        'previous_net_expense_cents': int(prev),
        'categories': [{'name': name, 'amount_cents': amount}
                       for name, amount in sorted(categories.items(), key=lambda item: (-item[1], item[0]))],
        'months': month_list,
        'transactions': [{k: r[k] for k in PUBLIC_FIELDS} for r in rows],
        'evidence_ids': [r['id'] for r in rows],
        'flags': anomaly_candidates(rows),
    }
