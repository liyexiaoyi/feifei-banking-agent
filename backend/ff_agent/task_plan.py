'''Persistent plans; no execution, banking, or automatic replay.

Only trusted application code may supply settlement results. Actions must carry
an explicit nonempty type. Frozen graphs must impose a total dependency order.
Only definite planning failures may resume before freeze. Resuming completed
work yields DONE. Planning ownership is checked when freezing or reporting a
planning failure; orphan marking is explicit and never proves a worker stopped.
An orphan remains unsettled: its original token may report a definitive result,
but a different session is never treated as proof that its worker has stopped.
Audit, on_stop, and on_resume callbacks must use the supplied connection without
committing, rolling back, reentering this store, or performing external I/O.
'''

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

__all__ = ['PlanStore', 'PlanError']


class PlanError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _conflict():
    raise PlanError('PLAN_CONFLICT', '计划状态已变化或不允许此操作。')


def _string(value, empty=False):
    if not isinstance(value, str) or (not empty and not value):
        raise PlanError('INVALID_ARGUMENT', '参数格式无效。', 400)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False,
                          sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise PlanError('INVALID_ARGUMENT', '参数必须是有效的 JSON 数据。', 400) from None


class PlanStore:
    def __init__(self, path, clock=None, audit=None):
        self.clock = time.time if clock is None else clock
        self.audit = audit
        self.boot_session = uuid.uuid4().hex
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(path, timeout=5, isolation_level=None,
                                       check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA foreign_keys = ON')
            with self._tx() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS plan_store_plans (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL,
                    request_key TEXT NOT NULL, text_hash TEXT NOT NULL,
                    risk_epoch TEXT NOT NULL, version INTEGER NOT NULL,
                    status TEXT NOT NULL, reason TEXT, frozen INTEGER NOT NULL,
                    created_at REAL NOT NULL, planning_session TEXT,
                    UNIQUE(user, request_key))''')
                columns = {column['name'] for column in
                           db.execute('PRAGMA table_info(plan_store_plans)')}
                if 'planning_session' not in columns:
                    db.execute('''ALTER TABLE plan_store_plans
                        ADD COLUMN planning_session TEXT''')
                db.execute('''CREATE TABLE IF NOT EXISTS plan_store_nodes (
                    plan_id TEXT NOT NULL REFERENCES plan_store_plans(id),
                    idx INTEGER NOT NULL, kind TEXT NOT NULL, action TEXT NOT NULL,
                    deps TEXT NOT NULL, effect_key TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, error_code TEXT, token TEXT, boot_session TEXT,
                    PRIMARY KEY(plan_id, idx))''')
                db.execute('''CREATE TABLE IF NOT EXISTS plan_store_attempts (
                    token TEXT PRIMARY KEY, plan_id TEXT NOT NULL, idx INTEGER NOT NULL,
                    settled INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(plan_id, idx) REFERENCES plan_store_nodes(plan_id, idx))''')
                db.execute('''CREATE INDEX IF NOT EXISTS plan_store_running
                    ON plan_store_nodes(state, boot_session, plan_id)''')
        except Exception:
            if hasattr(self, '_db'):
                self._db.close()
            raise PlanError('PLAN_STORE_FAILURE', '计划存储暂时不可用。', 500) from None

    @contextmanager
    def _tx(self, write=True):
        with self._lock:
            if self._db.in_transaction:
                _conflict()
            try:
                self._db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield self._db
                self._db.commit()
            except BaseException as exc:
                if self._db.in_transaction:
                    self._db.rollback()
                if isinstance(exc, PlanError) or not isinstance(exc, Exception):
                    raise
                raise PlanError('PLAN_STORE_FAILURE', '计划存储暂时不可用。', 500) from None

    def close(self):
        with self._lock:
            if self._db.in_transaction:
                _conflict()
            self._db.close()

    def _row(self, db, user, plan_id):
        _string(user)
        _string(plan_id)
        row = db.execute('SELECT * FROM plan_store_plans WHERE user=? AND id=?',
                         (user, plan_id)).fetchone()
        if row is None:
            raise PlanError('PLAN_NOT_FOUND', '计划不存在。', 404)
        return row

    def _view(self, db, row):
        plan = {key: row[key] for key in
                ('id', 'request_key', 'risk_epoch', 'version', 'status', 'reason')}
        plan['nodes'] = []
        for node in db.execute('SELECT * FROM plan_store_nodes WHERE plan_id=? ORDER BY idx',
                               (row['id'],)):
            item = {key: node[key] for key in
                    ('idx', 'kind', 'effect_key', 'state', 'error_code', 'token')}
            for key in ('action', 'deps', 'result'):
                item[key] = json.loads(node[key]) if node[key] is not None else None
            plan['nodes'].append(item)
        return plan

    def _emit(self, db, user, event, plan):
        if self.audit is not None:
            self.audit(db, user, event, {'plan_id': plan['id'],
                                        'version': plan['version'],
                                        'status': plan['status']})

    def _changed(self, db, row, status, reason, event):
        changed = db.execute('''UPDATE plan_store_plans SET version=version+1,
            status=?, reason=? WHERE id=? AND user=? AND version=?''',
            (status, reason, row['id'], row['user'], row['version']))
        if changed.rowcount != 1:
            _conflict()
        plan = self._view(db, self._row(db, row['user'], row['id']))
        self._emit(db, row['user'], event, plan)
        return plan

    def _finish_planning(self, db, row, status, reason, event, frozen):
        changed = db.execute('''UPDATE plan_store_plans
            SET version=version+1, status=?, reason=?, frozen=?
            WHERE id=? AND user=? AND version=? AND status=?
                AND planning_session=? AND frozen=0''',
            (status, reason, frozen, row['id'], row['user'], row['version'],
             'PLANNING', self.boot_session))
        if changed.rowcount != 1:
            _conflict()
        plan = self._view(db, self._row(db, row['user'], row['id']))
        self._emit(db, row['user'], event, plan)
        return plan

    def _cas(self, row, version, epoch):
        _string(epoch, empty=True)
        if epoch != row['risk_epoch']:
            raise PlanError('REQUEST_SUPERSEDED', '风险评估代次已变化。')
        if type(version) is not int or version != row['version']:
            _conflict()

    def reserve(self, user, key, text, epoch):
        _string(user)
        _string(key)
        _string(text, empty=True)
        _string(epoch, empty=True)
        try:
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        except UnicodeError:
            raise PlanError('INVALID_ARGUMENT', '正文编码无效。', 400) from None
        with self._tx() as db:
            row = db.execute('SELECT * FROM plan_store_plans WHERE user=? AND request_key=?',
                             (user, key)).fetchone()
            if row is not None:
                if row['text_hash'] != digest:
                    raise PlanError('IDEMPOTENCY_CONFLICT', '请求标识已用于不同正文。')
                return self._view(db, row), False
            plan_id = uuid.uuid4().hex
            db.execute('''INSERT INTO plan_store_plans
                (id,user,request_key,text_hash,risk_epoch,version,status,reason,
                 frozen,created_at,planning_session)
                VALUES (?,?,?,?,?,1,?,NULL,0,?,?)''',
                (plan_id, user, key, digest, epoch, 'PLANNING',
                 float(self.clock()), self.boot_session))
            plan = self._view(db, self._row(db, user, plan_id))
            self._emit(db, user, 'PLAN_RESERVED', plan)
            return plan, True

    def get(self, user, id):
        with self._tx(False) as db:
            return self._view(db, self._row(db, user, id))

    def snapshot(self, db, user, id):
        """Use a caller-owned transaction to compose one consistent public view."""
        if not db.in_transaction:
            _conflict()
        return self._view(db, self._row(db, user, id))

    def find(self, user, key):
        _string(user)
        _string(key)
        with self._tx(False) as db:
            row = db.execute('SELECT * FROM plan_store_plans WHERE user=? AND request_key=?',
                             (user, key)).fetchone()
            if row is None:
                raise PlanError('PLAN_NOT_FOUND', '计划不存在。', 404)
            return self._view(db, row)

    def list(self, user, limit=20):
        _string(user)
        if type(limit) is not int or not 0 <= limit <= 50:
            raise PlanError('INVALID_ARGUMENT', '列表数量必须是有效的非负整数。', 400)
        with self._tx(False) as db:
            rows = db.execute('''SELECT * FROM plan_store_plans WHERE user=?
                ORDER BY created_at DESC, id DESC LIMIT ?''', (user, limit)).fetchall()
            return [self._view(db, row) for row in rows]

    def _validate_nodes(self, nodes):
        def invalid():
            raise PlanError('INVALID_PLAN',
                            '计划须含一至六个显式串行依赖节点，动作须含有效 type。', 400)
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 6:
            invalid()
        items = {}
        for node in nodes:
            if not isinstance(node, dict) or set(node) != {'idx', 'action', 'deps'}:
                invalid()
            idx, action, deps = node['idx'], node['action'], node['deps']
            if type(idx) is not int or idx in items or not 0 <= idx < len(nodes):
                invalid()
            if not isinstance(action, dict) or not isinstance(action.get('type'), str):
                invalid()
            if not action['type'] or not isinstance(deps, list):
                invalid()
            if any(type(d) is not int or not 0 <= d < len(nodes) or d == idx for d in deps):
                invalid()
            if len(set(deps)) != len(deps):
                invalid()
            items[idx] = (action['type'], _json(action), _json(deps), tuple(deps))
        ancestors, visiting = {}, set()
        def visit(idx):
            if idx in visiting:
                invalid()
            if idx not in ancestors:
                visiting.add(idx)
                found = set()
                for dep in items[idx][3]:
                    found.add(dep)
                    found.update(visit(dep))
                visiting.remove(idx)
                ancestors[idx] = found
            return ancestors[idx]
        for idx in items:
            visit(idx)
        for i in items:
            for j in items:
                if i != j and i not in ancestors[j] and j not in ancestors[i]:
                    invalid()
        return items

    def freeze(self, user, id, version, nodes):
        items = self._validate_nodes(nodes)
        with self._tx() as db:
            row = self._row(db, user, id)
            if type(version) is not int or version != row['version']:
                _conflict()
            if (row['status'] != 'PLANNING' or row['frozen']
                    or row['planning_session'] != self.boot_session):
                _conflict()
            for idx in sorted(items):
                kind, action, deps, _ = items[idx]
                db.execute('''INSERT INTO plan_store_nodes
                    (plan_id,idx,kind,action,deps,effect_key,state) VALUES (?,?,?,?,?,?,?)''',
                    (id, idx, kind, action, deps, 'task-' + id + '-' + str(idx), 'PENDING'))
            return self._finish_planning(db, row, 'RUNNING', None, 'PLAN_FROZEN', 1)

    def planning_failed(self, user, id, version):
        """Only the planning caller may report a definite, effect-free failure."""
        with self._tx() as db:
            row = self._row(db, user, id)
            self._cas(row, version, row['risk_epoch'])
            if (row['status'] != 'PLANNING' or row['frozen']
                    or row['planning_session'] != self.boot_session):
                _conflict()
            return self._finish_planning(
                db, row, 'PAUSED', 'PLANNING_FAILED', 'PLAN_PLANNING_FAILED', 0)

    def claim(self, user, id, idx, version, epoch):
        with self._tx() as db:
            row = self._row(db, user, id)
            self._cas(row, version, epoch)
            if row['status'] != 'RUNNING' or type(idx) is not int:
                _conflict()
            nodes = {n['idx']: n for n in self._view(db, row)['nodes']}
            node = nodes.get(idx)
            if node is None or node['state'] != 'PENDING':
                _conflict()
            if any(n['state'] in ('RUNNING', 'ORPHAN') for n in nodes.values()):
                _conflict()
            if any(nodes[d]['state'] not in ('DONE', 'PREPARED') for d in node['deps']):
                _conflict()
            token = uuid.uuid4().hex
            db.execute('''UPDATE plan_store_nodes SET state=?, token=?, boot_session=?,
                result=NULL, error_code=NULL WHERE plan_id=? AND idx=?''',
                ('RUNNING', token, self.boot_session, id, idx))
            db.execute('INSERT INTO plan_store_attempts(token,plan_id,idx) VALUES (?,?,?)',
                       (token, id, idx))
            return self._changed(db, row, 'RUNNING', None, 'PLAN_NODE_CLAIMED'), token

    def settle(self, user, id, idx, token, state, result=None, error_code=None):
        with self._tx() as db:
            row = self._row(db, user, id)
            if type(idx) is not int:
                _conflict()
            _string(token)
            attempt = db.execute('''SELECT * FROM plan_store_attempts
                WHERE token=? AND plan_id=? AND idx=?''', (token, id, idx)).fetchone()
            if attempt is None:
                _conflict()
            if attempt['settled']:
                return self._view(db, row)
            if state not in ('DONE', 'PREPARED', 'BLOCKED', 'FAILED'):
                _conflict()
            if error_code is not None:
                _string(error_code)
            encoded = None if result is None else _json(result)
            changed = db.execute('''UPDATE plan_store_nodes SET state=?, result=?, error_code=?
                WHERE plan_id=? AND idx=? AND token=? AND state IN (?,?)''',
                (state, encoded, error_code, id, idx, token, 'RUNNING', 'ORPHAN'))
            if changed.rowcount != 1:
                _conflict()
            db.execute('UPDATE plan_store_attempts SET settled=1 WHERE token=?', (token,))
            # A business refusal blocks its explicit dependents, without running them.
            for _ in range(6):
                nodes = self._view(db, row)['nodes']
                blocked = {n['idx'] for n in nodes if n['state'] == 'BLOCKED'}
                for node in nodes:
                    if node['state'] == 'PENDING' and any(d in blocked for d in node['deps']):
                        db.execute("UPDATE plan_store_nodes SET state='BLOCKED', error_code='DEPENDENCY_BLOCKED' WHERE plan_id=? AND idx=?", (id, node['idx']))
            status, reason = row['status'], row['reason']
            if state == 'FAILED' and status not in ('DONE', 'HANDOFF', 'CANCELLED'):
                status, reason = 'PAUSED', 'TOOL_FAILURE'
            elif status == 'RUNNING' and self._complete(db, id):
                status, reason = 'DONE', None
            return self._changed(db, row, status, reason, 'PLAN_NODE_SETTLED')

    def _complete(self, db, id):
        states = [n[0] for n in db.execute(
            'SELECT state FROM plan_store_nodes WHERE plan_id=?', (id,))]
        return bool(states) and all(s in ('DONE', 'PREPARED', 'BLOCKED') for s in states)

    def control(self, user, id, version, action, epoch, on_stop=None, on_resume=None, stoppable=None):
        with self._tx() as db:
            row = self._row(db, user, id)
            self._cas(row, version, epoch)
            if action not in ('pause', 'resume', 'handoff', 'cancel'):
                _conflict()
            if row['status'] in ('HANDOFF', 'CANCELLED'):
                _conflict()
            if row['status'] == 'DONE':
                if action not in ('cancel', 'handoff') or stoppable is None:
                    _conflict()
                if stoppable(db, self._view(db, row)) is not True:
                    _conflict()
            if action == 'resume':
                if row['status'] != 'PAUSED' or (not row['frozen'] and row['reason'] != 'PLANNING_FAILED'):
                    _conflict()
                if db.execute('''SELECT 1 FROM plan_store_nodes
                    WHERE plan_id=? AND state IN (?,?) LIMIT 1''',
                    (id, 'RUNNING', 'ORPHAN')).fetchone():
                    _conflict()
                db.execute('''UPDATE plan_store_nodes SET state=?, result=NULL,
                    error_code=NULL, token=NULL, boot_session=NULL WHERE plan_id=? AND state=?''',
                    ('PENDING', id, 'FAILED'))
                status = 'RUNNING' if row['frozen'] else 'PLANNING'
                if row['frozen'] and self._complete(db, id):
                    status = 'DONE'
                if status == 'PLANNING':
                    changed = db.execute('''UPDATE plan_store_plans
                        SET planning_session=?
                        WHERE id=? AND user=? AND version=? AND status=?
                            AND frozen=0 AND reason=?''',
                        (self.boot_session, id, user, row['version'],
                         'PAUSED', 'PLANNING_FAILED'))
                    if changed.rowcount != 1:
                        _conflict()
            else:
                if action == 'pause' and row['status'] not in ('RUNNING', 'PLANNING'):
                    _conflict()
                status = {'pause': 'PAUSED', 'handoff': 'HANDOFF', 'cancel': 'CANCELLED'}[action]
            event = 'LOCAL_HANDOFF' if action == 'handoff' else 'PLAN_' + action.upper()
            reason = 'PLANNING_FAILED' if status == 'PLANNING' and row['reason'] == 'PLANNING_FAILED' else None
            plan = self._changed(db, row, status, reason, event)
            if action != 'resume' and on_stop is not None:
                on_stop(db, self._view(db, self._row(db, user, id)))
            if action == 'resume' and on_resume is not None:
                on_resume(db, plan)
            return plan

    def mark_orphans(self):
        with self._tx(False) as db:
            candidates = db.execute('''SELECT p.user, p.id
                FROM plan_store_plans p
                WHERE (p.status=? AND
                    (p.planning_session IS NULL OR p.planning_session<>?))
                    OR EXISTS (
                        SELECT 1 FROM plan_store_nodes n
                        WHERE n.plan_id=p.id AND n.state=?
                            AND (n.boot_session IS NULL OR n.boot_session<>?))''',
                ('PLANNING', self.boot_session, 'RUNNING', self.boot_session)).fetchall()
        plans = []
        for candidate in candidates:
            with self._tx() as db:
                row = self._row(db, candidate['user'], candidate['id'])
                planning_orphan = (row['status'] == 'PLANNING'
                                   and row['planning_session'] != self.boot_session)
                changed = db.execute('''UPDATE plan_store_nodes SET state=?
                    WHERE plan_id=? AND state=? AND (boot_session IS NULL OR boot_session<>?)''',
                    ('ORPHAN', row['id'], 'RUNNING', self.boot_session))
                if not planning_orphan and not changed.rowcount:
                    continue
                status, reason = row['status'], row['reason']
                if status not in ('DONE', 'HANDOFF', 'CANCELLED'):
                    status, reason = 'PAUSED', 'PROCESS_INTERRUPTED'
                plans.append(self._changed(db, row, status, reason, 'PLAN_ORPHANED'))
        return plans
