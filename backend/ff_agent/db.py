"""SQLite persistence and deterministic synthetic bank fixtures."""
import calendar
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, locked_until REAL DEFAULT 0, subscription_consent REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, masked_number TEXT NOT NULL, balance_cents INTEGER NOT NULL CHECK(balance_cents>=0));
CREATE TABLE IF NOT EXISTS payees(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL, remark TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, account_id TEXT NOT NULL, kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, category TEXT NOT NULL, merchant TEXT NOT NULL, occurred_at TEXT NOT NULL, note TEXT DEFAULT '', operation_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, merchant TEXT NOT NULL, amount_cents INTEGER NOT NULL, next_charge TEXT NOT NULL, status TEXT NOT NULL, direct_cancel INTEGER NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, executed_day TEXT, result TEXT, attempts INTEGER DEFAULT 0, confirmed_at REAL, UNIQUE(user_id,idempotency_key));
CREATE TABLE IF NOT EXISTS operation_plan_links(operation_id TEXT PRIMARY KEY REFERENCES operations(id),user_id TEXT NOT NULL,plan_id TEXT NOT NULL,node_idx INTEGER NOT NULL,origin TEXT NOT NULL,source_id TEXT);
CREATE INDEX IF NOT EXISTS operation_plan_owner ON operation_plan_links(user_id,plan_id,node_idx);
CREATE TABLE IF NOT EXISTS plan_link_issues(plan_id TEXT NOT NULL,user_id TEXT NOT NULL,code TEXT NOT NULL,PRIMARY KEY(plan_id,user_id,code));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, operation_id TEXT, event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, request_key TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_requests(user_id TEXT NOT NULL, request_key TEXT NOT NULL, input TEXT NOT NULL, response TEXT, status TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(user_id,request_key));
CREATE TABLE IF NOT EXISTS contexts(user_id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS safety_holds(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_key TEXT NOT NULL, codes TEXT NOT NULL, reasons TEXT NOT NULL, advice TEXT NOT NULL, created_at REAL NOT NULL, ended_at REAL, UNIQUE(user_id,request_key));
CREATE UNIQUE INDEX IF NOT EXISTS safety_one_active ON safety_holds(user_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS chat_request_epochs(user_id TEXT NOT NULL, request_key TEXT NOT NULL, epoch TEXT NOT NULL, PRIMARY KEY(user_id,request_key));
CREATE TABLE IF NOT EXISTS memory_notes(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL, tags TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS memory_active_content ON memory_notes(user_id,content_hash) WHERE removed=0;
CREATE TABLE IF NOT EXISTS memory_links(user_id TEXT NOT NULL, left_id TEXT NOT NULL, right_id TEXT NOT NULL, PRIMARY KEY(user_id,left_id,right_id));
CREATE TABLE IF NOT EXISTS memory_requests(user_id TEXT NOT NULL, request_key TEXT NOT NULL, content_hash TEXT NOT NULL, note_id TEXT NOT NULL, PRIMARY KEY(user_id,request_key));
CREATE INDEX IF NOT EXISTS transaction_user_date ON transactions(user_id,occurred_at);
CREATE INDEX IF NOT EXISTS operation_user ON operations(user_id,created_at);
"""


def connect(path):
    connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=10000')
    return connection


def bootstrap(path: Path, now: datetime):
    path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.Connection.__exit__ commits/rolls back but does not close the
    # handle. Keep that transaction behavior and close on every exit path.
    with closing(connect(path)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            db.commit()
            return
        db.executemany('INSERT INTO users(id,name) VALUES(?,?)', [('demo', '小一'), ('guest', '体验用户')])
        db.executemany('INSERT INTO accounts VALUES(?,?,?,?,?)', [
            ('a_daily', 'demo', '日常账户', '6222 ···· 0826', 2000000),
            ('a_saving', 'demo', '储蓄账户', '6222 ···· 1018', 800000),
            ('b_daily', 'guest', '日常账户', '6222 ···· 0356', 500000),
            ('b_saving', 'guest', '储蓄账户', '6222 ···· 0831', 200000),
        ])
        db.executemany('INSERT INTO payees VALUES(?,?,?,?,?)', [
            ('p_mom', 'demo', '妈妈', '13800000001', '家人'),
            ('p_dad', 'demo', '爸爸', '13800000002', '家人'),
            ('p_lin_1', 'demo', '林晓', '13900000011', '同学'),
            ('p_lin_2', 'demo', '林晓', '13900000022', '同事'),
            ('p_guest', 'guest', '朋友', '13700000033', '好友'),
        ])
        subs = [('sub_video', '流光视频', 2500, 1), ('sub_music', '云声音乐', 1500, 1),
                ('sub_cloud', '云盘空间', 6800, 1), ('sub_tool', '效率工具', 1200, 1),
                ('sub_gym', '城市健身', 9800, 0)]
        for index, (sid, name, amount, direct) in enumerate(subs):
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?)',
                       (sid, 'demo', name, amount, (now + timedelta(days=index+2)).date().isoformat(), 'ACTIVE', direct,
                        '月度自动续费；取消后本期权益保留至到期' if direct else '需到商户应用或门店取消，银行端无法直接解除'))
        for offset in (0, 1):
            month_start = now.replace(day=1)
            if offset:
                month_start = (month_start - timedelta(days=1)).replace(day=1)
            prefix = month_start.strftime('%Y-%m')
            for day in range(1, 21):
                for kind, amount, category, merchant in [('coffee', 1800, '餐饮', '街角咖啡'),
                         ('lunch', 3500 + offset*500, '餐饮', '好食餐厅'), ('metro', 600, '交通', '城市地铁')]:
                    db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                               (f'{prefix}-{day}-{kind}', 'demo', 'a_daily', 'EXPENSE', amount, category, merchant,
                                f'{prefix}-{day:02d}T12:00:00+08:00', '',))
            rows = [(f'{prefix}-rent','EXPENSE',240000,'住房','月度房租'),
                    (f'{prefix}-refund','REFUND',3500,'退款','好食餐厅'),
                    (f'{prefix}-salary','INCOME',1200000,'收入','工资入账'),
                    (f'{prefix}-internal','INTERNAL',50000,'本人互转','储蓄账户')]
            rows.extend((f'{prefix}-{sid}','EXPENSE',amount,'订阅',name) for sid,name,amount,_ in subs)
            for tid, kind, amount, category, merchant in rows:
                db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                           (tid, 'demo', 'a_daily', kind, amount, category, merchant, f'{prefix}-05T09:00:00+08:00', '合成演示流水'))
        db.commit()
