"""Local demo card credential (not a bank PIN). Only PBKDF2 derived values are stored."""
import hashlib
import hmac
import json
import re
import secrets

from .bank import DomainError

KIND = 'card_credential_change'
ITERATIONS = 600000
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_KEY = re.compile(r'[A-Za-z0-9_-]{8,64}')
_FIELDS = ('card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind',
           'expected_card_version', 'expected_credential_version', 'challenge_id',
           'risk_epoch', 'simulated')
_UNRESOLVED = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')


def bootstrap_card_credentials(bank):
    with bank.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS card_credentials (
            card_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            version INTEGER NOT NULL CHECK (version >= 1),
            salt TEXT NOT NULL,
            verifier TEXT NOT NULL,
            updated_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS card_credential_challenges (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            card_id TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            salt TEXT,
            verifier TEXT,
            payload_hash TEXT NOT NULL,
            expires_at REAL NOT NULL,
            consumed_at REAL)""")


def _fail(code='CREDENTIAL_CONFLICT', message='演示口令请求无法继续，请刷新后核对。', status=409):
    return DomainError(code, message, status)


def _valid_secret(value):
    return isinstance(value, str) and 8 <= len(value) <= 64 and all(33 <= ord(c) <= 126 for c in value)


def _valid_key(value):
    return isinstance(value, str) and _KEY.fullmatch(value) is not None


def _derive(secret, salt):
    return hashlib.pbkdf2_hmac('sha256', secret.encode('ascii'), salt, ITERATIONS, 32)


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _digest(encoded):
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _card(db, user, card_id):
    if not isinstance(card_id, str):
        raise DomainError('NOT_FOUND', '未找到可访问的数据。', 404)
    rows = db.execute("""SELECT c.* FROM bank_all_cards c
        JOIN accounts a ON a.id=c.account_id AND a.user_id=c.user_id
        WHERE c.id=? AND c.user_id=?""", (card_id, user)).fetchall()
    if not rows:
        raise DomainError('NOT_FOUND', '未找到可访问的数据。', 404)
    if len(rows) != 1:
        raise DomainError('CARD_CONFLICT', '卡片来源存在歧义，请先核对原记录。', 409)
    return dict(rows[0])


def _pick(card, *names):
    for name in names:
        if name in card:
            return card[name]
    return None


def _live(db, user, card_id):
    row = db.execute('SELECT * FROM card_credentials WHERE card_id=? AND user_id=?', (card_id, user)).fetchone()
    if row is None:
        return None
    row = dict(row)
    if (type(row['version']) is not int or row['version'] < 1 or not isinstance(row['salt'], str)
            or not isinstance(row['verifier'], str) or not _HEX32.fullmatch(row['salt'])
            or not _HEX64.fullmatch(row['verifier'])):
        raise _fail()
    return row


def _assert_card_ready(bank, db, user, card_id):
    from .card_controls import assert_card_reconciled
    if bank._safety_hold(db, user):
        raise DomainError('SAFETY_HOLD', '发现风险线索，敏感操作已暂停。', 409)
    assert_card_reconciled(db, user, card_id)
    marks = ','.join('?' * len(_UNRESOLVED))
    rows = db.execute(f'SELECT payload FROM operations WHERE user_id=? AND type=? AND status IN ({marks})',
                      (user, KIND, *_UNRESOLVED)).fetchall()
    for row in rows:
        try:
            target = json.loads(row['payload']).get('card_id')
        except (TypeError, ValueError, AttributeError):
            raise _fail('CARD_RESULT_PENDING', '这张卡还有结果待核对的操作，请先查看原回执。') from None
        if target == card_id:
            raise _fail('CARD_RESULT_PENDING', '这张卡还有结果待核对的操作，请先查看原回执。')


def _view(card_id, live):
    return {'card_id': card_id, 'configured': live is not None,
            'credential_version': live['version'] if live else 0,
            'simulated': True, 'bank_connected': False}


class CardCredentials:
    def __init__(self, bank):
        self.bank = bank

    def get(self, user, card_id):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            card = _card(db, user, card_id)
            return _view(card['id'], _live(db, user, card['id']))

    def prepare(self, user, card_id, new_secret, current_secret, expected_version, key):
        if not _valid_secret(new_secret):
            raise _fail('INVALID_SECRET', '演示口令须为8到64位可打印ASCII字符，不能含空格。', 400)
        if current_secret is not None and not _valid_secret(current_secret):
            raise _fail('INVALID_SECRET', '当前演示口令格式无效。', 400)
        if type(expected_version) is not int or expected_version < 0:
            raise _fail('INVALID_VERSION', '口令版本无效，请刷新。', 400)
        if not _valid_key(key):
            raise _fail('INVALID_KEY', '请求编号无效。', 400)
        if key.startswith('task-'):
            raise _fail('PLAN_CONTEXT_REQUIRED', '演示口令只能在专用表单中设置。')
        bank = self.bank
        with bank.transaction() as db:
            bank._user(db, user)
            if db.execute('SELECT 1 FROM operations WHERE user_id=? AND idempotency_key=?', (user, key)).fetchone():
                raise _fail('REQUEST_ALREADY_EXISTS', '该请求编号已存在，请先只读查询原请求结果。')
            bank._expire(db, user)
            card = _card(db, user, card_id)
            if card['status'] != 'ACTIVE':
                raise DomainError('CARD_CONFLICT', '卡片当前状态不能设置演示口令。', 409)
            bank._unlocked(db, user)
            _assert_card_ready(bank, db, user, card['id'])
            live = _live(db, user, card['id'])
            if (live['version'] if live else 0) != expected_version:
                raise _fail('CREDENTIAL_CONFLICT', '演示口令版本已改变，请刷新。')
            if live is None:
                if current_secret is not None:
                    raise _fail('INVALID_REQUEST', '首次设置不需要当前演示口令。', 400)
            else:
                if current_secret is None:
                    raise _fail('CURRENT_SECRET_REQUIRED', '修改前须核对当前演示口令。', 400)
                actual = _derive(current_secret, bytes.fromhex(live['salt']))
                if not hmac.compare_digest(actual, bytes.fromhex(live['verifier'])):
                    from .verification import record_failure
                    streak = record_failure(db, user, bank.now().timestamp())
                    db.commit()
                    raise DomainError('INVALID_VERIFICATION',
                                      '当前演示口令不正确。敏感操作已暂时锁定10分钟。' if streak >= 3
                                      else '当前演示口令不正确。连续3次错误将锁定敏感操作10分钟。', 403)
            challenge_id = 'cc_' + secrets.token_hex(16)
            payload = {'card_id': card['id'], 'account_id': card['account_id'],
                       'card_name': _pick(card, 'card_name', 'name'),
                       'masked_number': _pick(card, 'masked_number', 'masked', 'number_masked'),
                       'storage_kind': _pick(card, 'storage_kind', 'kind', 'source'),
                       'expected_card_version': card['version'],
                       'expected_credential_version': expected_version,
                       'challenge_id': challenge_id, 'risk_epoch': bank._safety_epoch(db, user),
                       'simulated': True}
            salt = secrets.token_bytes(16)
            verifier = _derive(new_secret, salt).hex()
            op = bank._create(db, user, KIND, payload, 'RED', key)
            db.execute("""INSERT INTO card_credential_challenges
                (id,user_id,card_id,operation_id,salt,verifier,payload_hash,expires_at,consumed_at)
                VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (challenge_id, user, card['id'], op['id'], salt.hex(), verifier,
                 _digest(_canonical(payload)), op['expires_at']))
            return op

    def execute(self, db, user, row):
        if not db.in_transaction:
            raise _fail('INVALID_EXECUTION', '演示口令须在银行事务内更新。')
        bank = self.bank
        now = bank.now().timestamp()
        op = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (dict(row)['id'], user)).fetchone()
        if op is None:
            raise DomainError('NOT_FOUND', '未找到可访问的操作', 404)
        op = dict(op)
        if (op['type'] != KIND or op['risk'] != 'RED' or op['status'] != 'WAITING_VERIFICATION'
                or op['confirmed_at'] is None or op['expires_at'] <= now or op['payload'] != dict(row)['payload']):
            raise _fail()
        try:
            payload = json.loads(op['payload'])
        except (TypeError, ValueError):
            raise _fail() from None
        if (not isinstance(payload, dict) or set(payload) != set(_FIELDS) or _canonical(payload) != op['payload']
                or payload['simulated'] is not True or type(payload['expected_card_version']) is not int
                or type(payload['expected_credential_version']) is not int or payload['expected_credential_version'] < 0):
            raise _fail()
        cand = db.execute('SELECT * FROM card_credential_challenges WHERE operation_id=?', (op['id'],)).fetchone()
        if cand is None:
            raise _fail()
        cand = dict(cand)
        if (cand['user_id'] != user or cand['card_id'] != payload['card_id'] or cand['id'] != payload['challenge_id']
                or not hmac.compare_digest(str(cand['payload_hash']), _digest(op['payload']))
                or cand['consumed_at'] is not None or cand['expires_at'] <= now
                or not isinstance(cand['salt'], str) or not isinstance(cand['verifier'], str)
                or not _HEX32.fullmatch(cand['salt']) or not _HEX64.fullmatch(cand['verifier'])):
            raise _fail()
        card = _card(db, user, payload['card_id'])
        if (card['id'] != payload['card_id'] or card['account_id'] != payload['account_id']
                or card['version'] != payload['expected_card_version'] or card['status'] != 'ACTIVE'
                or _pick(card, 'card_name', 'name') != payload['card_name']
                or _pick(card, 'masked_number', 'masked', 'number_masked') != payload['masked_number']
                or _pick(card, 'storage_kind', 'kind', 'source') != payload['storage_kind']):
            raise DomainError('CARD_CONFLICT', '卡片状态或版本已改变，请刷新。', 409)
        bank._unlocked(db, user)
        if bank._safety_epoch(db, user) != payload['risk_epoch']:
            raise _fail('REQUEST_SUPERSEDED', '该请求早于最近一次风险提示，已失效。')
        _assert_card_ready(bank, db, user, card['id'])
        live = _live(db, user, card['id'])
        expected = payload['expected_credential_version']
        if (live['version'] if live else 0) != expected:
            raise _fail('CREDENTIAL_CONFLICT', '演示口令版本已改变，请刷新。')
        version = expected + 1
        if live is None:
            if expected != 0:
                raise _fail()
            db.execute('INSERT INTO card_credentials(card_id,user_id,version,salt,verifier,updated_at) VALUES(?,?,?,?,?,?)',
                       (card['id'], user, version, cand['salt'], cand['verifier'], now))
        else:
            done = db.execute('UPDATE card_credentials SET version=?,salt=?,verifier=?,updated_at=? WHERE card_id=? AND user_id=? AND version=?',
                              (version, cand['salt'], cand['verifier'], now, card['id'], user, expected))
            if done.rowcount != 1:
                raise _fail()
        used = db.execute('UPDATE card_credential_challenges SET salt=NULL,verifier=NULL,consumed_at=? WHERE id=? AND consumed_at IS NULL',
                          (now, cand['id']))
        if used.rowcount != 1:
            raise _fail()
        bank._event(db, user, op['id'], 'CARD_CREDENTIAL_CHANGED', f"{card['id']} / 演示口令版本 {version}")
        return {'card_id': card['id'], 'configured': True, 'credential_version': version,
                'simulated': True, 'bank_connected': False,
                'message': '本地演示口令已更新，仅用于演示，不是真实银行卡密码。'}


def discard_candidate(db, user, operation_id):
    db.execute('UPDATE card_credential_challenges SET salt=NULL,verifier=NULL WHERE user_id=? AND operation_id=?',
               (user, operation_id))


def purge_expired(db, user, now_timestamp):
    db.execute('UPDATE card_credential_challenges SET salt=NULL,verifier=NULL WHERE user_id=? AND expires_at<=? AND (salt IS NOT NULL OR verifier IS NOT NULL)',
               (user, now_timestamp))
