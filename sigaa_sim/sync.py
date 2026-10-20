"""Orquestração da coleta: coletar → validar → persistir mudanças → pendências → notificar.

Cada seção é persistida em sua própria transação (item + revisão + evento + outbox + jobs),
então uma falha em uma disciplina não afeta as demais nem deixa estado pela metade.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from sigaa_sim import notify
from sigaa_sim.clock import Clock
from sigaa_sim.config import Settings
from sigaa_sim.domain import (
    AuthStatus,
    ChangeEvent,
    CollectionStatus,
    Course,
    EventType,
    Item,
    ItemKind,
    SectionResult,
)
from sigaa_sim.parsers import parse_records
from sigaa_sim.sigaa.base import SECTIONS, SigaaAdapter
from sigaa_sim.storage import Jobs, Repo, set_meta, transaction
from sigaa_sim.storage.changes import (
    SECTION_KIND,
    apply_removals,
    detect_section,
    track_missing,
)

log = logging.getLogger(__name__)

# Prioridades de jobs (menor = mais urgente)
PRIO_INTERPRET_URGENT = 10
PRIO_MATERIAL_NEAR_DEADLINE = 20
PRIO_MATERIAL = 40
PRIO_INTERPRET = 30


@dataclass
class SyncStats:
    courses: int = 0
    sections_ok: int = 0
    sections_partial: int = 0
    sections_failed: int = 0
    sections_empty: int = 0
    items_seen: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    notifications: int = 0
    jobs: int = 0
    parse_errors: int = 0
    duration_s: float = 0.0
    baseline: bool = False
    status: str = "running"
    auth: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def run_sync(conn: sqlite3.Connection, adapter: SigaaAdapter, settings: Settings, clock: Clock) -> SyncStats:
    repo, jobs = Repo(conn), Jobs(conn)
    t0 = time.monotonic()
    stats = SyncStats()
    baseline = not repo.has_successful_run()
    stats.baseline = baseline
    now_iso = clock.now_utc_iso()

    with transaction(conn):
        run_id = repo.start_run(adapter.name, now_iso, baseline)

    auth = adapter.authenticate()
    stats.auth = str(auth.status)
    if auth.status != AuthStatus.OK:
        msg = auth.message or str(auth.status)
        stats.status = "failed"
        stats.errors.append(f"autenticação: {msg}")
        with transaction(conn):
            notify.sync_failure_alert(
                conn,
                settings,
                clock,
                key=f"auth:{auth.status}:{clock.today().isoformat()}",
                detail=msg if auth.status != AuthStatus.NEEDS_INTERACTION else None,
                session=auth.status == AuthStatus.NEEDS_INTERACTION,
            )
            stats.duration_s = round(time.monotonic() - t0, 2)
            repo.finish_run(run_id, "failed", clock.now_utc_iso(), stats.as_dict(), error=f"auth: {msg}")
        adapter.close()
        return stats

    try:
        courses = adapter.list_courses()
    except Exception as e:  # falha de navegação/parser: não toca no estado
        log.exception("falha ao listar disciplinas")
        stats.status = "failed"
        stats.errors.append(f"listar disciplinas: {e}")
        with transaction(conn):
            notify.sync_failure_alert(
                conn,
                settings,
                clock,
                key=f"list_courses:{clock.today().isoformat()}",
                detail="Não consegui listar as disciplinas.",
            )
            stats.duration_s = round(time.monotonic() - t0, 2)
            repo.finish_run(run_id, "failed", clock.now_utc_iso(), stats.as_dict(), error=str(e)[:500])
        adapter.close()
        return stats

    stats.courses = len(courses)
    all_new_events: list[tuple[ChangeEvent, Item, Course]] = []
    tz = settings.tz

    for course in courses:
        with transaction(conn):
            repo.upsert_course(course, now_iso)
        for section in SECTIONS:
            t_sec = time.monotonic()
            if section not in adapter.supported_sections:
                res = SectionResult(course_id=course.id, section=section, status=CollectionStatus.UNSUPPORTED)
            else:
                res = _collect_section(adapter, course, section, tz, stats)
            kind = SECTION_KIND[section]
            log.info(
                "coleta %s/%s: %s (%d registros) em %.1fs",
                course.code,
                section,
                res.status,
                len(res.items),
                time.monotonic() - t_sec,
            )
            _tally(stats, res)
            stats.items_seen += len(res.items)
            try:
                with transaction(conn):
                    repo.record_section(run_id, res, now_iso)
                    det = detect_section(repo, res, now_iso, run_id, baseline)
                    track_missing(repo, res, kind)
                    stats.created += len(det.created)
                    stats.updated += len(det.updated)
                    for ev in det.events:
                        item = repo.get_item(ev.item_id)
                        assert item is not None
                        if not baseline and notify.notify_event(conn, settings, clock, ev, item, course):
                            stats.notifications += 1
                        stats.jobs += _enqueue_jobs(jobs, ev, item, now_iso, clock)
                        all_new_events.append((ev, item, course))
            except Exception as e:
                log.exception("falha ao persistir %s/%s", course.code, section)
                stats.errors.append(f"{course.code}/{section}: {e}")
                stats.sections_failed += 1

    with transaction(conn):
        removed = apply_removals(repo, settings.collection.removal_after_missing_collections, now_iso, run_id)
        stats.removed = len(removed)
        if baseline:
            if notify.first_connection(conn, settings, clock, partial=stats.sections_failed > 0):
                stats.notifications += 1
            set_meta(conn, "digest_since", now_iso)
        else:
            stats.notifications += notify.schedule_reminders(conn, settings, clock)

        # coleta parcial: um aviso por dia identificando a parte afetada
        if stats.sections_failed and not baseline and not (stats.sections_ok or stats.sections_partial):
            notify.sync_failure_alert(
                conn,
                settings,
                clock,
                key=f"sections_failed:{clock.today().isoformat()}",
                detail="Nenhuma seção pôde ser lida.",
            )
        stats.duration_s = round(time.monotonic() - t0, 2)
        if stats.sections_failed and (stats.sections_ok or stats.sections_partial or stats.sections_empty):
            stats.status = "partial"
        elif stats.sections_failed:
            stats.status = "failed"
        else:
            stats.status = "success"
        repo.finish_run(run_id, stats.status, clock.now_utc_iso(), stats.as_dict())
        if stats.status in ("success", "partial"):
            set_meta(conn, "last_success_at", clock.now_utc_iso())
    adapter.close()
    return stats


def _collect_section(
    adapter: SigaaAdapter, course: Course, section: str, tz, stats: SyncStats
) -> SectionResult:
    kind = SECTION_KIND[section]
    try:
        raw = adapter.collect(course, section)
    except Exception as e:
        log.warning("coleta %s/%s falhou: %s", course.code, section, e)
        return SectionResult(
            course_id=course.id, section=section, status=CollectionStatus.FAILED, error=str(e)[:300]
        )
    if raw.status in (CollectionStatus.FAILED, CollectionStatus.UNSUPPORTED):
        return SectionResult(
            course_id=course.id, section=section, status=raw.status, error=raw.error, evidence=raw.evidence
        )
    items, errors = parse_records(raw.records, course.id, kind, tz)
    stats.parse_errors += len(errors)
    status = raw.status
    if errors:
        # registros ilegíveis: coleta vira parcial; ausência não conta como remoção
        status = CollectionStatus.PARTIAL
    if status == CollectionStatus.COMPLETE and not items and not raw.evidence:
        # lista vazia sem evidência de "nenhum registro": não confiamos (parser quebrado?)
        return SectionResult(
            course_id=course.id,
            section=section,
            status=CollectionStatus.FAILED,
            error="seção vazia sem evidência explícita de ausência de registros",
        )
    return SectionResult(
        course_id=course.id,
        section=section,
        status=status,
        items=items,
        error="; ".join(errors)[:300] or raw.error,
        evidence=raw.evidence,
    )


def _tally(stats: SyncStats, res: SectionResult) -> None:
    if res.status == CollectionStatus.COMPLETE:
        stats.sections_ok += 1
    elif res.status == CollectionStatus.PARTIAL:
        stats.sections_partial += 1
    elif res.status == CollectionStatus.EMPTY:
        stats.sections_empty += 1
    elif res.status == CollectionStatus.FAILED:
        stats.sections_failed += 1


def _enqueue_jobs(jobs: Jobs, ev: ChangeEvent, item: Item, now_iso: str, clock: Clock) -> int:
    n = 0
    if item.kind == ItemKind.MATERIAL and item.file_url:
        prio = PRIO_MATERIAL
        payload = {"item_id": item.id, "course_id": item.course_id, "url": item.file_url}
        # dedupe por item+url: URL nova => job novo; mesma URL substituída é tratada pela revalidação
        if jobs.enqueue("download", f"download:{item.id}:{item.file_url}", payload, now_iso, prio):
            n += 1
    if item.kind == ItemKind.ANNOUNCEMENT and ev.event_type in (
        EventType.CREATED,
        EventType.UPDATED,
        EventType.BASELINE,
    ):
        baseline = ev.event_type == EventType.BASELINE
        # Na baseline interpretamos só avisos recentes (contexto para o planner) e SEM notificar:
        # o aluno já viu esses avisos; notificação só para avisos novos a partir de agora.
        if not baseline or _published_recent(item, clock, days=7):
            prio = PRIO_INTERPRET if baseline else PRIO_INTERPRET_URGENT
            key = f"interpret:{item.id}:{item.content_hash()[:12]}"
            payload = {"item_id": item.id, "notify": not baseline}
            if jobs.enqueue("interpret_announcement", key, payload, now_iso, prio):
                n += 1
    return n


def _published_recent(item: Item, clock: Clock, days: int) -> bool:
    d = item.published_at.date() if item.published_at else item.published_date
    return bool(d and (clock.today() - d).days <= days)
