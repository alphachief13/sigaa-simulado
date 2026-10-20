"""Conexão SQLite, migrações versionadas e trava de execução."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
OPTIONAL_MIGRATIONS = {"002_fts.sql"}  # falha tolerada (FTS5 ausente)


class LockError(Exception):
    pass


def connect(db_path: Path | str) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Aplica migrações ainda não registradas em ``meta.schema_migrations``."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_migrations'").fetchone()
    applied = set(row["value"].split(",")) if row and row["value"] else set()
    done: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.execute("BEGIN")
            _exec_many(conn, sql)
            applied.add(path.name)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_migrations', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (",".join(sorted(applied)),),
            )
            conn.execute("COMMIT")
            done.append(path.name)
        except sqlite3.OperationalError as e:
            conn.execute("ROLLBACK")
            if path.name in OPTIONAL_MIGRATIONS:
                set_meta(conn, f"migration_skipped:{path.name}", str(e))
                continue
            raise
    return done


def _exec_many(conn: sqlite3.Connection, sql: str) -> None:
    for stmt in _split_statements(sql):
        conn.execute(stmt)


def _split_statements(sql: str) -> list[str]:
    """Divide o SQL em statements respeitando blocos BEGIN...END de triggers."""
    stmts, buf, depth = [], [], 0
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.endswith("BEGIN"):
            depth += 1
        if upper.startswith("END;"):
            depth -= 1
        if stripped.endswith(";") and depth == 0:
            stmts.append("\n".join(buf))
            buf = []
    if buf:
        stmts.append("\n".join(buf))
    return stmts


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'").fetchone()
    return row is not None


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@contextmanager
def run_lock(lock_path: Path, stale_seconds: int = 3 * 3600) -> Iterator[None]:
    """Trava por arquivo com PID. Recupera de travas órfãs (processo morto ou antiga)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip() or 0)
        except ValueError:
            pid = 0
        alive = _pid_alive(pid)
        age = time.time() - lock_path.stat().st_mtime
        if alive and age < stale_seconds:
            raise LockError(f"outra execução em andamento (pid {pid}); trava: {lock_path}")
        lock_path.unlink(missing_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
