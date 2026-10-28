"""Additive goal-control schema. Runs inside the caller's Bank BEGIN IMMEDIATE transaction.

Never begins, commits or uses executescript; any failure rolls back with the caller.
The v2 partial index covers UNKNOWN or unpaused ACTIVE rows, so those two cannot coexist
for one user/session; paused ACTIVE rows are saved goals and may be many.
"""
from .bank import DomainError

OPEN_SQL = "status='UNKNOWN' OR (status='ACTIVE' AND paused=0)"

TABLES = (
    """CREATE TABLE IF NOT EXISTS conversation_goal_focus(user_id TEXT NOT NULL,
    session_hash TEXT NOT NULL, goal_id TEXT REFERENCES conversation_goals(id),
    updated_at TEXT NOT NULL, PRIMARY KEY(user_id,session_hash))""",
    """CREATE TABLE IF NOT EXISTS conversation_goal_controls(user_id TEXT NOT NULL,
    session_hash TEXT NOT NULL, request_key TEXT NOT NULL, goal_id TEXT NOT NULL,
    action TEXT NOT NULL, args_hash TEXT NOT NULL, result TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(user_id,session_hash,request_key))""",
)

COLUMNS = (
    ('paused', 'ALTER TABLE conversation_goals ADD COLUMN paused INTEGER NOT NULL DEFAULT 0 '
               'CHECK(paused IN (0,1))'),
    ('pause_gen', 'ALTER TABLE conversation_goals ADD COLUMN pause_gen INTEGER NOT NULL DEFAULT 0 '
                  'CHECK(pause_gen>=0)'),
)


def bootstrap_controls(bank, db):
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversation_goals)')}
    for name, ddl in COLUMNS:
        if name not in cols:
            db.execute(ddl)
    dup = db.execute('SELECT 1 FROM conversation_goals WHERE ' + OPEN_SQL +
                     ' GROUP BY user_id,session_hash HAVING COUNT(*)>1 LIMIT 1').fetchone()
    if dup is not None:
        raise DomainError('MIGRATION_CONFLICT', '目标数据存在重复的进行中记录，无法迁移。', 500)
    db.execute('DROP INDEX IF EXISTS conversation_goal_one_open')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS conversation_goal_one_open_v2 '
               'ON conversation_goals(user_id,session_hash) WHERE ' + OPEN_SQL)
    for stmt in TABLES:
        db.execute(stmt)
    # Seed focus only where none exists; an explicit NULL focus is never overwritten.
    db.execute('INSERT OR IGNORE INTO conversation_goal_focus(user_id,session_hash,goal_id,updated_at) '
               'SELECT user_id,session_hash,id,? FROM conversation_goals WHERE ' + OPEN_SQL,
               (bank.now().isoformat(),))
