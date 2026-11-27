"""Executors: how whychanged reaches a database.

An executor is any async function `sql -> (columns, rows)`. whychanged only ever
sends it checked, read-only SELECTs, and only aggregates come back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

Executor = Callable[[str], Awaitable[tuple[list[str], list[tuple[Any, ...]]]]]


def duckdb_executor(
    database: str | Path = ":memory:",
    *,
    csv: dict[str, str | Path] | None = None,
    read_only: bool = True,
) -> Executor:
    """A DuckDB file, or an in-memory database over CSV / Parquet files.

    `csv` maps a table name to a file; each becomes a view, so a measure can say
    `from: signups s` for `signups.csv`.
    """
    import duckdb

    in_memory = str(database) == ":memory:"
    conn = duckdb.connect(str(database), read_only=read_only and not in_memory)
    for name, path in (csv or {}).items():
        reader = "read_parquet" if str(path).endswith(".parquet") else "read_csv_auto"
        quoted = str(path).replace("'", "''")
        conn.execute(f"CREATE VIEW \"{name}\" AS SELECT * FROM {reader}('{quoted}')")

    def run(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        # A cursor per call: DuckDB cursors are independent and safe across threads
        cur = conn.cursor()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            return columns, cur.fetchall()
        finally:
            cur.close()

    async def execute(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        return await asyncio.to_thread(run, sql)

    return execute


def dbapi_executor(connection: Any) -> Executor:
    """Any DB-API 2.0 connection (psycopg, ...). Calls are serialised on one connection."""
    lock = asyncio.Lock()

    def run(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        cur = connection.cursor()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            return columns, [tuple(r) for r in cur.fetchall()]
        finally:
            cur.close()

    async def execute(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        async with lock:
            return await asyncio.to_thread(run, sql)

    return execute


def postgres_executor(url: str) -> Executor:
    """A Postgres URL, read-only. Needs the `postgres` extra."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError('Postgres needs psycopg: pip install "whychanged[postgres]"') from exc
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    return dbapi_executor(conn)
