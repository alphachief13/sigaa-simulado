"""Processamento de trabalhos pendentes (downloads, extração, interpretação, resumos).

Handlers são registrados por tipo de job. Cada job roda isolado: falha vira tentativa
registrada com próxima execução; ``Defer`` adia sem contar tentativa (orçamento de IA).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.storage import Jobs

log = logging.getLogger(__name__)


class Defer(Exception):
    """Adiar o job (sem contar como falha). ``seconds`` até a próxima tentativa."""

    def __init__(self, reason: str, seconds: int = 3600):
        super().__init__(reason)
        self.seconds = seconds


class Terminal(Exception):
    """Falha definitiva: o job é marcado como failed sem novas tentativas."""


@dataclass
class JobContext:
    conn: sqlite3.Connection
    settings: Settings
    clock: Clock
    adapter_factory: Callable[[], object] | None = None
    llm: object | None = None
    extras: dict = field(default_factory=dict)


Handler = Callable[[JobContext, dict], None]
_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def handlers() -> dict[str, Handler]:
    return dict(_HANDLERS)


def process_jobs(ctx: JobContext, limit: int = 20, kinds: list[str] | None = None) -> dict[str, int]:
    jobs = Jobs(ctx.conn)
    stats = {"done": 0, "failed": 0, "deferred": 0, "skipped": 0}
    now_iso = ctx.clock.now_utc_iso()
    for job in jobs.due(now_iso, kinds=kinds, limit=limit):
        handler = _HANDLERS.get(job["kind"])
        if handler is None:
            stats["skipped"] += 1
            continue
        now_iso = ctx.clock.now_utc_iso()
        try:
            handler(ctx, job)
            ctx.conn.execute("BEGIN IMMEDIATE")
            jobs.mark_done(job["id"], now_iso)
            ctx.conn.execute("COMMIT")
            stats["done"] += 1
        except Defer as d:
            until = to_utc_iso(ctx.clock.now() + timedelta(seconds=d.seconds))
            ctx.conn.execute("BEGIN IMMEDIATE")
            jobs.defer(job["id"], until, str(d))
            ctx.conn.execute("COMMIT")
            stats["deferred"] += 1
            log.info("job %s#%s adiado: %s", job["kind"], job["id"], d)
        except Terminal as t:
            ctx.conn.execute("BEGIN IMMEDIATE")
            jobs.mark_failed_attempt(job["id"], str(t), now_iso, terminal=True)
            ctx.conn.execute("COMMIT")
            stats["failed"] += 1
            log.warning("job %s#%s falhou definitivamente: %s", job["kind"], job["id"], t)
        except Exception as e:
            if ctx.conn.in_transaction:
                ctx.conn.execute("ROLLBACK")
            ctx.conn.execute("BEGIN IMMEDIATE")
            jobs.mark_failed_attempt(job["id"], f"{type(e).__name__}: {e}", now_iso)
            ctx.conn.execute("COMMIT")
            stats["failed"] += 1
            log.exception("job %s#%s falhou", job["kind"], job["id"])
    return stats
