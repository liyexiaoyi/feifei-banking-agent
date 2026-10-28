# B2 本机续费提醒持久化：仅模拟数据、仅本机页面运行时检查。
# 只写 renewal_reminders 与 audit；不触碰 operations、用户授权、资金、交易或订阅关系。
# 无网络调用、无后台循环、无配置密钥。
import json
import secrets
from datetime import date

WINDOW_DAYS = 3
_PREFIX = 'rem_'
_MAX_ID_LEN = 64

_SCHEMA = '''CREATE TABLE IF NOT EXISTS renewal_reminders(
id TEXT PRIMARY KEY,
user_id TEXT NOT NULL,
subscription_id TEXT NOT NULL,
charge_date TEXT NOT NULL,
amount_cents INTEGER NOT NULL,
merchant TEXT NOT NULL,
created_at REAL NOT NULL,
acknowledged INTEGER NOT NULL DEFAULT 0,
version INTEGER NOT NULL DEFAULT 1,
UNIQUE(user_id,subscription_id,charge_date,amount_cents))'''

_CURRENT_SQL = '''SELECT r.id, r.merchant, r.amount_cents, r.charge_date, r.acknowledged, r.version
FROM renewal_reminders r
JOIN subscriptions s ON s.id=r.subscription_id AND s.user_id=r.user_id
WHERE r.user_id=? AND s.status='ACTIVE' AND s.next_charge=r.charge_date
AND s.amount_cents=r.amount_cents AND s.merchant=r.merchant'''


def _error(code, message, status):
    from .bank import DomainError  # 延迟导入，避免循环依赖
    return DomainError(code, message, status)


def _not_found():
    return _error('NOT_FOUND', '未找到可访问的数据', 404)


def _conflict():
    return _error('REMINDER_VERSION_CONFLICT', '提醒状态已变化，请刷新后再确认。', 409)


def _parse_day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def _in_window(value, today):
    day = _parse_day(value)
    if day is None:
        return False
    return 0 <= (day - today).days <= WINDOW_DAYS


def _valid_amount(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _public(row):
    return {'id': row['id'], 'merchant': row['merchant'], 'amount_cents': row['amount_cents'],
            'charge_date': row['charge_date'], 'acknowledged': bool(row['acknowledged']),
            'version': row['version'], 'simulated': True}


# 以下私有 helper 只接收已打开的 db，自身不开事务。

def _current_rows(db, user, today):
    rows = db.execute(_CURRENT_SQL + ' ORDER BY r.charge_date, r.created_at, r.id', (user,)).fetchall()
    return [r for r in rows if _in_window(r['charge_date'], today)]


def _current_one(db, user, reminder_id, today):
    row = db.execute(_CURRENT_SQL + ' AND r.id=?', (user, reminder_id)).fetchone()
    if row is None or not _in_window(row['charge_date'], today):
        return None
    return row


def _insert_due(bank, db, user, today):
    created = 0
    subs = db.execute('SELECT id, merchant, amount_cents, next_charge FROM subscriptions '
                      'WHERE user_id=? AND status=? ORDER BY rowid', (user, 'ACTIVE')).fetchall()
    for s in subs:
        if (not _valid_amount(s['amount_cents']) or not isinstance(s['merchant'], str)
                or not s['merchant'] or _parse_day(s['next_charge']) is None):
            raise _error('INVALID_REMINDER_DATA', '部分续费信息暂时无法核对，请稍后重新查询。', 503)
        if not _in_window(s['next_charge'], today):
            continue
        existing = db.execute('SELECT id,merchant,version FROM renewal_reminders WHERE user_id=? '
                              'AND subscription_id=? AND charge_date=? AND amount_cents=?',
                              (user,s['id'],s['next_charge'],s['amount_cents'])).fetchone()
        if existing and existing['merchant'] != s['merchant']:
            db.execute('UPDATE renewal_reminders SET merchant=?,acknowledged=0,version=version+1 WHERE id=? AND user_id=?',
                       (s['merchant'],existing['id'],user))
            bank._event(db,user,None,'REMINDER_UPDATED',json.dumps({'reminder_id':existing['id'],'version':existing['version']+1}))
            continue
        rid = _PREFIX + secrets.token_urlsafe(16)
        cur = db.execute('INSERT OR IGNORE INTO renewal_reminders(id,user_id,subscription_id,charge_date,'
                         'amount_cents,merchant,created_at) VALUES(?,?,?,?,?,?,?)',
                         (rid, user, s['id'], s['next_charge'], s['amount_cents'], s['merchant'],
                          bank.now().timestamp()))
        if cur.rowcount == 1:
            created += 1
            bank._event(db, user, None, 'REMINDER_CREATED', json.dumps({'reminder_id': rid}))
    return created


def bootstrap_reminders(bank):
    with bank.transaction() as db:
        db.execute(_SCHEMA)


def check_reminders(bank, user):
    with bank.transaction() as db:
        bank._consent(db, user)
        today = bank.now().date()
        created = _insert_due(bank, db, user, today)
        reminders = [_public(r) for r in _current_rows(db, user, today)]
    return {'simulated': True, 'created_count': created, 'reminders': reminders}


def list_reminders(bank, user):
    with bank.transaction() as db:
        bank._consent(db, user)
        reminders = [_public(r) for r in _current_rows(db, user, bank.now().date())]
    return {'simulated': True, 'reminders': reminders}


def ack_reminder(bank, user, reminder_id, expected_version):
    with bank.transaction() as db:
        bank._consent(db, user)
        if not _valid_version(expected_version):
            raise _error('INVALID_EXPECTED_VERSION', 'expected_version 必须是正整数。', 422)
        if (not isinstance(reminder_id, str) or not reminder_id.startswith(_PREFIX)
                or len(reminder_id) > _MAX_ID_LEN):
            raise _not_found()
        row = _current_one(db, user, reminder_id, bank.now().date())
        if row is None:
            raise _not_found()
        version = row['version']
        if row['acknowledged']:
            # 已看：重放首次版本或当前版本都返回同一结果，不改版本、不写审计
            if expected_version in (version - 1, version):
                return _public(row)
            raise _conflict()
        if expected_version != version:
            raise _conflict()
        cur = db.execute('UPDATE renewal_reminders SET acknowledged=1, version=version+1 '
                         'WHERE id=? AND user_id=? AND version=? AND acknowledged=0',
                         (row['id'], user, version))
        if cur.rowcount != 1:
            raise _conflict()
        bank._event(db, user, None, 'REMINDER_ACKNOWLEDGED',
                    json.dumps({'reminder_id': row['id'], 'version': version + 1}))
        updated = db.execute('SELECT id, merchant, amount_cents, charge_date, acknowledged, version '
                             'FROM renewal_reminders WHERE id=? AND user_id=?', (row['id'], user)).fetchone()
        return _public(updated)
