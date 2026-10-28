from .bank import DomainError


def bootstrap_verification(bank):
    with bank.transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS verification_streaks (
                user_id TEXT PRIMARY KEY REFERENCES users(id),
                failures INTEGER NOT NULL CHECK (failures >= 0),
                updated_at REAL NOT NULL
            )
        """)


def _require_transaction(db):
    if not db.in_transaction:
        raise DomainError("INVALID_EXECUTION", "验证状态须在银行事务内更新。", 409)


def check_unlocked(db, user, now):
    _require_transaction(db)
    row = db.execute(
        "SELECT locked_until FROM users WHERE id = ?", (user,)
    ).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "未找到可访问的数据", 404)
    until = row[0]
    if until > now:
        raise DomainError(
            "SENSITIVE_LOCKED",
            "敏感操作已锁定10分钟，请在锁定到期后重试，期间仍可查询账单。",
            423,
        )
    if 0 < until <= now:
        db.execute("UPDATE users SET locked_until = 0 WHERE id = ?", (user,))
        db.execute("DELETE FROM verification_streaks WHERE user_id = ?", (user,))


def record_failure(db, user, now) -> int:
    check_unlocked(db, user, now)
    row = db.execute(
        "SELECT failures FROM verification_streaks WHERE user_id = ?", (user,)
    ).fetchone()
    count = (row[0] if row is not None else 0) + 1
    db.execute("""
        INSERT INTO verification_streaks (user_id, failures, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            failures = excluded.failures,
            updated_at = excluded.updated_at
    """, (user, count, now))
    if count >= 3:
        db.execute(
            "UPDATE users SET locked_until = ? WHERE id = ?",
            (now + 600, user),
        )
    return count


def record_success(db, user):
    _require_transaction(db)
    row = db.execute("SELECT 1 FROM users WHERE id = ?", (user,)).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "未找到可访问的数据", 404)
    db.execute("DELETE FROM verification_streaks WHERE user_id = ?", (user,))
