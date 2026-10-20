"""Perguntas livres do aluno respondidas com base na knowledge base (busca textual + prazos).

A LLM só interpreta o contexto recuperado; a pergunta é DADO no prompt, não instrução.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.domain import ItemKind
from sigaa_sim.knowledge.store import Knowledge
from sigaa_sim.llm.client import LLMInvalidOutput, LLMUnavailable
from sigaa_sim.llm.prompts import QUESTION
from sigaa_sim.llm.schemas import QuestionAnswer
from sigaa_sim.storage import Repo
from sigaa_sim.telegram.format import fmt_when, upcoming

MAX_CHUNKS = 8
MAX_CHUNK_CHARS = 700


def build_context(conn: sqlite3.Connection, settings: Settings, clock: Clock, question: str) -> dict:
    repo, kb = Repo(conn), Knowledge(conn, settings)
    courses = {c.id: c for c in repo.list_courses()}
    items = repo.list_items()
    today = clock.today()
    deadlines = [
        f"- {fmt_when(it.due_at or it.due_date, settings.tz)} | {courses[it.course_id].name} | {it.title}"
        + (f" | escopo: {it.scope}" if it.scope else "")
        for it, _ in upcoming(items, today, 60)
    ] or ["(nenhum prazo nos próximos 60 dias)"]
    since = to_utc_iso(clock.now() - timedelta(days=21))
    ann = [
        it
        for it in items
        if it.kind == ItemKind.ANNOUNCEMENT
        and (it.published_at or it.published_date)
        and (it.published_at.date() if it.published_at else it.published_date) >= (today - timedelta(days=21))
    ]
    ann.sort(key=lambda i: i.published_at.date() if i.published_at else i.published_date, reverse=True)
    announcements = [
        f"- {fmt_when(i.published_at or i.published_date, settings.tz)} | {courses[i.course_id].name} | "
        f"{i.title}: {(i.body or '')[:240]}"
        for i in ann[:8]
    ] or ["(nenhum aviso recente)"]
    hits = kb.search(question, limit=MAX_CHUNKS)
    chunks = [
        f"[{h['title']} — {h['ref']}] ({courses[h['course_id']].name}) {h['text'][:MAX_CHUNK_CHARS]}"
        for h in hits
    ] or ["(nenhum trecho relevante encontrado nos materiais)"]
    return {
        "today": today.isoformat(),
        "question": question.strip()[:800],
        "since": since,
        "deadlines": "\n".join(deadlines[:15]),
        "announcements": "\n".join(announcements),
        "chunks": "\n\n".join(chunks),
        "hits": hits,
    }


def ask(conn: sqlite3.Connection, settings: Settings, clock: Clock, llm, question: str) -> str:
    """Retorna a mensagem pronta (texto simples). Sem IA, devolve só a busca textual."""
    ctx = build_context(conn, settings, clock, question)
    if llm is None or not getattr(llm, "enabled", False):
        lines = ["🔎 IA desativada; resultados da busca nos materiais:"]
        for h in ctx["hits"][:5]:
            lines.append(f"• {h['title']} — {h['ref']}: {h['text'][:160].replace(chr(10), ' ')}…")
        return "\n".join(lines) if ctx["hits"] else "Nada encontrado nos materiais para essa pergunta."
    prompt = QUESTION.format(
        **{k: ctx[k] for k in ("today", "question", "deadlines", "announcements", "chunks")}
    )
    scope = hashlib.sha256(ctx["question"].lower().encode()).hexdigest()[:16]
    try:
        obj, _ = llm.complete("question_answer", scope, prompt, QuestionAnswer)
    except LLMUnavailable as e:
        return f"IA indisponível agora ({e}). Tente mais tarde ou use /buscar <termo>."
    except LLMInvalidOutput:
        return "A IA não conseguiu formular uma resposta válida. Tente reformular a pergunta."
    lines = ["💬 Resposta (IA, baseada nos seus materiais e prazos)", obj.answer.strip()]
    if obj.sources:
        lines.append("")
        lines.append("Fontes:")
        for src in obj.sources[:6]:
            lines.append(f"• {src.material}" + (f" — {src.ref}" if src.ref else ""))
    if obj.not_found:
        lines.append("")
        lines.append("Sem apoio nos materiais: " + "; ".join(obj.not_found[:3]))
    conf = {"high": "alta", "medium": "média", "low": "baixa"}[obj.confidence]
    lines.append(f"Confiança: {conf}")
    return "\n".join(lines)


def ask_structured(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, llm, question: str
) -> dict | None:
    """Resposta estruturada para o renderer: {answer, sources[str], gaps[str]} ou
    {unavailable: True}; None quando a IA está desativada (chamador usa busca textual).
    Fontes citadas pela IA são validadas contra os trechos/prazos realmente recuperados."""
    ctx = build_context(conn, settings, clock, question)
    if llm is None or not getattr(llm, "enabled", False):
        return None
    prompt = QUESTION.format(
        **{k: ctx[k] for k in ("today", "question", "deadlines", "announcements", "chunks")}
    )
    scope = hashlib.sha256(ctx["question"].lower().encode()).hexdigest()[:16]
    try:
        obj, _ = llm.complete("question_answer", scope, prompt, QuestionAnswer)
    except (LLMUnavailable, LLMInvalidOutput):
        return {"unavailable": True}
    known = {h["title"].lower(): h for h in ctx["hits"]}
    sources: list[str] = []
    for src in obj.sources:
        title = src.material.strip()
        hit = known.get(title.lower()) or next(
            (h for t, h in known.items() if t in title.lower() or title.lower() in t), None
        )
        if hit is None:
            continue  # referência não corresponde a um registro recuperado: descartada
        ref = src.ref.strip()
        label = hit["title"] + (
            f" · {ref}"
            if ref and ref.lower().replace(" ", "") in hit["ref"].lower().replace(" ", "")
            else f" · {hit['ref']}"
        )
        if label not in sources:
            sources.append(label)
    answer = obj.answer.strip()
    if not sources and not ctx["hits"] and obj.not_found:
        answer = ""
    return {"answer": answer, "sources": sources, "gaps": [g.strip() for g in obj.not_found if g.strip()]}
