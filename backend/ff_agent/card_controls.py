"""B5 card controls (simulated). Changes are fixed RED and need Bank confirm+verify."""
import json
from datetime import datetime

from .bank import DomainError
from .cards import CardService

TYPE = 'card_controls_update'
MAX_CENTS = 5000000
DEFAULTS = {'locked': False, 'single_limit_cents': 2000000, 'daily_limit_cents': 5000000}
_ACTIONS = {'lock': 'locked', 'unlock': 'locked',
            'set_single_limit': 'single_limit_cents', 'set_daily_limit': 'daily_limit_cents'}
_STATE = set(DEFAULTS)
_PAYLOAD = {'card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind',
            'expected_control_version', 'action', 'before', 'after', 'simulated'}
_CARD_TYPES = ('card_report_lost', 'card_restore', 'card_controls_update', 'card_spend', 'card_credential_change')
_KEY_COLUMNS = ('idempotency_key', 'idem_key', 'key')

_DDL = """CREATE TABLE IF NOT EXISTS card_controls(
    card_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    storage_kind TEXT NOT NULL CHECK(storage_kind IN ('physical','virtual')),
    locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
    single_limit_cents INTEGER NOT NULL DEFAULT 2000000 CHECK(single_limit_cents BETWEEN 1 AND 5000000),
    daily_limit_cents INTEGER NOT NULL DEFAULT 5000000 CHECK(daily_limit_cents BETWEEN 1 AND 5000000),
    control_version INTEGER NOT NULL DEFAULT 1 CHECK(control_version >= 1),
    CHECK(single_limit_cents <= daily_limit_cents))"""


def _conflict(msg='卡片控制状态已改变，请刷新。', code='CARD_CONFLICT'):
    return DomainError(code, msg, 409)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _strict_int(v):
    return type(v) is int


def bootstrap_card_controls(bank):
    """DDL + seed existing owned cards with defaults. Idempotent; no state overwrite."""
    with bank.transaction() as db:
        db.execute(_DDL)
        db.execute("""INSERT OR IGNORE INTO card_controls(card_id,user_id,storage_kind)
            SELECT id,user_id,storage_kind FROM bank_all_cards
            WHERE storage_kind IN ('physical','virtual')""")


def ensure_controls_row(db, user, card_id, storage_kind):
    """Called by root inside the virtual-card INSERT transaction (caller owns tx)."""
    if storage_kind not in ('physical', 'virtual') or not isinstance(card_id, str) or not card_id:
        raise DomainError('INVALID_CARD', '卡片编号无效。', 400)
    if not getattr(db, 'in_transaction', False):  # caller must own an active tx; never autocommit
        raise DomainError('TRANSACTION_REQUIRED', '卡片控制记录必须在调用方事务内创建。', 500)
    card = CardService._card(db, user, card_id)  # 404 missing/foreign, 409 ambiguous; before any write
    if card['id'] != card_id or card['storage_kind'] != storage_kind:
        raise _conflict('卡片控制记录存在歧义，请先核对原记录。')
    db.execute('INSERT OR IGNORE INTO card_controls(card_id,user_id,storage_kind) VALUES(?,?,?)',
               (card_id, user, storage_kind))
    row = db.execute('SELECT user_id,storage_kind FROM card_controls WHERE card_id=?', (card_id,)).fetchone()
    if row is None or row['user_id'] != user or row['storage_kind'] != storage_kind:
        raise _conflict('卡片控制记录存在歧义，请先核对原记录。')


def _card_id_of(raw):
    """Minimal parse for any card-related op type; malformed fails closed."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise _conflict('卡片操作数据无效，请先核对原记录。') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('card_id'), str):
        raise _conflict('卡片操作数据无效，请先核对原记录。')
    return payload['card_id']


def assert_card_reconciled(db, user, card_id, exclude=None):
    """Block while any own unresolved card op targets this card. UNKNOWN/EXECUTING/MANUAL_REVIEW
    are never excluded by id; exclude is accepted for signature compatibility and ignored."""
    marks = ','.join('?' * len(_CARD_TYPES))
    rows = db.execute(f"""SELECT id,status,payload FROM operations WHERE user_id=?
        AND type IN ({marks}) AND status IN ('UNKNOWN','EXECUTING','MANUAL_REVIEW')""",
        (user, *_CARD_TYPES)).fetchall()
    for pending in rows:
        if _card_id_of(pending['payload']) == card_id:
            raise DomainError('CARD_RESULT_PENDING', '这张卡还有结果待核对的操作，请先查看原回执。', 409)


def _validate_intent(action, value, version=None, check_version=True):
    if not isinstance(action, str) or action not in _ACTIONS:
        raise DomainError('INVALID_ACTION', '卡片控制操作无效。', 400)
    if action == 'lock' and value is not True:
        raise DomainError('INVALID_VALUE', '锁定参数必须为 true。', 400)
    if action == 'unlock' and value is not False:
        raise DomainError('INVALID_VALUE', '解锁参数必须为 false。', 400)
    if action.startswith('set_') and (not _strict_int(value) or not 1 <= value <= MAX_CENTS):
        raise DomainError('INVALID_AMOUNT', '限额必须为 1 至 5000000 分的整数。', 400)
    if check_version and (not _strict_int(version) or version < 1):
        raise DomainError('INVALID_VERSION', '版本必须为正整数。', 400)


def _derive(before, action, value):
    after = dict(before)
    after[_ACTIONS[action]] = value
    if after == before:
        raise DomainError('NO_CHANGE', '卡片控制未发生变化。', 400)
    if after['single_limit_cents'] > after['daily_limit_cents']:
        raise DomainError('INVALID_LIMIT', '单笔限额不能高于日限额。', 400)
    return after


def _strict_state(state):
    if not isinstance(state, dict) or set(state) != _STATE or type(state['locked']) is not bool:
        raise _conflict('操作数据无效。', 'INVALID_OPERATION')
    for f in ('single_limit_cents', 'daily_limit_cents'):
        if not _strict_int(state[f]) or not 1 <= state[f] <= MAX_CENTS:
            raise _conflict('操作数据无效。', 'INVALID_OPERATION')
    return state


def _intent_of(payload):
    """Return (card_id, action, value, version) of a stored control payload, strictly."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD or payload['simulated'] is not True:
        raise _conflict('操作数据不完整。', 'INVALID_OPERATION')
    action = payload['action']
    if not isinstance(action, str) or action not in _ACTIONS:
        raise _conflict('操作数据无效。', 'INVALID_OPERATION')
    after = _strict_state(payload['after'])
    _strict_state(payload['before'])
    version = payload['expected_control_version']
    if not _strict_int(version) or version < 1 or not isinstance(payload['card_id'], str):
        raise _conflict('操作数据无效。', 'INVALID_OPERATION')
    return payload['card_id'], action, after[_ACTIONS[action]], version


class CardControls:
    def __init__(self, bank):
        self.bank = bank  # no DDL here; bootstrap is root's job

    # ---- reads -------------------------------------------------------
    @staticmethod
    def _controls(db, user, card):
        row = db.execute('SELECT * FROM card_controls WHERE card_id=?', (card['id'],)).fetchone()
        if row is None or row['user_id'] != user or row['storage_kind'] != card['storage_kind']:
            raise _conflict('卡片控制记录缺失或存在歧义，请先核对原记录。')  # never lazy-create
        if row['locked'] not in (0, 1) or not _strict_int(row['control_version']):
            raise _conflict('卡片控制记录无效。')
        before = {'locked': bool(row['locked']), 'single_limit_cents': row['single_limit_cents'],
                  'daily_limit_cents': row['daily_limit_cents']}
        return _strict_state(before), row['control_version']

    def _today(self):
        now = self.bank.now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise DomainError('CLOCK_INVALID', '系统时间无效。', 500)
        return now.date().isoformat()

    def _spent_today(self, db, user, card_id):
        from .card_spend_rules import settled_cents
        return settled_cents(db, user, self.bank.now(), card_id)

    def _view(self, db, user, card_id):
        card = CardService._card(db, user, card_id)
        state, version = self._controls(db, user, card)
        return {'card_id': card['id'], **state, 'control_version': version,
                'spent_today_cents': self._spent_today(db, user, card['id']), 'simulated': True}

    def get_controls(self, user, card_id):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            return self._view(db, user, card_id)

    def list_controls(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            ids = [r['id'] for r in db.execute("""SELECT DISTINCT c.id FROM bank_all_cards c
                JOIN accounts a ON a.id=c.account_id AND a.user_id=c.user_id
                WHERE c.user_id=? ORDER BY c.id""", (user,))]
            return [self._view(db, user, i) for i in ids]

    # ---- prepare -----------------------------------------------------
    @staticmethod
    def _by_key(db, user, key):
        cols = {r['name'] for r in db.execute('PRAGMA table_info(operations)')}
        col = next((c for c in _KEY_COLUMNS if c in cols), None)
        if col is None:
            raise DomainError('IDEMPOTENCY_UNAVAILABLE', '无法校验幂等键。', 500)  # fail closed
        rows = db.execute(f'SELECT * FROM operations WHERE user_id=? AND "{col}"=?', (user, key)).fetchall()
        if len(rows) > 1:
            raise _conflict('幂等记录存在歧义。', 'IDEMPOTENCY_CONFLICT')
        return rows[0] if rows else None

    def _created(self, created):
        return created if isinstance(created, dict) else self.bank._view(created)

    def prepare_control(self, user, card_id, action, value, expected_version, key, plan_ticket=None):
        if not isinstance(card_id, str) or not card_id:
            raise DomainError('INVALID_CARD', '卡片编号无效。', 400)
        if not isinstance(key, str) or not key:
            raise DomainError('INVALID_KEY', '幂等键无效。', 400)
        _validate_intent(action, value, expected_version)
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key) if plan_ticket is not None else None
            existing = self._by_key(db, user, key)
            if existing is not None:  # bind intent BEFORE fresh checks; replay read-only
                if existing['type'] != TYPE:
                    raise _conflict('幂等键已用于其他操作。', 'IDEMPOTENCY_CONFLICT')
                try:
                    stored = json.loads(existing['payload'])
                    intent = _intent_of(stored)
                except (TypeError, ValueError, DomainError):
                    raise _conflict('原操作数据无效，请先核对原回执。', 'IDEMPOTENCY_CONFLICT') from None
                if intent != (card_id, action, value, expected_version):
                    raise _conflict('幂等键对应的操作内容已改变。', 'IDEMPOTENCY_CONFLICT')
                # Route through _create so Bank owner/key binding is preserved, not bypassed.
                return self._created(self.bank._create(db, user, TYPE, stored, 'RED', key, owner=owner))
            card = CardService._card(db, user, card_id)
            before, version = self._controls(db, user, card)
            if version != expected_version:
                raise _conflict()
            after = _derive(before, action, value)
            assert_card_reconciled(db, user, card['id'])
            self.bank._unlocked(db, user)
            payload = {'card_id': card['id'], 'account_id': card['account_id'], 'card_name': card['name'],
                       'masked_number': card['masked_number'], 'storage_kind': card['storage_kind'],
                       'expected_control_version': version, 'action': action,
                       'before': before, 'after': after, 'simulated': True}
            return self._created(self.bank._create(db, user, TYPE, payload, 'RED', key, owner=owner))

    # ---- execute (caller-owned transaction, dispatched by Bank after verify) ----
    def execute(self, db, user, row):
        cur = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (row['id'], user)).fetchone()
        if cur is None:
            raise DomainError('NOT_FOUND', '未找到可访问的数据。', 404)
        if cur['type'] != TYPE or cur['risk'] != 'RED' or cur['status'] != 'WAITING_VERIFICATION' \
                or cur['confirmed_at'] is None:
            raise _conflict('操作状态无效。', 'INVALID_OPERATION')
        if cur['expires_at'] <= self.bank.now().timestamp():
            raise DomainError('OPERATION_EXPIRED', '操作已过期。', 409)
        raw = cur['payload']
        if raw != row['payload']:
            raise _conflict('操作数据已改变。', 'INVALID_OPERATION')
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            raise _conflict('操作数据无效。', 'INVALID_OPERATION') from None
        if _canonical(payload) != raw:
            raise _conflict('操作数据无效。', 'INVALID_OPERATION')
        card_id, action, value, expected = _intent_of(payload)
        _validate_intent(action, value, check_version=False)
        if _derive(payload['before'], action, value) != payload['after']:
            raise _conflict('操作数据无效。', 'INVALID_OPERATION')
        card = CardService._card(db, user, card_id)
        if (card['id'], card['account_id'], card['name'], card['masked_number'], card['storage_kind']) != \
                (payload['card_id'], payload['account_id'], payload['card_name'],
                 payload['masked_number'], payload['storage_kind']):
            raise _conflict('卡片信息已改变，请刷新。')
        before, version = self._controls(db, user, card)
        if before != payload['before'] or version != expected:
            raise _conflict()
        assert_card_reconciled(db, user, card_id, exclude=cur['id'])
        self.bank._unlocked(db, user)
        after = payload['after']
        updated = db.execute("""UPDATE card_controls SET locked=?,single_limit_cents=?,daily_limit_cents=?,
            control_version=control_version+1 WHERE card_id=? AND user_id=? AND control_version=?""",
            (int(after['locked']), after['single_limit_cents'], after['daily_limit_cents'],
             card_id, user, version)).rowcount
        if updated != 1:
            raise _conflict()
        result = {'card_id': card_id, **after, 'control_version': version + 1, 'simulated': True}
        self.bank._event(db, user, cur['id'], 'CARD_CONTROLS_CHANGED',
                         _canonical({'card_id': card_id, 'action': action, 'before': payload['before'],
                          'after': after, 'control_version': version + 1, 'simulated': True}))
        return result  # Bank finalizes SUCCEEDED; no funds/transactions writes here
