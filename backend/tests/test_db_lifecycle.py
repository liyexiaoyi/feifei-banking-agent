"""Initialization must release its SQLite handle even before garbage collection."""
from datetime import datetime, timezone
import sqlite3

import pytest

from ff_agent import db


@pytest.mark.parametrize('seeded', [False, True])
def test_bootstrap_releases_connection_on_seed_and_existing_database(tmp_path, monkeypatch, seeded):
    path = tmp_path/'resource.sqlite3'
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    if seeded:
        db.bootstrap(path, now)
    connection = db.connect(path)
    monkeypatch.setattr(db, 'connect', lambda _: connection)
    db.bootstrap(path, now)
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


def test_bootstrap_releases_connection_after_schema_error(tmp_path, monkeypatch):
    path = tmp_path/'failed.sqlite3'
    connection = db.connect(path)
    monkeypatch.setattr(db, 'connect', lambda _: connection)
    monkeypatch.setattr(db, 'SCHEMA', 'INVALID SQL STATEMENT;')
    with pytest.raises(sqlite3.OperationalError):
        db.bootstrap(path, datetime(2026, 10, 26, tzinfo=timezone.utc))
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
