"""Small DB-API compatibility layer for the SQLite-to-PostgreSQL transition.

Repositories retain SQLite support for fast isolated tests. Production selects
PostgreSQL by setting DATABASE_URL; this adapter gives the existing repository
queries a consistent mapping-row and qmark-parameter interface.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def is_postgres_database(target: str) -> bool:
    return target.startswith(("postgres://", "postgresql://"))


class PostgresConnection:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised in deployment configuration
            raise RuntimeError("PostgreSQL support requires psycopg. Install backend requirements first.") from exc
        self._connection = psycopg.connect(database_url, row_factory=dict_row)

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def execute(self, query: str, parameters: tuple | list | None = None):
        # SQLite's BEGIN IMMEDIATE is an implementation detail. Psycopg starts
        # transactions automatically on the first statement.
        if query.strip().upper() == "BEGIN IMMEDIATE":
            return self._connection.execute("SELECT 1")
        return self._connection.execute(query.replace("?", "%s"), parameters or ())

    def executescript(self, script: str) -> None:
        # Repository schemas contain simple DDL statements; no function bodies
        # or procedural blocks are used, so semicolon splitting is intentional.
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def connect(target: str):
    if is_postgres_database(target):
        return PostgresConnection(target)
    connection = sqlite3.connect(target, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_parent_dir(target: str) -> None:
    if target == ":memory:" or is_postgres_database(target):
        return
    Path(target).parent.mkdir(parents=True, exist_ok=True)
