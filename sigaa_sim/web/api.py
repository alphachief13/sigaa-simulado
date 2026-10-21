"""Consultas da interface web. Cada função recebe ``(conn, settings, clock)`` e devolve dados
serializáveis em JSON. Nada aqui fala com SIGAA, Groq ou Telegram diretamente — só lê o banco
e reutiliza planner, knowledge base e o roteador de perguntas do Telegram.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sigaa_sim.clock import Clock, from_utc_iso, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.domain import Course, EventType, Item, ItemKind
from sigaa_sim.notify import agenda_items
from sigaa_sim.storage import Jobs, Outbox, Repo, get_meta, has_fts
from sigaa_sim.telegram import templates as T
from sigaa_sim.telegram.views import MessageView, display_name, fmt_day, relative_days

KIND_LABEL = {
    "announcement": "Aviso",
    "activity": "Tarefa",
    "assessment": "Avaliação",
    "grade": "Nota",
    "content": "Conteúdo",
    "material": "Material",
}

EVENT_LABEL = {
    "created": "novo",
    "updated": "alterado",
    "deadline_changed": "prazo alterado",
    "grade_changed": "nota alterada",
    "material_replaced": "arquivo substituído",
    "removed": "removido",
    "baseline": "baseline",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ helpers
def _names(settings: Settings) -> dict[str, str]:
    return settings.display_names


def _course_dict(c: Course, settings: Settings) -> dict[str, Any]:
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "short_name": display_name(c.name, _names(settings)),
        "term": c.term,
        "professor": c.professor,
        "url": c.url,
    }


def _iso(v: date | datetime | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return to_utc_iso(v)
    return v.isoformat()


def _item_dict(it: Item, courses: dict[str, Course], settings: Settings, today: date) -> dict[str, Any]:
    tz = settings.tz
    course = courses.get(it.course_id)
    due = it.due_at or it.due_date
    due_day = it.due_at.astimezone(tz).date() if it.due_at else it.due_date
    published = it.published_at or it.published_date
    return {
        "id": it.id,
        "course_id": it.course_id,
        "course": display_name(course.name, _names(settings)) if course else it.course_id,
        "kind": str(it.kind),
        "kind_label": KIND_LABEL.get(str(it.kind), str(it.kind)),
        "title": it.title,
        "body": it.body,
        "url": it.url,
        "published": _iso(published),
        "published_label": fmt_day(published, tz, today) if published else None,
        "due": _iso(due),
        "due_label": fmt_day(due, tz, today) if due else None,
        "due_relative": relative_days(due_day, today) if due_day else None,
        "days_left": (due_day - today).days if due_day else None,
        "weight": it.weight,
        "scope": it.scope,
        "grade_value": it.grade_value,
        "grade_max": it.grade_max,
        "grade_label": it.grade_label,
        "file_name": it.file_name,
        "file_url": it.file_url,
        "topic": it.extra.get("topic"),
    }


def _last_run(repo: Repo) -> dict[str, Any] | None:
    row = repo.last_run()
    if not row:
        return None
    stats = json.loads(row["stats_json"] or "{}")
    return {
        "id": row["id"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "adapter": row["adapter"],
        "baseline": bool(row["baseline"]),
        "error": row["error"],
        "stats": stats,
    }


def _ai_today(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(total_tokens),0) t FROM llm_calls WHERE created_at >= date('now')"
    ).fetchone()
    return {"calls": int(row["c"]), "tokens": int(row["t"])}


def view_dict(view: MessageView) -> dict[str, Any]:
    return view.to_dict()


# ------------------------------------------------------------------ overview
def overview(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> dict[str, Any]:
    repo = Repo(conn)
    today = clock.today()
    courses = {c.id: c for c in repo.list_courses()}
    agenda = agenda_items(repo, settings, today, 30)
    since = to_utc_iso(clock.now() - timedelta(days=7))
    events = [e for e in repo.list_events(since=since, limit=500) if e.event_type != EventType.BASELINE]
    last = _last_run(repo)
    sections_bad = []
    if last:
        for s in repo.sections_for_run(last["id"]):
            if s["status"] in ("failed", "partial"):
                c = courses.get(s["course_id"])
                sections_bad.append(
                    {
                        "course": display_name(c.name, _names(settings)) if c else s["course_id"],
                        "section": s["section"],
                        "status": s["status"],
                        "error": s["error"],
                    }
                )
    grades_recent = [
        e
        for e in events
        if e.kind == ItemKind.GRADE and e.event_type in (EventType.CREATED, EventType.GRADE_CHANGED)
    ]
    plan = _plan_summary(conn, settings, today)
    return {
        "today": today.isoformat(),
        "now": clock.now().isoformat(),
        "profile": settings.sigaa.profile,
        "institution": settings.sigaa.institution,
        "last_success_at": get_meta(conn, "last_success_at"),
        "last_run": last,
        "sections_bad": sections_bad[:10],
        "kpis": {
            "courses": len(courses),
            "items": repo.count("items", "removed_at IS NULL"),
            "deadlines_7d": sum(1 for a in agenda if (a.due - today).days <= 7),
            "deadlines_30d": len(agenda),
            "events_7d": len(events),
            "grades_7d": len(grades_recent),
            "materials_downloaded": repo.count("materials", "status='downloaded'"),
            "materials_total": repo.count("materials"),
            "jobs_pending": sum(Jobs(conn).pending_by_kind().values()),
            "outbox_pending": Outbox(conn).counts().get("pending", 0),
        },
        "next_deadlines": [
            {
                "item_id": a.item.id,
                "title": a.item.title,
                "course": display_name(a.course.name, _names(settings)),
                "kind": a.kind_label,
                "due": _iso(a.item.due_at or a.item.due_date),
                "due_label": fmt_day(a.item.due_at or a.item.due_date, settings.tz, today),
                "relative": relative_days(a.due, today),
                "days_left": (a.due - today).days,
            }
            for a in agenda[:5]
        ],
        "recent_events": _events_dicts(repo, events[-8:][::-1], courses, settings, today),
        "plan": plan,
        "ai_today": _ai_today(conn),
        "integrations": {
            "telegram": settings.telegram.enabled,
            "telegram_mode": settings.telegram.mode,
            "groq": settings.groq.enabled,
            "groq_model": settings.groq.model,
        },
    }


def _plan_summary(conn: sqlite3.Connection, settings: Settings, day: date) -> dict[str, Any] | None:
    from sigaa_sim.planner import load_plan, plan_tasks_rows

    plan = load_plan(conn, day)
    if not plan:
        return None
    rows = plan_tasks_rows(conn, plan.id) if plan.id else []
    done = sum(1 for r in rows if r["status"] == "done")
    return {
        "date": day.isoformat(),
        "version": plan.version,
        "source": plan.source,
        "available_minutes": plan.available_minutes,
        "tasks": len(plan.tasks),
        "done": done,
        "minutes_done": sum(r["minutes"] for r in rows if r["status"] == "done"),
    }


# ------------------------------------------------------------------ cursos
def courses(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> list[dict[str, Any]]:
    repo = Repo(conn)
    today = clock.today()
    out = []
    for c in repo.list_courses():
        d = _course_dict(c, settings)
        counts = {}
        for r in conn.execute(
            "SELECT kind, COUNT(*) n FROM items WHERE course_id=? AND removed_at IS NULL GROUP BY kind",
            (c.id,),
        ):
            counts[r["kind"]] = r["n"]
        d["counts"] = counts
        agenda = [a for a in agenda_items(repo, settings, today, 60) if a.course.id == c.id]
        d["next_deadline"] = (
            {
                "title": agenda[0].item.title,
                "due_label": fmt_day(agenda[0].item.due_at or agenda[0].item.due_date, settings.tz, today),
                "relative": relative_days(agenda[0].due, today),
                "days_left": (agenda[0].due - today).days,
            }
            if agenda
            else None
        )
        d["materials_downloaded"] = repo.count("materials", "course_id=? AND status='downloaded'", (c.id,))
        out.append(d)
    return out


def course_detail(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, course_id: str
) -> dict[str, Any]:
    repo = Repo(conn)
    course = repo.get_course(course_id)
    if not course:
        raise ApiError("disciplina não encontrada", 404)
    today = clock.today()
    courses_map = {course.id: course}
    items = repo.list_items(course_id=course.id)
    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(str(it.kind), []).append(_item_dict(it, courses_map, settings, today))
    return {
        "course": _course_dict(course, settings),
        "items": by_kind,
        "materials": materials(conn, settings, clock, course.id),
    }


# ------------------------------------------------------------------ prazos
def deadlines(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, horizon: int = 60
) -> list[dict[str, Any]]:
    repo = Repo(conn)
    today = clock.today()
    out = []
    for a in agenda_items(repo, settings, today, horizon):
        out.append(
            {
                "item_id": a.item.id,
                "title": a.item.title,
                "course_id": a.course.id,
                "course": display_name(a.course.name, _names(settings)),
                "kind": str(a.item.kind),
                "kind_label": a.kind_label,
                "due": _iso(a.item.due_at or a.item.due_date),
                "due_label": fmt_day(a.item.due_at or a.item.due_date, settings.tz, today, weekday=True),
                "has_time": a.item.due_at is not None,
                "relative": relative_days(a.due, today),
                "days_left": (a.due - today).days,
                "weight": a.item.weight,
                "scope": a.item.scope,
            }
        )
    return out


# ------------------------------------------------------------------ itens
def items(
    conn: sqlite3.Connection,
    settings: Settings,
    clock: Clock,
    kind: str | None = None,
    course_id: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    repo = Repo(conn)
    today = clock.today()
    courses_map = {c.id: c for c in repo.list_courses()}
    k = None
    if kind:
        try:
            k = ItemKind(kind)
        except ValueError as e:
            raise ApiError(f"tipo inválido: {kind}") from e
    out = repo.list_items(course_id=course_id, kind=k)
    if q:
        ql = q.lower()
        out = [i for i in out if ql in i.title.lower() or (i.body and ql in i.body.lower())]
    if k == ItemKind.ANNOUNCEMENT or k is None:
        out.sort(
            key=lambda i: (i.published_at.date() if i.published_at else i.published_date) or date.min,
            reverse=True,
        )
    return [_item_dict(i, courses_map, settings, today) for i in out[:limit]]


def item_detail(conn: sqlite3.Connection, settings: Settings, clock: Clock, item_id: str) -> dict[str, Any]:
    repo = Repo(conn)
    row = repo.get_item_row(item_id)
    if not row:
        raise ApiError("item não encontrado", 404)
    it = Item.model_validate_json(row["current_json"])
    courses_map = {c.id: c for c in repo.list_courses()}
    today = clock.today()
    d = _item_dict(it, courses_map, settings, today)
    d["revision"] = row["revision"]
    d["first_seen_at"] = row["first_seen_at"]
    d["last_seen_at"] = row["last_seen_at"]
    d["updated_at"] = row["updated_at"]
    d["removed_at"] = row["removed_at"]
    d["events"] = _events_dicts(
        repo, repo.list_events(item_id=item_id, limit=50)[::-1], courses_map, settings, today
    )
    d["revisions"] = [
        {"revision": r["revision"], "collected_at": r["collected_at"], "content_hash": r["content_hash"][:12]}
        for r in repo.item_revisions(item_id)
    ]
    if it.kind == ItemKind.MATERIAL:
        mats = materials(conn, settings, clock, it.course_id)
        d["material"] = next((m for m in mats if m["item_id"] == it.id), None)
    return d


# ------------------------------------------------------------------ eventos
def _events_dicts(
    repo: Repo, events, courses_map: dict[str, Course], settings: Settings, today: date
) -> list[dict]:
    out = []
    for e in events:
        it = repo.get_item(e.item_id)
        c = courses_map.get(e.course_id)
        out.append(
            {
                "id": e.id,
                "item_id": e.item_id,
                "course_id": e.course_id,
                "course": display_name(c.name, _names(settings)) if c else e.course_id,
                "kind": str(e.kind),
                "kind_label": KIND_LABEL.get(str(e.kind), str(e.kind)),
                "event_type": str(e.event_type),
                "event_label": EVENT_LABEL.get(str(e.event_type), str(e.event_type)),
                "title": it.title if it else e.item_id,
                "line": T.digest_line(e.event_type, it, settings.tz, today) if it else str(e.event_type),
                "changed_fields": e.changed_fields,
                "old": e.old,
                "new": e.new,
                "detected_at": e.detected_at,
            }
        )
    return out


def events(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, days: int = 14, limit: int = 200
) -> list[dict]:
    repo = Repo(conn)
    since = to_utc_iso(clock.now() - timedelta(days=days))
    courses_map = {c.id: c for c in repo.list_courses()}
    evs = [e for e in repo.list_events(since=since, limit=2000) if e.event_type != EventType.BASELINE]
    return _events_dicts(repo, evs[::-1][:limit], courses_map, settings, clock.today())


# ------------------------------------------------------------------ notas
def grades(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> list[dict[str, Any]]:
    repo = Repo(conn)
    courses_map = {c.id: c for c in repo.list_courses()}
    today = clock.today()
    groups: dict[str, list[dict]] = {}
    for g in repo.list_items(kind=ItemKind.GRADE):
        groups.setdefault(g.course_id, []).append(_item_dict(g, courses_map, settings, today))
    return [
        {
            "course_id": cid,
            "course": display_name(courses_map[cid].name, _names(settings)) if cid in courses_map else cid,
            "grades": gs,
        }
        for cid, gs in groups.items()
    ]


# ------------------------------------------------------------------ materiais
def materials(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, course_id: str | None = None
) -> list[dict]:
    from sigaa_sim.knowledge import Knowledge

    kb = Knowledge(conn, settings)
    repo = Repo(conn)
    courses_map = {c.id: c for c in repo.list_courses()}
    today = clock.today()
    out = []
    for r in kb.current_materials(course_id):
        it = Item.model_validate_json(r["current_json"])
        d = _item_dict(it, courses_map, settings, today)
        ver = conn.execute(
            "SELECT local_path, size_bytes, content_type, downloaded_at FROM material_versions "
            "WHERE material_id=? AND superseded_at IS NULL ORDER BY id DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        local_path = Path(ver["local_path"]) if ver else None
        summ = (
            conn.execute(
                "SELECT json FROM llm_outputs WHERE task='document_summary' AND scope_id=? "
                "ORDER BY id DESC LIMIT 1",
                (r["current_file_hash"],),
            ).fetchone()
            if r["current_file_hash"]
            else None
        )
        d.update(
            {
                "item_id": it.id,
                "status": r["status"],
                "error": r["error"],
                "doc_status": r["doc_status"],
                "doc_kind": r["doc_kind"],
                "page_count": r["page_count"],
                "size_bytes": ver["size_bytes"] if ver else None,
                "downloaded_at": ver["downloaded_at"] if ver else None,
                "file_available": bool(local_path and local_path.exists()),
                "summary": json.loads(summ["json"]) if summ else None,
            }
        )
        out.append(d)
    return out


def material_file(conn: sqlite3.Connection, settings: Settings, item_id: str) -> tuple[Path, str]:
    row = conn.execute(
        "SELECT v.local_path, v.content_type FROM materials m JOIN material_versions v ON v.material_id=m.id "
        "WHERE m.item_id=? AND v.superseded_at IS NULL ORDER BY v.id DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if not row:
        raise ApiError("arquivo não disponível", 404)
    path = Path(row["local_path"]).resolve()
    root = settings.materials_dir.resolve()
    if root not in path.parents or not path.exists():
        raise ApiError("arquivo fora do diretório de materiais", 403)
    return path, row["content_type"] or "application/octet-stream"


# ------------------------------------------------------------------ plano
def _plan_dict(conn: sqlite3.Connection, settings: Settings, plan, today: date) -> dict[str, Any]:
    from sigaa_sim.planner import plan_tasks_rows

    repo = Repo(conn)
    courses_map = {c.id: c for c in repo.list_courses()}
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM items")}
    rows = plan_tasks_rows(conn, plan.id) if plan.id else []
    tasks = []
    for i, t in enumerate(plan.tasks):
        row = rows[i] if i < len(rows) else None
        c = courses_map.get(t.course_id)
        tasks.append(
            {
                "task_id": row["id"] if row else None,
                "seq": i + 1,
                "course_id": t.course_id,
                "course": display_name(c.name, _names(settings)) if c else t.course_id,
                "objective": t.objective,
                "minutes": t.minutes,
                "why_now": t.why_now,
                "prerequisites": [titles.get(r, r) for r in t.prerequisites],
                "materials": [
                    {"id": r, "title": titles.get(r, r.rsplit(":", 1)[-1])} for r in t.material_refs
                ],
                "steps": [{"minutes": s.minutes, "action": s.action} for s in t.steps],
                "done_when": t.done_when,
                "link_to_assessment": t.link_to_assessment,
                "is_suggested_exercise": t.is_suggested_exercise,
                "status": row["status"] if row else "pending",
                "difficulty": row["difficulty"] if row else None,
                "note": row["note"] if row else None,
            }
        )
    return {
        "id": plan.id,
        "date": plan.plan_date.isoformat(),
        "version": plan.version,
        "source": plan.source,
        "available_minutes": plan.available_minutes,
        "used_minutes": sum(t.minutes for t in plan.tasks),
        "notes": plan.notes,
        "commitments": [
            {
                "item_id": c.item.id,
                "title": c.item.title,
                "course": display_name(c.course.name, _names(settings)),
                "due": c.due.isoformat(),
                "due_label": fmt_day(c.due, settings.tz, today),
                "days_left": (c.due - today).days,
                "priority": c.priority,
                "reasons": c.reasons,
            }
            for c in plan.commitments
        ],
        "tasks": tasks,
    }


def plan_get(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, day: date | None = None
) -> dict | None:
    from sigaa_sim.planner import load_plan, needs_replan

    day = day or clock.today()
    plan = load_plan(conn, day)
    if not plan:
        return None
    d = _plan_dict(conn, settings, plan, clock.today())
    d["stale"] = needs_replan(conn, plan, settings)
    return d


def plan_build(
    conn: sqlite3.Connection,
    settings: Settings,
    clock: Clock,
    day: date | None = None,
    new: bool = False,
    use_ai: bool = True,
) -> dict:
    from sigaa_sim.llm import make_llm
    from sigaa_sim.planner import build_plan, load_plan, needs_replan

    day = day or clock.today()
    llm = make_llm(settings, conn, clock) if use_ai else None
    existing = load_plan(conn, day)
    force = new or (existing is not None and needs_replan(conn, existing, settings))
    plan = build_plan(conn, settings, clock, day, llm=llm, force=force)
    d = _plan_dict(conn, settings, plan, clock.today())
    d["stale"] = False
    return d


def progress_record(
    conn: sqlite3.Connection,
    clock: Clock,
    task_id: int,
    status: str,
    minutes: int | None = None,
    difficulty: str | None = None,
    note: str | None = None,
) -> None:
    from sigaa_sim.planner import record_progress

    try:
        record_progress(conn, clock, task_id, status, minutes, difficulty or None, note or None)
    except ValueError as e:
        raise ApiError(str(e)) from e


def progress_log(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, days: int = 30
) -> dict[str, Any]:
    repo = Repo(conn)
    courses_map = {c.id: c for c in repo.list_courses()}
    since = to_utc_iso(clock.now() - timedelta(days=days))
    rows = conn.execute(
        """SELECT p.*, t.objective, t.minutes task_minutes FROM progress_log p
           LEFT JOIN study_tasks t ON t.id=p.task_id WHERE p.recorded_at>=? ORDER BY p.id DESC LIMIT 300""",
        (since,),
    ).fetchall()
    entries = []
    for r in rows:
        c = courses_map.get(r["course_id"])
        entries.append(
            {
                "task_id": r["task_id"],
                "course": display_name(c.name, _names(settings)) if c else r["course_id"],
                "status": r["status"],
                "minutes": r["minutes"] or (r["task_minutes"] if r["status"] == "done" else None),
                "difficulty": r["difficulty"],
                "note": r["note"],
                "objective": r["objective"],
                "recorded_at": r["recorded_at"],
            }
        )
    per_course = conn.execute(
        """SELECT course_id, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) d, COUNT(*) n,
                  SUM(CASE WHEN status='done' THEN minutes ELSE 0 END) m
           FROM study_tasks GROUP BY course_id"""
    ).fetchall()
    # minutos por dia (para gráfico de barras simples)
    daily: dict[str, int] = {}
    for e in entries:
        if e["status"] == "done" and e["minutes"]:
            day = from_utc_iso(e["recorded_at"]).astimezone(settings.tz).date().isoformat()
            daily[day] = daily.get(day, 0) + int(e["minutes"])
    return {
        "entries": entries,
        "per_course": [
            {
                "course": display_name(courses_map[r["course_id"]].name, _names(settings))
                if r["course_id"] in courses_map
                else r["course_id"],
                "done": r["d"],
                "total": r["n"],
                "minutes": r["m"] or 0,
            }
            for r in per_course
        ],
        "daily": [{"date": k, "minutes": v} for k, v in sorted(daily.items())],
    }


# ------------------------------------------------------------------ busca
def search(
    conn: sqlite3.Connection,
    settings: Settings,
    clock: Clock,
    q: str,
    course_id: str | None = None,
    limit: int = 12,
) -> dict:
    from sigaa_sim.knowledge import Knowledge

    repo = Repo(conn)
    courses_map = {c.id: c for c in repo.list_courses()}
    hits = Knowledge(conn, settings).search(q, course_id=course_id, limit=limit)
    return {
        "fts": has_fts(conn),
        "hits": [
            {
                "item_id": h["item_id"],
                "course_id": h["course_id"],
                "course": display_name(courses_map[h["course_id"]].name, _names(settings))
                if h["course_id"] in courses_map
                else h["course_id"],
                "title": h["title"],
                "ref": h["ref"],
                "text": h["text"],
                "score": h["score"],
            }
            for h in hits
        ],
    }


# ------------------------------------------------------------------ chat
def _chat_view(view: MessageView) -> dict:
    # a ajuda do Telegram lista "/comandos"; no painel ela vira o menu de botões
    d = view_dict(view)
    d["help"] = view.title in (T.help_short().title, T.help_full().title)
    return d


def chat_message(conn: sqlite3.Connection, settings: Settings, clock: Clock, text: str) -> dict:
    from sigaa_sim.telegram.commands import Ctx, handle, parse_command, question_view

    text = (text or "").strip()
    if not text:
        raise ApiError("mensagem vazia")
    ctx = Ctx(conn, settings, clock)
    cmd, arg = parse_command(text)
    view = handle(ctx, cmd, arg) if cmd else question_view(ctx, text)
    return _chat_view(view)


def chat_action(conn: sqlite3.Connection, settings: Settings, clock: Clock, callback: str) -> dict:
    from sigaa_sim.telegram.commands import Ctx, handle_callback

    ctx = Ctx(conn, settings, clock)
    view, _ = handle_callback(ctx, (callback or "")[:64])
    return _chat_view(view)


# ------------------------------------------------------------------ operação
def ops_status(conn: sqlite3.Connection, settings: Settings, clock: Clock) -> dict[str, Any]:
    repo = Repo(conn)
    s = settings.secrets
    lock = settings.data_dir / "sync.lock"
    lock_info = None
    if lock.exists():
        try:
            lock_info = {
                "pid": int(lock.read_text().strip() or 0),
                "age_s": int(datetime.now().timestamp() - lock.stat().st_mtime),
            }
        except (ValueError, OSError):
            lock_info = {"pid": None, "age_s": None}
    checks = [
        {"label": f"config: {settings.config_path or 'defaults'}", "ok": True},
        {
            "label": f"perfil SIGAA: {settings.sigaa.profile}",
            "ok": settings.sigaa.profile in ("simulado", "demo"),
        },
        {"label": "FTS5 disponível", "ok": has_fts(conn), "hint": "busca cai para LIKE"},
    ]
    if settings.sigaa.profile == "simulado":
        checks.append(
            {
                "label": "banco do SIGAA Simulado",
                "ok": settings.sim_db_path.exists(),
                "hint": "use Preparar tudo (ou Gerar SIGAA) nesta página",
            }
        )
    if settings.telegram.enabled and settings.telegram.mode == "http":
        checks.append({"label": "TELEGRAM_BOT_TOKEN", "ok": bool(s.telegram_bot_token)})
        checks.append({"label": "TELEGRAM_CHAT_ID", "ok": bool(s.telegram_chat_id)})
        checks.append({"label": "TELEGRAM_USER_ID", "ok": bool(s.telegram_user_id), "soft": True})
    if settings.groq.enabled:
        checks.append({"label": f"GROQ_API_KEY ({settings.groq.model})", "ok": bool(s.groq_api_key)})
    checks.append(
        {"label": "WEB_TOKEN definido (protege esta interface)", "ok": bool(s.web_token), "soft": True}
    )
    jobs = Jobs(conn)
    failed_jobs = [
        dict(r)
        for r in conn.execute(
            "SELECT id, kind, status, attempts, last_error, next_attempt_at FROM jobs "
            "WHERE status IN ('failed','deferred') ORDER BY id DESC LIMIT 20"
        )
    ]
    return {
        "checks": checks,
        "last_run": _last_run(repo),
        "last_success_at": get_meta(conn, "last_success_at"),
        "lock": lock_info,
        "outbox": Outbox(conn).counts(),
        "jobs": jobs.counts(),
        "jobs_pending_by_kind": jobs.pending_by_kind(),
        "jobs_failed": failed_jobs,
        "ai_today": _ai_today(conn),
        "ai_limits": {
            "calls_per_day": settings.groq.max_calls_per_day,
            "tokens_per_day": settings.groq.max_tokens_per_day,
        },
        "runs": [
            {
                "id": r["id"],
                "status": r["status"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "adapter": r["adapter"],
                "baseline": bool(r["baseline"]),
                "stats": json.loads(r["stats_json"] or "{}"),
                "error": r["error"],
            }
            for r in conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 15")
        ],
        "data_dir": str(settings.data_dir.resolve()),
        "db_path": str(settings.db_path.resolve()),
    }


def outbox_recent(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    out = []
    for r in Outbox(conn).recent(limit):
        d = {k: r[k] for k in r.keys() if k != "view_json"}
        d["view"] = json.loads(r["view_json"]) if r["view_json"] else None
        out.append(d)
    return out


def log_tail(settings: Settings, lines: int = 200) -> list[str]:
    path = settings.logs_dir / "sigaa_sim.log"
    if not path.exists():
        return []
    try:
        data = path.read_bytes()[-400_000:]
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def config_view(settings: Settings) -> dict[str, Any]:
    """Configuração sem segredos (só presença booleana)."""
    s = settings.secrets
    return {
        "config_path": str(settings.config_path) if settings.config_path else None,
        "general": settings.general.model_dump(mode="json"),
        "sigaa": {k: v for k, v in settings.sigaa.model_dump(mode="json").items()},
        "telegram": settings.telegram.model_dump(mode="json"),
        "groq": settings.groq.model_dump(mode="json"),
        "knowledge": settings.knowledge.model_dump(mode="json"),
        "planner": settings.planner.model_dump(mode="json"),
        "display_names": settings.display_names,
        "secrets_present": {
            "GROQ_API_KEY": bool(s.groq_api_key),
            "TELEGRAM_BOT_TOKEN": bool(s.telegram_bot_token),
            "TELEGRAM_CHAT_ID": bool(s.telegram_chat_id),
            "TELEGRAM_USER_ID": bool(s.telegram_user_id),
            "WEB_TOKEN": bool(s.web_token),
        },
    }
