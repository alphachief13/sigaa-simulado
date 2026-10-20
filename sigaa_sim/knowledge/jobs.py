"""Handlers de jobs da knowledge base (download + extração)."""

from __future__ import annotations

import logging

from sigaa_sim.domain import AuthStatus
from sigaa_sim.knowledge.store import Knowledge
from sigaa_sim.pipeline import Defer, JobContext, Terminal, register
from sigaa_sim.storage import Jobs, Repo
from sigaa_sim.sync import PRIO_MATERIAL

log = logging.getLogger(__name__)


@register("download")
def handle_download(ctx: JobContext, job: dict) -> None:
    repo = Repo(ctx.conn)
    item = repo.get_item(job["payload"]["item_id"])
    if item is None or not item.file_url:
        raise Terminal("item inexistente ou sem URL")
    course = repo.get_course(item.course_id)
    if course is None:
        raise Terminal("disciplina inexistente")
    kb = Knowledge(ctx.conn, ctx.settings)
    now_iso = ctx.clock.now_utc_iso()

    adapter = ctx.extras.get("adapter")
    if adapter is None:
        if ctx.adapter_factory is None:
            raise Defer("sem adapter disponível para download", seconds=3600)
        adapter = ctx.adapter_factory()
        auth = adapter.authenticate()
        if auth.status != AuthStatus.OK:
            raise Defer(f"autenticação indisponível ({auth.status}); download adiado", seconds=6 * 3600)
        ctx.extras["adapter"] = adapter  # reutiliza na mesma execução

    out = kb.fetch_material(adapter, item, course, now_iso)
    log.info("material %s: %s (%s)", item.title, out.status, out.message)
    if out.status in ("blocked", "unsupported"):
        raise Terminal(out.message)
    if out.status in ("invalid", "too_large"):
        raise Terminal(out.message)
    if out.status == "failed":
        raise RuntimeError(out.message)
    if out.file_hash and out.doc_status == "extracted":
        # resumo por documento (cache por hash: mesma versão nunca é resumida duas vezes)
        Jobs(ctx.conn).enqueue(
            "summarize_document",
            f"summarize:{out.file_hash}",
            {"file_hash": out.file_hash, "item_id": item.id, "course_id": course.id},
            now_iso,
            priority=PRIO_MATERIAL,
        )
