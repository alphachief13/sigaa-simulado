"""Outbox de notificações e fila de trabalhos pendentes (ambos em SQLite)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from sigaa_sim.clock import from_utc_iso, to_utc_iso


class Outbox:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(
        self,
        kind: str,
        dedupe_key: str,
        text: str,
        now_iso: str,
        urgent: bool = False,
        not_before_iso: str | None = None,
        view_json: str | None = None,
    ) -> bool:
        """Insere se a dedupe_key ainda não existir. Retorna True se inseriu."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO outbox(kind, dedupe_key, text, urgent, next_attempt_at,
                                            created_at, view_json) VALUES(?,?,?,?,?,?,?)""",
            (kind, dedupe_key, text, int(urgent), not_before_iso or now_iso, now_iso, view_json),
        )
        return cur.rowcount == 1

    def enqueue_view(
        self,
        kind: str,
        dedupe_key: str,
        view,
        now_iso: str,
        urgent: bool = False,
        not_before_iso: str | None = None,
    ) -> bool:
        import json

        from sigaa_sim.telegram.views import render_plain

        return self.enqueue(
            kind,
            dedupe_key,
            render_plain(view),
            now_iso,
            urgent,
            not_before_iso,
            json.dumps(view.to_dict(), ensure_ascii=False),
        )

    def has_key(self, dedupe_key: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM outbox WHERE dedupe_key=?", (dedupe_key,)).fetchone() is not None
        )

    def due(self, now_iso: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM outbox WHERE status='pending' AND next_attempt_at<=?
               ORDER BY urgent DESC, id LIMIT ?""",
            (now_iso, limit),
        ).fetchall()

    def mark_sent(self, row_id: int, message_id: str | None, now_iso: str) -> None:
        self.conn.execute(
            "UPDATE outbox SET status='sent', message_id=?, sent_at=?, attempts=attempts+1 WHERE id=?",
            (message_id, now_iso, row_id),
        )

    def mark_failed_attempt(self, row_id: int, error: str, now_iso: str, max_attempts: int) -> None:
        row = self.conn.execute("SELECT attempts FROM outbox WHERE id=?", (row_id,)).fetchone()
        attempts = int(row["attempts"]) + 1
        status = "failed" if attempts >= max_attempts else "pending"
        backoff = min(2**attempts, 60) * 60  # minutos -> segundos, cap 1h
        next_at = to_utc_iso(from_utc_iso(now_iso) + timedelta(seconds=backoff))
        self.conn.execute(
            "UPDATE outbox SET attempts=?, last_error=?, status=?, next_attempt_at=? WHERE id=?",
            (attempts, error[:500], status, next_at, row_id),
        )

    def postpone(self, row_id: int, until_iso: str) -> None:
        self.conn.execute("UPDATE outbox SET next_attempt_at=? WHERE id=?", (until_iso, row_id))

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM outbox GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


class Jobs:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(
        self, kind: str, dedupe_key: str, payload: dict[str, Any], now_iso: str, priority: int = 50
    ) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO jobs(kind, dedupe_key, payload_json, priority, next_attempt_at,
                                          created_at) VALUES(?,?,?,?,?,?)""",
            (
                kind,
                dedupe_key,
                json.dumps(payload, ensure_ascii=False, default=str),
                priority,
                now_iso,
                now_iso,
            ),
        )
        if cur.rowcount == 1:
            return True
        # Se já existia mas estava concluído, reabre (ex.: material substituído).
        self.conn.execute(
            """UPDATE jobs SET status='pending', payload_json=?, priority=?, next_attempt_at=?,
                    attempts=0, last_error=NULL, done_at=NULL
               WHERE dedupe_key=? AND status IN ('done','failed')""",
            (json.dumps(payload, ensure_ascii=False, default=str), priority, now_iso, dedupe_key),
        )
        return False

    def due(self, now_iso: str, kinds: list[str] | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE status IN ('pending','deferred') AND next_attempt_at<=?"
        args: list[Any] = [now_iso]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)
        sql += " ORDER BY priority, id LIMIT ?"
        args.append(limit)
        return [
            dict(r) | {"payload": json.loads(r["payload_json"])}
            for r in self.conn.execute(sql, args).fetchall()
        ]

    def mark_done(self, job_id: int, now_iso: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status='done', done_at=?, attempts=attempts+1 WHERE id=?",
            (now_iso, job_id),
        )

    def mark_failed_attempt(
        self,
        job_id: int,
        error: str,
        now_iso: str,
        max_attempts: int = 5,
        defer_seconds: int | None = None,
        terminal: bool = False,
    ) -> None:
        row = self.conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        attempts = int(row["attempts"]) + 1
        if terminal or attempts >= max_attempts:
            status = "failed"
            next_at = now_iso
        else:
            status = "pending"
            secs = defer_seconds if defer_seconds is not None else min(2**attempts, 120) * 60
            next_at = to_utc_iso(from_utc_iso(now_iso) + timedelta(seconds=secs))
        self.conn.execute(
            "UPDATE jobs SET attempts=?, last_error=?, status=?, next_attempt_at=? WHERE id=?",
            (attempts, error[:500], status, next_at, job_id),
        )

    def defer(self, job_id: int, until_iso: str, reason: str) -> None:
        """Adia sem contar tentativa (ex.: orçamento de IA esgotado)."""
        self.conn.execute(
            "UPDATE jobs SET status='deferred', next_attempt_at=?, last_error=? WHERE id=?",
            (until_iso, reason[:500], job_id),
        )

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def pending_by_kind(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) c FROM jobs WHERE status IN ('pending','deferred') GROUP BY kind"
        ).fetchall()
        return {r["kind"]: r["c"] for r in rows}
