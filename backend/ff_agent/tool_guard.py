"""Persistent tool circuit breaker; install as ff_agent/tool_guard.py."""
from __future__ import annotations

import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, TypedDict

__all__ = ['ToolGuard', 'Ticket', 'ToolHealth', 'ToolPaused', 'ToolFailure']
GROUPS = ('planner', 'bill', 'transfer', 'subscriptions', 'cards', 'wealth', 'codegen')


@dataclass(frozen=True, slots=True)
class Ticket:
    id: str
    user: str
    group: str
    generation: str
    probe: bool


class ToolHealth(TypedDict):
    group: str
    state: str
    consecutive_failures: int
    retry_after_seconds: int
    probe_available: bool
    needs_handoff: bool
    version: str


class ToolPaused(Exception):
    code = 'TOOL_PAUSED'
    status_code = 503

    def __init__(self, group, retry_after_seconds, needs_handoff):
        self.group = group
        self.retry_after_seconds = retry_after_seconds
        self.needs_handoff = needs_handoff
        super().__init__(self.code)


class ToolFailure(Exception):
    code = 'TOOL_UNAVAILABLE'
    status_code = 503

    def __init__(self, group):
        self.group = group
        super().__init__(self.code)


class ToolGuard:
    def __init__(self, path, clock=None, audit=None, boot_session=None):
        """Open the guard without declaring any existing worker dead.

        Guards belonging to the same application boot may share a nonempty
        boot_session. If omitted, a fresh identifier is allocated for this
        instance. Neither opening the database nor allocating an identifier
        marks existing calls as orphaned; that requires mark_orphans().
        """
        if boot_session is None:
            boot_session = uuid.uuid4().hex
        if not isinstance(boot_session, str) or not boot_session:
            raise ValueError('boot_session must be a nonempty string')
        self._boot_session = boot_session
        self._clock = clock if clock is not None else time.time
        self._audit = audit
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(path), timeout=10, isolation_level=None, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        try:
            with self._tx() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS tool_health (
                    user_id TEXT NOT NULL,
                    tool_group TEXT NOT NULL,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    paused_until REAL NOT NULL DEFAULT 0,
                    probe_token TEXT,
                    probe_until REAL NOT NULL DEFAULT 0,
                    generation TEXT NOT NULL,
                    state TEXT NOT NULL,
                    PRIMARY KEY (user_id, tool_group)
                )''')
                db.execute('''CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tool_group TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    probe INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    settled_at REAL,
                    boot_session TEXT,
                    orphaned_at REAL
                )''')
                columns = {
                    row['name'] for row in db.execute('PRAGMA table_info(tool_calls)')
                }
                if 'boot_session' not in columns:
                    db.execute('ALTER TABLE tool_calls ADD COLUMN boot_session TEXT')
                if 'orphaned_at' not in columns:
                    db.execute('ALTER TABLE tool_calls ADD COLUMN orphaned_at REAL')
                db.execute('''CREATE INDEX IF NOT EXISTS tool_calls_pending_idx
                    ON tool_calls(user_id, tool_group, settled_at)''')
        except BaseException:
            self._db.close()
            raise

    @property
    def boot_session(self):
        return self._boot_session

    @contextmanager
    def _tx(self, write=True):
        with self._lock:
            db = self._db
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def close(self):
        with self._lock:
            self._db.close()

    @staticmethod
    def _key(user, group=None):
        if not isinstance(user, str) or not user:
            raise ValueError('user must be a nonempty string')
        if group is not None and group not in GROUPS:
            raise ValueError('unsupported tool group')

    def _now(self):
        now = float(self._clock())
        if not math.isfinite(now):
            raise ValueError('clock must return finite Unix seconds')
        return now

    def _emit(self, db, user, event, detail):
        if self._audit is not None:
            self._audit(db, user, event, detail)

    @staticmethod
    def _load(db, user, group):
        return db.execute(
            'SELECT * FROM tool_health WHERE user_id=? AND tool_group=?',
            (user, group),
        ).fetchone()

    def _ensure(self, db, user, group):
        db.execute('''INSERT OR IGNORE INTO tool_health
            (user_id, tool_group, generation, state) VALUES (?, ?, ?, 'READY')''',
            (user, group, uuid.uuid4().hex))
        return self._load(db, user, group)

    @staticmethod
    def _pending(db, user, group):
        return db.execute('''SELECT 1 FROM tool_calls
            WHERE user_id=? AND tool_group=? AND settled_at IS NULL LIMIT 1''',
            (user, group)).fetchone() is not None

    def mark_orphans(self, user=None, group=None) -> int:
        """Explicitly quarantine unsettled calls from other boot sessions.

        With no scope arguments, all users and groups are considered. Legacy
        calls with NULL boot_session are also considered previous-session
        calls. The return value is the number of newly marked calls.

        Orphaning expresses uncertainty about a call, not worker termination
        or settlement. It preserves settled_at, records orphaned_at, and
        fences affected groups with a fresh generation. Repeated invocation
        does not rotate generations for already marked calls. A registered
        ticket may still settle later, but cannot change the fenced health.
        """
        if user is not None:
            self._key(user, group)
        elif group is not None and group not in GROUPS:
            raise ValueError('unsupported tool group')

        conditions = [
            'settled_at IS NULL',
            'orphaned_at IS NULL',
            '(boot_session IS NULL OR boot_session <> ?)',
        ]
        parameters = [self._boot_session]
        if user is not None:
            conditions.append('user_id=?')
            parameters.append(user)
        if group is not None:
            conditions.append('tool_group=?')
            parameters.append(group)
        where = ' AND '.join(conditions)

        with self._tx() as db:
            affected = db.execute(
                'SELECT DISTINCT user_id, tool_group FROM tool_calls WHERE ' + where,
                parameters,
            ).fetchall()
            if not affected:
                return 0
            now = self._now()
            cursor = db.execute(
                "UPDATE tool_calls SET status='orphan', orphaned_at=? WHERE " + where,
                [now, *parameters],
            )
            marked = cursor.rowcount
            for affected_row in affected:
                affected_user = affected_row['user_id']
                affected_group = affected_row['tool_group']
                self._ensure(db, affected_user, affected_group)
                db.execute('''UPDATE tool_health SET state='CHECK_REQUIRED',
                    generation=? WHERE user_id=? AND tool_group=?''',
                    (uuid.uuid4().hex, affected_user, affected_group))
                self._emit(
                    db, affected_user, 'TOOL_CHECK_REQUIRED',
                    affected_group + ' / Previous-session calls remain unsettled; '
                    'manual review required.',
                )
        return marked

    def _expire(self, db, row, now):
        if row['state'] == 'PROBING' and now >= row['probe_until']:
            db.execute('''UPDATE tool_health SET state='CHECK_REQUIRED',
                generation=? WHERE user_id=? AND tool_group=?''',
                (uuid.uuid4().hex, row['user_id'], row['tool_group']))
            self._emit(db, row['user_id'], 'TOOL_CHECK_REQUIRED',
                       'Probe lease expired; manual review required.')
            row = self._load(db, row['user_id'], row['tool_group'])
        return row

    @staticmethod
    def _view(group, row, now, pending=False):
        state = row['state'] if row else 'READY'
        if state == 'PROBING' and now >= row['probe_until']:
            state = 'CHECK_REQUIRED'
        elif state == 'PAUSED' and now >= row['paused_until'] and pending:
            # A cooldown cannot establish that an outstanding call ended.
            # This is a read-only projection, not orphaning or settlement.
            state = 'CHECK_REQUIRED'
        deadline = 0.0
        if state == 'PAUSED':
            deadline = row['paused_until']
        elif state == 'PROBING':
            deadline = row['probe_until']
        return ToolHealth(
            group=group, state=state,
            consecutive_failures=row['fail_count'] if row else 0,
            retry_after_seconds=max(0, math.ceil(deadline - now))
                if state in ('PAUSED', 'PROBING') else 0,
            probe_available=(state == 'PAUSED' and now >= deadline and not pending),
            needs_handoff=state == 'CHECK_REQUIRED',
            version=row['generation'] if row else '0',
        )

    @staticmethod
    def _paused(health):
        return ToolPaused(health['group'], health['retry_after_seconds'],
                          health['needs_handoff'])

    def start(self, user, group) -> Ticket:
        self._key(user, group)
        denial = None
        with self._tx() as db:
            now = self._now()
            row = self._expire(db, self._ensure(db, user, group), now)
            pending = self._pending(db, user, group)
            health = self._view(group, row, now, pending)
            probe = health['probe_available']
            if health['state'] != 'READY' and not probe:
                denial = self._paused(health)
            else:
                token = uuid.uuid4().hex
                generation = uuid.uuid4().hex if probe else row['generation']
                if probe:
                    db.execute('''UPDATE tool_health SET state='PROBING',
                        generation=?, probe_token=?, probe_until=?
                        WHERE user_id=? AND tool_group=?''',
                        (generation, token, now + 35, user, group))
                db.execute('''INSERT INTO tool_calls
                    (id, user_id, tool_group, generation, probe, status,
                     started_at, boot_session)
                    VALUES (?, ?, ?, ?, ?, 'running', ?, ?)''',
                    (token, user, group, generation, int(probe), now,
                     self._boot_session))
                ticket = Ticket(token, user, group, generation, probe)
                self._emit(db, user, 'TOOL_STARTED', 'Tool call registered.')
        if denial is not None:
            raise denial
        return ticket

    def finish(self, ticket: Ticket, outcome):
        if outcome not in ('ok', 'business', 'tech'):
            raise ValueError('unsupported outcome')
        if not isinstance(ticket, Ticket) or type(ticket.probe) is not bool:
            raise ValueError('invalid ticket')
        self._key(ticket.user, ticket.group)
        with self._tx() as db:
            call = db.execute('SELECT * FROM tool_calls WHERE id=?',
                              (ticket.id,)).fetchone()
            identity = (ticket.user, ticket.group, ticket.generation, int(ticket.probe))
            if call is None or tuple(call[k] for k in
                    ('user_id', 'tool_group', 'generation', 'probe')) != identity:
                raise ValueError('ticket does not match registration')
            if call['settled_at'] is not None:
                return
            now = self._now()
            row = self._load(db, ticket.user, ticket.group)
            if row is None:
                raise RuntimeError('missing tool health')
            row = self._expire(db, row, now)
            # Preserve orphaned_at as history even after a real late settlement.
            db.execute('''UPDATE tool_calls SET status=?, settled_at=?
                WHERE id=? AND settled_at IS NULL''', (outcome, now, ticket.id))
            eligible = (
                call['orphaned_at'] is None
                and row['generation'] == ticket.generation
                and (
                    (not ticket.probe and row['state'] == 'READY') or
                    (ticket.probe and row['state'] == 'PROBING'
                     and row['probe_token'] == ticket.id)
                )
            )
            if eligible:
                count = row['fail_count'] + 1 if outcome == 'tech' else 0
                if outcome == 'tech' and (ticket.probe or count >= 3):
                    state, deadline, generation = 'PAUSED', now + 30, uuid.uuid4().hex
                else:
                    state, deadline = 'READY', 0
                    generation = uuid.uuid4().hex if ticket.probe else row['generation']
                db.execute('''UPDATE tool_health SET fail_count=?, state=?,
                    paused_until=?, generation=?, probe_token=NULL, probe_until=0
                    WHERE user_id=? AND tool_group=?''',
                    (count, state, deadline, generation, ticket.user, ticket.group))
                if state == 'PAUSED':
                    self._emit(db, ticket.user, 'TOOL_PAUSED',
                               ticket.group + ' / 连续技术故障，暂停30秒')
                elif ticket.probe:
                    self._emit(db, ticket.user, 'TOOL_READY',
                               ticket.group + ' / 本次恢复检查通过')
            labels = {'ok': '已完成', 'business': '正常业务拒绝', 'tech': '技术异常'}
            self._emit(db, ticket.user, 'TOOL_SETTLED',
                       ticket.group + ' / ' + labels[outcome])

    def run(self, user, group, fn: Callable, business_error_predicate=None):
        ticket = self.start(user, group)
        try:
            result = fn()
        except BaseException as error:
            if not isinstance(error, Exception):
                self.finish(ticket, 'tech')
                raise
            business = False
            if business_error_predicate is not None:
                try:
                    business = bool(business_error_predicate(error))
                except BaseException as predicate_error:
                    if not isinstance(predicate_error, Exception):
                        self.finish(ticket, 'tech')
                        raise
                    business = False
            if business:
                self.finish(ticket, 'business')
                raise
        else:
            self.finish(ticket, 'ok')
            return result
        # Raise outside the handler, so the provider exception is not chained.
        self.finish(ticket, 'tech')
        raise ToolFailure(group) from None

    def snapshot(self, user) -> list[ToolHealth]:
        self._key(user)
        with self._tx(write=False) as db:
            now = self._now()
            rows = {r['tool_group']: r for r in db.execute(
                'SELECT * FROM tool_health WHERE user_id=?', (user,))}
            pending = {r[0] for r in db.execute('''SELECT DISTINCT tool_group
                FROM tool_calls WHERE user_id=? AND settled_at IS NULL''', (user,))}
            return [self._view(g, rows.get(g), now, g in pending) for g in GROUPS]

    def recover(self, user, group) -> ToolHealth:
        self._key(user, group)
        denial = None
        with self._tx() as db:
            now = self._now()
            row = self._expire(db, self._ensure(db, user, group), now)
            pending = self._pending(db, user, group)
            health = self._view(group, row, now, pending)
            if pending or row['state'] == 'PROBING' or (
                    row['state'] == 'PAUSED' and now < row['paused_until']):
                denial = self._paused(health)
            elif row['state'] == 'CHECK_REQUIRED':
                db.execute('''UPDATE tool_health SET state='PAUSED',
                    paused_until=?, probe_token=NULL, probe_until=0, generation=?
                    WHERE user_id=? AND tool_group=?''',
                    (now, uuid.uuid4().hex, user, group))
                self._emit(db, user, 'TOOL_RECOVERED',
                           'Manual review acknowledged; next probe enabled.')
                health = self._view(group, self._load(db, user, group), now)
        # Commit expiry transitions even when the operation is denied.
        if denial is not None:
            raise denial
        return health
