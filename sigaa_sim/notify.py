"""Política de notificações: o que interrompe o aluno agora e o que vai para o resumo.

- Primeira coleta: uma confirmação com o próximo prazo; histórico não vira novidade.
- Imediato: mudança comprovada de prazo/prova; nova avaliação/tarefa com prazo em ≤ 7 dias.
- Resumo (08h e 18h, só com novidades): notas, materiais, avisos comuns, conteúdo, demais mudanças.
- Lembretes: 7 dias, 1 dia e no dia; um por (item, prazo, faixa).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.domain import ChangeEvent, Course, EventType, Item, ItemKind
from sigaa_sim.storage import Outbox, Repo, get_meta, set_meta
from sigaa_sim.telegram import templates as T
from sigaa_sim.telegram.templates import Agenda
from sigaa_sim.telegram.views import display_name

DIGEST_TIMES = (time(8, 0), time(18, 0))
URGENT_DAYS = 7


def agenda_items(repo: Repo, settings: Settings, today: date, horizon: int) -> list[Agenda]:
    courses = {c.id: c for c in repo.list_courses()}
    out = []
    for it in repo.list_items():
        if it.kind not in (ItemKind.ACTIVITY, ItemKind.ASSESSMENT) or it.course_id not in courses:
            continue
        a = Agenda.from_item(it, courses[it.course_id], settings.tz)
        if a and today <= a.due <= today + timedelta(days=horizon):
            out.append(a)
    # data real; hora quando conhecida; desempate estável por título
    out.sort(
        key=lambda a: (
            a.due,
            a.item.due_at.astimezone(settings.tz).time() if a.item.due_at else time.max,
            a.item.title,
        )
    )
    return out


def names(settings: Settings) -> dict[str, str]:
    return settings.display_names


def first_connection(conn: sqlite3.Connection, settings: Settings, clock: Clock, partial: bool) -> bool:
    repo, outbox = Repo(conn), Outbox(conn)
    today = clock.today()
    agenda = agenda_items(repo, settings, today, 60)
    view = T.first_connection(
        len(repo.list_courses()),
        agenda[0] if agenda else None,
        settings.tz,
        today,
        names(settings),
        partial=partial,
    )
    return outbox.enqueue_view("baseline", "baseline", view, clock.now_utc_iso())


def is_immediate(ev: ChangeEvent, item: Item, today: date, tz) -> bool:
    """Interrompe agora só com mudança comprovada de prazo ou compromisso novo próximo."""
    if ev.event_type == EventType.DEADLINE_CHANGED:
        return True
    if ev.event_type == EventType.CREATED and item.kind in (ItemKind.ASSESSMENT, ItemKind.ACTIVITY):
        due = item.due_at.astimezone(tz).date() if item.due_at else item.due_date
        return due is not None and 0 <= (due - today).days <= URGENT_DAYS
    return False


def notify_event(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, ev: ChangeEvent, item: Item, course: Course
) -> bool:
    """Enfileira alerta imediato quando cabe; caso contrário só registra (o resumo lê os eventos)."""
    today = clock.today()
    tz = settings.tz
    if not is_immediate(ev, item, today, tz):
        return False
    outbox = Outbox(conn)
    if ev.event_type == EventType.DEADLINE_CHANGED:
        old = (ev.old or {}).get("due_at") or (ev.old or {}).get("due_date")
        new = (ev.new or {}).get("due_at") or (ev.new or {}).get("due_date")
        view = T.deadline_changed(item, course, old, new, tz, today, names(settings))
    else:
        a = Agenda.from_item(item, course, tz)
        if a is None:
            return False
        view = T.reminder(a, tz, today, names(settings))
        view.title = (
            f"Nova {'prova' if a.kind_label == 'prova' else 'entrega'} {T.relative_days(a.due, today)}"
        )
    return outbox.enqueue_view("event", f"event:{ev.id}", view, clock.now_utc_iso(), urgent=True)


def schedule_reminders(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> int:
    """Um lembrete por (item, prazo, faixa). Faixa vencida sem envio não é reposta."""
    repo, outbox = Repo(conn), Outbox(conn)
    today = clock.today()
    n = 0
    for a in agenda_items(repo, settings, today, max(settings.telegram.reminder_days, default=0)):
        days_left = (a.due - today).days
        if days_left not in settings.telegram.reminder_days:
            continue
        key = f"reminder:{a.item.id}:{a.item.deadline_key()}:{days_left}"
        view = T.reminder(a, settings.tz, today, names(settings))
        if outbox.enqueue_view("reminder", key, view, clock.now_utc_iso(), urgent=(days_left == 0)):
            n += 1
    return n


def digest_due(conn: sqlite3.Connection, clock: Clock) -> str | None:
    """Retorna o rótulo do slot (AAAA-MM-DD/08 ou /18) se um resumo está devido e ainda não foi feito."""
    now = clock.now()
    slot = None
    for t in DIGEST_TIMES:
        if now.time() >= t:
            slot = f"{now.date().isoformat()}/{t.hour:02d}"
    if slot is None or get_meta(conn, "digest_last_slot") == slot:
        return None
    return slot


def build_digest(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, since_iso: str
) -> tuple[T.MessageView | None, int]:
    repo = Repo(conn)
    courses = {c.id: c for c in repo.list_courses()}
    today = clock.today()
    sent_keys = {r["dedupe_key"] for r in conn.execute("SELECT dedupe_key FROM outbox WHERE kind='event'")}
    groups: dict[str, list[str]] = {}
    total = 0
    for ev in repo.list_events(since=since_iso, limit=500):
        if ev.event_type in (EventType.BASELINE, EventType.REMOVED) or f"event:{ev.id}" in sent_keys:
            continue
        item = repo.get_item(ev.item_id)
        if item is None or ev.course_id not in courses:
            continue
        extra = None
        if item.kind == ItemKind.ANNOUNCEMENT:
            extra = _announcement_gist(conn, item)
        groups.setdefault(ev.course_id, []).append(
            T.digest_line(ev.event_type, item, settings.tz, today, extra)
        )
        total += 1
    if not groups:
        return None, 0
    shown = sum(min(3, len(v)) for v in list(groups.values())[:3])
    nm = {cid: display_name(courses[cid].name, names(settings)) for cid in groups}
    return T.digest(groups, nm, more=max(0, total - shown)), total


def _announcement_gist(conn: sqlite3.Connection, item: Item) -> str | None:
    row = conn.execute(
        "SELECT json FROM llm_outputs WHERE task='announcement_interpretation' AND scope_id=? "
        "ORDER BY id DESC LIMIT 1",
        (item.id,),
    ).fetchone()
    if row:
        import json

        return json.loads(row["json"]).get("summary")
    return item.body


def enqueue_digest(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> bool:
    slot = digest_due(conn, clock)
    if slot is None:
        return False
    since = get_meta(conn, "digest_since") or to_utc_iso(clock.now() - timedelta(hours=12))
    view, total = build_digest(conn, settings, clock, since)
    conn.execute("BEGIN IMMEDIATE")
    set_meta(conn, "digest_last_slot", slot)
    set_meta(conn, "digest_since", clock.now_utc_iso())
    ok = False
    if view is not None:
        ok = Outbox(conn).enqueue_view("digest", f"digest:{slot}", view, clock.now_utc_iso())
    conn.execute("COMMIT")
    return ok


def sync_failure_alert(
    conn: sqlite3.Connection,
    settings: Settings,
    clock: Clock,
    key: str,
    detail: str | None = None,
    session: bool = False,
) -> bool:
    last_ok = get_meta(conn, "last_success_at")
    view = T.session_expired() if session else T.sync_failed(last_ok, settings.tz, detail)
    return Outbox(conn).enqueue_view("ops", f"ops:{key}", view, clock.now_utc_iso(), urgent=True)


def local_dt(value: str | datetime | None, tz) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(tz)
    from sigaa_sim.clock import from_utc_iso

    return from_utc_iso(value).astimezone(tz)
