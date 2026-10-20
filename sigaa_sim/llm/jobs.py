"""Handlers de jobs de IA. Indisponibilidade adia (Defer); saída inválida falha o job
sem afetar alertas factuais."""

from __future__ import annotations

import logging
from datetime import date

from sigaa_sim.domain import sha256_text
from sigaa_sim.knowledge.store import Knowledge
from sigaa_sim.llm.client import LLMInvalidOutput, LLMUnavailable
from sigaa_sim.llm.prompts import ANNOUNCEMENT, DOCUMENT_SUMMARY
from sigaa_sim.llm.schemas import AnnouncementInterpretation, DocumentSummary
from sigaa_sim.pipeline import Defer, JobContext, Terminal, register
from sigaa_sim.storage import Outbox, Repo
from sigaa_sim.telegram.format import fmt_when

log = logging.getLogger(__name__)

MAX_CHUNKS_PER_SUMMARY = 6
RELATIVE_WORDS = (
    "hoje",
    "amanhã",
    "amanha",
    "próxima aula",
    "proxima aula",
    "próxima semana",
    "proxima semana",
    "semana que vem",
)


def alert_decision(obj: AnnouncementInterpretation, item, today: date) -> str | None:
    """Decide se a interpretação vira alerta imediato e de que tipo.

    'confirmed' — data explícita futura sobre prova/entrega/mudança de horário.
    'confirm'   — aviso recente (≤ 3 dias) sobre prova/entrega com data indefinida ("próxima semana").
    None        — histórico/resumo: eventos passados, avisos de material, informes gerais.
    Expressões relativas nunca são convertidas em uma data exata.
    """
    if obj.kind not in ("deadline", "exam", "schedule_change"):
        return None
    published = item.published_at.date() if item.published_at else item.published_date
    if obj.structured_date:
        try:
            return "confirmed" if date.fromisoformat(obj.structured_date) >= today else None
        except ValueError:
            pass
    mentions = " ".join(obj.date_mentions).lower()
    if published is None:
        return None
    if any(w in mentions for w in RELATIVE_WORDS) or obj.needs_confirmation:
        return "confirm" if (today - published).days <= 3 else None
    return None


def _quote(obj: AnnouncementInterpretation) -> str:
    for m in obj.date_mentions:
        if any(w in m.lower() for w in RELATIVE_WORDS):
            return m.strip(" .")
    return obj.date_mentions[0].strip(" .") if obj.date_mentions else "uma data não especificada"


def _llm(ctx: JobContext):
    llm = ctx.llm
    if llm is None or not getattr(llm, "enabled", False):
        raise Defer("IA desativada; resumo adiado", seconds=24 * 3600)
    return llm


@register("summarize_document")
def handle_summarize(ctx: JobContext, job: dict) -> None:
    llm = _llm(ctx)
    repo = Repo(ctx.conn)
    p = job["payload"]
    kb = Knowledge(ctx.conn, ctx.settings)
    chunks = kb.chunks_for(p["file_hash"])
    if not chunks:
        raise Terminal("documento sem chunks")
    item = repo.get_item(p["item_id"])
    course = repo.get_course(p["course_id"])
    if item is None or course is None:
        raise Terminal("item/disciplina inexistente")
    budget_chars = ctx.settings.groq.max_input_chars - 600
    parts, used = [], 0
    for c in chunks[:MAX_CHUNKS_PER_SUMMARY]:
        piece = f"[{c['ref']}] {c['text']}"
        if used + len(piece) > budget_chars:
            piece = piece[: max(0, budget_chars - used)]
        if not piece:
            break
        parts.append(piece)
        used += len(piece)
    prompt = DOCUMENT_SUMMARY.format(course=course.name, title=item.title, text="\n\n".join(parts))
    try:
        obj, cached = llm.complete("document_summary", p["file_hash"], prompt, DocumentSummary)
    except LLMUnavailable as e:
        raise Defer(str(e), seconds=e.retry_seconds) from e
    except LLMInvalidOutput as e:
        raise Terminal(str(e)) from e
    log.info(
        "resumo %s (%s): %d tópicos%s",
        item.title,
        p["file_hash"][:8],
        len(obj.topics),
        " [cache]" if cached else "",
    )


@register("interpret_announcement")
def handle_interpret(ctx: JobContext, job: dict) -> None:
    llm = _llm(ctx)
    repo = Repo(ctx.conn)
    item = repo.get_item(job["payload"]["item_id"])
    if item is None:
        raise Terminal("aviso inexistente")
    course = repo.get_course(item.course_id)
    published = fmt_when(item.published_at or item.published_date, ctx.settings.tz)
    prompt = ANNOUNCEMENT.format(
        course=course.name if course else "?",
        published=published,
        title=item.title,
        body=item.body or "(sem corpo)",
    )
    try:
        obj, cached = llm.complete(
            "announcement_interpretation",
            item.id,
            prompt,
            AnnouncementInterpretation,
            cache_extra=item.content_hash()[:12],
        )
    except LLMUnavailable as e:
        raise Defer(str(e), seconds=e.retry_seconds) from e
    except LLMInvalidOutput as e:
        raise Terminal(str(e)) from e
    if cached or not job["payload"].get("notify", True) or course is None:
        return
    today = ctx.clock.today()
    decision = alert_decision(obj, item, today)
    if decision is None:
        log.info("aviso %s fica para o resumo (sem evento futuro comprovado)", item.title[:40])
        return
    from sigaa_sim.telegram import templates as T

    what = {"exam": "prova", "deadline": "entrega", "schedule_change": "aula"}[obj.kind]
    if obj.kind == "schedule_change":  # mudança de horário pode ser da prova, não da aula
        low = (item.body or item.title).lower()
        what = "prova" if ("prova" in low or "avalia" in low) else "aula"
    tz = ctx.settings.tz
    if decision == "confirmed":
        when = T.fmt_day(date.fromisoformat(obj.structured_date), tz, today, weekday=True)
        title = {
            "exam": "Data da prova",
            "deadline": "Prazo de entrega",
            "schedule_change": "Mudança de aula",
        }[obj.kind]
        view = T.announcement_alert(
            item, course, obj.summary, f"Data: {when}", tz, today, ctx.settings.display_names, title=title
        )
    else:
        view = T.date_to_confirm(
            item,
            course,
            _quote(obj),
            item.published_at or item.published_date,
            tz,
            today,
            what=what,
            names=ctx.settings.display_names,
        )
    # dedupe por (turma, tipo, conteúdo): o mesmo aviso publicado duas vezes gera um alerta
    body_key = sha256_text((item.body or item.title).strip().lower())[:12]
    ctx.conn.execute("BEGIN IMMEDIATE")
    Outbox(ctx.conn).enqueue_view(
        "interpretation",
        f"interp:{item.course_id}:{obj.kind}:{body_key}",
        view,
        ctx.clock.now_utc_iso(),
        urgent=True,
    )
    ctx.conn.execute("COMMIT")
