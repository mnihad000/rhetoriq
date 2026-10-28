"""Small DB-API compatibility layer for the SQLite-to-PostgreSQL transition.

Repositories retain SQLite support for fast isolated tests. Production selects
PostgreSQL by setting DATABASE_URL; this adapter gives the existing repository
queries a consistent mapping-row and qmark-parameter interface.
"""

from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Any


def _active_fence():
    """Return the current job fence without importing the queue eagerly."""
    try:
        from services.job_queue import current_job
    except ImportError:  # pragma: no cover - during very early bootstrap
        return None
    return current_job()


def _validate_fence(connection, target: str) -> None:
    """Validate a bound job lease using the connection's own transaction.

    The lazy import is intentional: ``job_queue`` itself uses this adapter.
    Keeping this check at commit time means every repository using ``connect``
    gets stale-worker protection without having to thread a job token through
    each repository method.
    """
    job = _active_fence()
    if job is None:
        return
    try:
        from services.job_queue import LeaseLostError
    except ImportError:  # pragma: no cover
        return
    if is_postgres_database(target):
        row = connection.execute(
            """
            SELECT status, lease_owner, lease_generation, lease_until
            FROM event_outbox
            WHERE event_id = ?
            FOR UPDATE
            """,
            (job.event_id,),
        ).fetchone()
        valid = bool(row) and row["status"] == "running" and row["lease_owner"] == job.lease_owner
        valid = valid and int(row["lease_generation"]) == int(job.lease_generation)
        valid = valid and row["lease_until"] is not None and row["lease_until"] > _postgres_now(connection)
    else:
        row = connection.execute(
            """
            SELECT 1
            FROM event_outbox
            WHERE event_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_generation = ?
              AND julianday(lease_until) > julianday('now')
            """,
            (job.event_id, job.lease_owner, job.lease_generation),
        ).fetchone()
        valid = row is not None
    if not valid:
        raise LeaseLostError(f"Lease lost for job {job.event_id}")


def _postgres_now(connection):
    row = connection.execute(
        "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') AS now"
    ).fetchone()
    return row["now"] if hasattr(row, "keys") else row[0]


def is_postgres_database(target: str) -> bool:
    if target.startswith(("postgres://", "postgresql://")):
        return True
    if "=" not in target:
        return False
    try:
        from psycopg import ProgrammingError
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return False
    try:
        return bool(conninfo_to_dict(target))
    except (ProgrammingError, ValueError):
        return False


def postgres_query(query: str) -> str:
    """Convert repository qmark SQL to psycopg's parameter format.

    Repositories intentionally share qmark SQL with SQLite. Literal percent
    signs must be escaped before psycopg parses the converted statement.
    """
    return query.replace("%", "%%").replace("?", "%s")


class PostgresConnection:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised in deployment configuration
            raise RuntimeError("PostgreSQL support requires psycopg. Install backend requirements first.") from exc
        self._connection = psycopg.connect(database_url, row_factory=dict_row)

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()

    def commit(self) -> None:
        _validate_fence(self, self._database_url)
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def execute(self, query: str, parameters: tuple | list | None = None):
        # SQLite's BEGIN IMMEDIATE is an implementation detail. Psycopg starts
        # transactions automatically on the first statement.
        if query.strip().upper() == "BEGIN IMMEDIATE":
            return self._connection.execute("SELECT 1")
        return self._connection.execute(postgres_query(query), parameters or ())

    def executemany(self, query: str, parameters):
        return self._connection.executemany(postgres_query(query), parameters)

    def cursor(self):
        return self._connection.cursor()

    def executescript(self, script: str) -> None:
        # Repository schemas contain simple DDL statements; no function bodies
        # or procedural blocks are used, so semicolon splitting is intentional.
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


class _FencedSQLiteConnection:
    """Small proxy that adds commit-time lease validation to sqlite3."""

    def __init__(self, connection: sqlite3.Connection, target: str) -> None:
        self._connection = connection
        self._target = target

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()

    def execute(self, query: str, parameters: tuple | list | None = None):
        return self._connection.execute(query, parameters or ())

    def executemany(self, query: str, parameters):
        return self._connection.executemany(query, parameters)

    def executescript(self, script: str):
        # sqlite3.Connection.executescript() commits implicitly. Execute the
        # simple repository DDL statements individually so a fenced write can
        # still be checked before its transaction commits.
        # Repository SQL uses line comments. Strip those before splitting so
        # punctuation in a comment cannot be mistaken for a statement end.
        script = re.sub(r"(?m)^\s*--.*(?:\r?\n|$)", "", script)
        result = None
        for statement in script.split(";"):
            if statement.strip():
                result = self.execute(statement)
        return result

    def commit(self) -> None:
        _validate_fence(self, self._target)
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def connect(target: str):
    if is_postgres_database(target):
        return PostgresConnection(target)
    connection = sqlite3.connect(target, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return _FencedSQLiteConnection(connection, target)


def ensure_parent_dir(target: str) -> None:
    if target == ":memory:" or is_postgres_database(target):
        return
    Path(target).parent.mkdir(parents=True, exist_ok=True)
