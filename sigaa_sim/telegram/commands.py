"""Comandos e perguntas do Telegram → consulta de dados → ``MessageView``.

Texto livre é o caminho principal. Perguntas factuais sobre datas ("quando é minha prova de
Física II?") são respondidas por template, sem LLM. Listas usam dados e templates determinísticos.
Callbacks (paginação, fontes, ajuda, progresso) editam a mensagem original quando faz sentido.

Só ``TELEGRAM_USER_ID`` em ``TELEGRAM_CHAT_ID`` é atendido (mensagens e callbacks). Texto recebido
nunca é executado: vira termo de busca ou dado dentro de um prompt.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import unicodedata
from datetime import date, timedelta

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.domain import Course, EventType, Item, ItemKind
from sigaa_sim.notify import agenda_items, build_digest
from sigaa_sim.storage import Jobs, Outbox, Repo, get_meta, set_meta
from sigaa_sim.telegram import templates as T
from sigaa_sim.telegram.templates import Agenda
from sigaa_sim.telegram.views import Block, Button, MessageView, display_name, fmt_day, fmt_full, sentence

log = logging.getLogger(__name__)

_CMD_RE = re.compile(r"^/([a-zA-Z_]+)(?:@\w+)?(?:\s+(.*))?$", re.S)
HORIZON = 30


def parse_command(text: str | None) -> tuple[str | None, str]:
    if not text:
        return None, ""
    m = _CMD_RE.match(text.strip())
    if not m:
        return None, ""
    return m.group(1).lower(), (m.group(2) or "").strip()


def is_authorized(update: dict, settings: Settings) -> bool:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    frm = update.get("callback_query", {}).get("from") if "callback_query" in update else msg.get("from")
    frm_id = str((frm or {}).get("id", ""))
    chat = str((msg.get("chat") or {}).get("id", ""))
    return (
        bool(frm_id)
        and frm_id == settings.secrets.telegram_user_id
        and chat == settings.secrets.telegram_chat_id
    )


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip()


class Ctx:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, clock: Clock):
        self.conn, self.settings, self.clock = conn, settings, clock
        self.repo = Repo(conn)
        self.tz = settings.tz
        self.today = clock.today()
        self.courses = {c.id: c for c in self.repo.list_courses()}
        self.names = settings.display_names

    def name(self, course: Course) -> str:
        return display_name(course.name, self.names)

    def find_course(self, text: str) -> Course | None:
        """Aceita nome oficial, nome curto e aliases; exige casamento de palavra inteira (≥4 letras)."""
        q = _norm(text)
        if not q:
            return None
        best, best_len = None, 0
        for c in self.courses.values():
            for cand in {_norm(c.name), _norm(self.name(c)), _norm(c.code)}:
                if cand and (cand in q or q in cand) and len(cand) > best_len:
                    best, best_len = c, len(cand)
            for word in set(_norm(self.name(c)).split()):
                if len(word) >= 4 and re.search(rf"\b{re.escape(word)}\b", q) and len(word) > best_len:
                    best, best_len = c, len(word)
        return best

    def last_update(self) -> str | None:
        return get_meta(self.conn, "last_success_at")

    def agenda(self, horizon: int = HORIZON) -> list[Agenda]:
        return agenda_items(self.repo, self.settings, self.today, horizon)


# ------------------------------------------------------------------ intents
def deadlines_view(ctx: Ctx, page: int = 1) -> MessageView:
    view = T.deadlines_page(ctx.agenda(), page, ctx.tz, ctx.today, HORIZON, ctx.names, ctx.last_update())
    last = ctx.repo.last_run()
    if last and last["status"] == "failed":
        view.footer = (view.footer or "") + f" · dados de {fmt_full(ctx.last_update(), ctx.tz)}"
    return view


def factual_date_question(ctx: Ctx, text: str) -> MessageView | None:
    """'quando é minha prova de física' / 'que dia entrega o relatório' → template D, sem LLM."""
    q = _norm(text)
    if not re.search(r"\b(quando|que dia|qual (?:a )?data|data d[ae]|prazo)\b", q):
        return None
    kinds = None
    if re.search(r"\b(prova|avaliac|exame)", q):
        kinds = (ItemKind.ASSESSMENT,)
    elif re.search(r"\b(entrega|trabalho|relatorio|tarefa|lista|atividade)", q):
        kinds = (ItemKind.ACTIVITY,)
    course = ctx.find_course(q)
    agenda = [
        a
        for a in ctx.agenda(120)
        if (kinds is None or a.item.kind in kinds) and (course is None or a.course.id == course.id)
    ]
    if course is None and kinds is None:
        return None
    if not agenda:
        who = f" de {ctx.name(course)}" if course else ""
        what = "prova" if kinds == (ItemKind.ASSESSMENT,) else ("entrega" if kinds else "prazo")
        return MessageView(
            title=f"Nenhuma {what}{who} nos dados consultados",
            blocks=[
                Block(
                    [
                        "Considerando os próximos 120 dias.",
                        f"Última atualização: {fmt_full(ctx.last_update(), ctx.tz)}.",
                    ]
                )
            ],
            buttons=[Button("Ver prazos", "pz:1")],
        )
    if len(agenda) > 1 and course is None:
        return deadlines_view(ctx)
    a = agenda[0]
    view = T.factual_deadline(a, ctx.tz, ctx.today, ctx.names)
    if len(agenda) > 1:
        n = len(agenda) - 1
        view.blocks.append(
            Block(
                [
                    f"Depois dessa, há {n} outro compromisso de {ctx.name(course)}."
                    if n == 1
                    else f"Depois dessa, há {n} outros compromissos de {ctx.name(course)}."
                ]
            )
        )
        view.buttons.append(Button("Ver prazos", "pz:1"))
    return view


def grades_view(ctx: Ctx) -> MessageView:
    grades = ctx.repo.list_items(kind=ItemKind.GRADE)
    if not grades:
        return MessageView(
            title="Nenhuma nota lançada ainda",
            footer=f"Última atualização: {fmt_full(ctx.last_update(), ctx.tz)}",
        )
    by_course: dict[str, list[Item]] = {}
    for g in grades:
        by_course.setdefault(g.course_id, []).append(g)
    blocks = []
    for cid, gs in by_course.items():
        lines = [ctx.name(ctx.courses[cid])]
        for g in gs[:6]:
            val = (g.grade_value or "—") + (f" / {g.grade_max}" if g.grade_max else "")
            lines.append(f"{g.title}: {val}")
        blocks.append(Block(lines, bold=[lines[0]]))
    return MessageView(title="Notas lançadas", emoji="📝", blocks=blocks[:6], footer="Fonte: SIGAA · Notas")


def announcements_view(ctx: Ctx, arg: str, page: int = 1) -> MessageView:
    course = ctx.find_course(arg) if arg else None
    items = ctx.repo.list_items(course_id=course.id if course else None, kind=ItemKind.ANNOUNCEMENT)
    items.sort(
        key=lambda i: (i.published_at.date() if i.published_at else i.published_date) or date.min,
        reverse=True,
    )
    if not items:
        return MessageView(
            title="Nenhum aviso encontrado",
            blocks=[Block([f"Última atualização: {fmt_full(ctx.last_update(), ctx.tz)}."])],
        )
    size = 3
    start = (page - 1) * size
    chunk = items[start : start + size]
    blocks = [
        Block(
            [
                f"{fmt_day(i.published_at or i.published_date, ctx.tz, ctx.today)} · "
                f"{ctx.name(ctx.courses[i.course_id])}",
                i.title,
            ]
            + ([sentence(i.body, 180)] if i.body else []),
            bold=[i.title],
        )
        for i in chunk
    ]
    end = start + len(chunk)
    buttons = []
    key = course.id[-40:] if course else "all"
    if end < len(items):
        buttons.append(Button("Ver mais avisos", f"av:{key}:{page + 1}"))
    if page > 1:
        buttons.append(Button("Voltar", f"av:{key}:1"))
    return MessageView(
        title="Últimos avisos",
        emoji="📢",
        subtitle=ctx.name(course) if course else None,
        blocks=blocks,
        footer=f"Mostrando {start + 1}–{end} de {len(items)} · fonte: SIGAA · Notícias",
        buttons=buttons,
    )


def materials_view(ctx: Ctx, arg: str, page: int = 1) -> MessageView:
    course = ctx.find_course(arg) if arg else None
    term = "" if course else _norm(arg)
    mats = ctx.repo.list_items(course_id=course.id if course else None, kind=ItemKind.MATERIAL)
    if term:
        mats = [m for m in mats if term in _norm(m.title) or term in _norm(m.extra.get("topic") or "")]
    if not mats:
        return MessageView(
            title="Nenhum material encontrado",
            blocks=[Block(["Posso listar por disciplina: /materiais Física II"])],
        )
    size = 6
    start = (page - 1) * size
    chunk = mats[start : start + size]
    blocks = []
    for m in chunk:
        topic = m.extra.get("topic")
        blocks.append(
            Block(
                [m.title, f"{ctx.name(ctx.courses[m.course_id])}" + (f" · {topic}" if topic else "")],
                bold=[m.title],
            )
        )
    end = start + len(chunk)
    buttons = []
    key = course.id[-40:] if course else "all"
    if end < len(mats):
        buttons.append(Button(f"Ver mais ({len(mats) - end})", f"mat:{key}:{page + 1}"))
    if page > 1:
        buttons.append(Button("Voltar", f"mat:{key}:1"))
    return MessageView(
        title="Materiais",
        emoji="📎",
        subtitle=ctx.name(course) if course else None,
        blocks=blocks,
        footer=f"Mostrando {start + 1}–{end} de {len(mats)} · fonte: SIGAA · Arquivos",
        buttons=buttons,
    )


def search_view(ctx: Ctx, term: str) -> MessageView:
    from sigaa_sim.knowledge import Knowledge

    hits = Knowledge(ctx.conn, ctx.settings).search(term, limit=3)
    if not hits:
        return T.not_found(ctx.find_course(term), ctx.names)
    blocks = [
        Block(
            [
                f"{h['title']} · {h['ref']}",
                ctx.name(ctx.courses[h["course_id"]]),
                sentence(h["text"].replace("\n", " "), 200),
            ],
            bold=[f"{h['title']} · {h['ref']}"],
        )
        for h in hits
    ]
    return MessageView(title=f"Resultados para “{term[:40]}”", emoji="🔎", blocks=blocks)


def courses_view(ctx: Ctx) -> MessageView:
    cs = list(ctx.courses.values())
    if not cs:
        return MessageView(title="Nenhuma disciplina coletada ainda")
    lines = []
    for c in cs:
        n = ctx.repo.count("items", "course_id=? AND kind='material' AND removed_at IS NULL", (c.id,))
        lines.append(f"{ctx.name(c)} — {n} arquivo{'s' if n != 1 else ''}")
    return MessageView(
        title=f"Turmas de {cs[0].term}",
        emoji="🎓",
        blocks=[Block(lines)],
        footer="Nomes oficiais em /disciplinas completo",
        buttons=[Button("Nomes oficiais", "disc:full")],
    )


def courses_full_view(ctx: Ctx) -> MessageView:
    return MessageView(
        title="Disciplinas (nome oficial)",
        blocks=[Block([f"{c.name} ({c.code})" for c in ctx.courses.values()])],
    )


def digest_view(ctx: Ctx, full: bool = False) -> MessageView:
    since = to_utc_iso(ctx.clock.now() - timedelta(hours=24))
    view, total = build_digest(ctx.conn, ctx.settings, ctx.clock, since)
    if view is None:
        return MessageView(
            title="Sem novidades nas últimas 24 horas",
            blocks=[Block([f"Última atualização: {fmt_full(ctx.last_update(), ctx.tz)}."])],
        )
    view.subtitle = "Últimas 24 horas"
    if full:
        evs = [e for e in ctx.repo.list_events(since=since, limit=500) if e.event_type != EventType.BASELINE]
        lines = []
        for e in evs[:25]:
            it = ctx.repo.get_item(e.item_id)
            if it:
                lines.append(
                    f"{ctx.name(ctx.courses[e.course_id])}: "
                    f"{T.digest_line(e.event_type, it, ctx.tz, ctx.today)}"
                )
        view = MessageView(title="Todas as novidades", subtitle="Últimas 24 horas", blocks=[Block(lines)])
    return view


def study_view(ctx: Ctx, day: date | None = None, full: bool = False) -> MessageView:
    from sigaa_sim.llm import make_llm
    from sigaa_sim.planner import build_plan, load_plan, needs_replan, plan_tasks_rows, render_plan

    day = day or ctx.today
    existing = load_plan(ctx.conn, day)
    force = existing is not None and needs_replan(ctx.conn, existing, ctx.settings)
    plan = build_plan(
        ctx.conn, ctx.settings, ctx.clock, day, llm=make_llm(ctx.settings, ctx.conn, ctx.clock), force=force
    )
    if full:
        return MessageView(
            title="Plano completo", blocks=[Block(render_plan(plan, ctx.tz, ctx.conn).split("\n")[1:])]
        )
    if not plan.tasks:
        reason = plan.notes[0] if plan.notes else "Não há prazos no horizonte configurado."
        return T.no_study(sentence(reason, 200))
    rows = plan_tasks_rows(ctx.conn, plan.id) if plan.id else []
    task = plan.tasks[0]
    task_id = next((r["id"] for r in rows if r["status"] in ("pending", "in_progress")), None)
    if task_id is not None:
        idx = next(i for i, r in enumerate(rows) if r["id"] == task_id)
        task = plan.tasks[idx]
    course = ctx.courses.get(task.course_id)
    steps = [(s.minutes, s.action) for s in task.steps]
    return T.study_step(
        course,
        task.minutes,
        task.objective,
        steps,
        task.done_when,
        task.why_now,
        ctx.names,
        has_full_plan=len(plan.tasks) > 1,
        task_id=task_id,
    )


def question_view(ctx: Ctx, text: str) -> MessageView:
    fact = factual_date_question(ctx, text)
    if fact is not None:
        return fact
    if re.search(r"\b(novidade|o que mudou|atualiza)", _norm(text)):
        return digest_view(ctx)
    if re.search(r"\b(o que (eu )?estud|estudar hoje|por onde come)", _norm(text)):
        return study_view(ctx)
    from sigaa_sim.llm import make_llm
    from sigaa_sim.llm.ask import ask_structured

    res = ask_structured(ctx.conn, ctx.settings, ctx.clock, make_llm(ctx.settings, ctx.conn, ctx.clock), text)
    if res is None:
        return search_view(ctx, text)
    if res.get("unavailable"):
        v = search_view(ctx, text)
        v.footer = "A explicação por IA está indisponível agora; estes são os trechos encontrados."
        return v
    if not res["answer"]:
        return T.not_found(ctx.find_course(text), ctx.names)
    return T.content_answer(res["answer"], res["sources"], res["gaps"])


def source_view(ctx: Ctx, key: str) -> MessageView:
    row = ctx.conn.execute("SELECT current_json FROM items WHERE id LIKE ? LIMIT 1", (f"%{key}",)).fetchone()
    if not row:
        return MessageView(title="Fonte não encontrada")
    item = Item.model_validate_json(row["current_json"])
    course = ctx.courses.get(item.course_id)
    lines = [f"Título: {item.title}", f"Disciplina: {course.name if course else '—'}"]
    pub = item.published_at or item.published_date
    if pub:
        lines.append(f"Publicado em: {fmt_full(pub, ctx.tz)}")
    if item.due_at or item.due_date:
        lines.append(f"Prazo: {fmt_full(item.due_at or item.due_date, ctx.tz)}")
    if item.scope:
        lines.append(f"Conteúdo: {item.scope}")
    blocks = [Block(lines)]
    if item.body:
        blocks.append(Block([item.body.strip()[:1500]]))
    return MessageView(
        title="Fonte", subtitle=T.source(item, course, ctx.names) if course else None, blocks=blocks
    )


def progress_view(ctx: Ctx, arg: str) -> MessageView:
    from sigaa_sim.planner import load_plan, plan_tasks_rows, record_progress

    m = re.match(
        r"^(\d+)\s*(conclu\w*|done|feito|pulei|skipped|comec\w*|in_progress)?(?:\s+(f[aá]cil|ok|dif[ií]cil|easy|hard))?(?:\s+(.*))?$",
        _norm(arg),
    )
    if m:
        status = {
            "done": "done",
            "feito": "done",
            "skipped": "skipped",
            "pulei": "skipped",
            "in_progress": "in_progress",
        }
        st = "done"
        if m.group(2):
            w = m.group(2)
            st = status.get(w) or (
                "done" if w.startswith("conclu") else "in_progress" if w.startswith("comec") else "done"
            )
        diff = {"facil": "easy", "ok": "ok", "dificil": "hard", "easy": "easy", "hard": "hard"}.get(
            m.group(3) or ""
        )
        try:
            record_progress(ctx.conn, ctx.clock, int(m.group(1)), st, None, diff, m.group(4))
        except ValueError as e:
            return MessageView(title="Não encontrei essa sessão", blocks=[Block([str(e)])])
        label = {
            "done": "Sessão concluída",
            "skipped": "Sessão pulada",
            "in_progress": "Sessão em andamento",
        }[st]
        return MessageView(
            title=label, blocks=[Block(["Marcação feita por você. Isso não altera a entrega no SIGAA."])]
        )
    plan = load_plan(ctx.conn, ctx.today)
    if not plan or not plan.id:
        return MessageView(
            title="Sem plano para hoje", blocks=[Block(["Peça /hoje para gerar um passo de estudo."])]
        )
    rows = plan_tasks_rows(ctx.conn, plan.id)
    mark = {"done": "✅", "in_progress": "▶️", "skipped": "⏭", "pending": "▫️"}
    lines = [
        f"{mark[r['status']]} {r['id']} · {r['minutes']} min · {sentence(r['objective'], 70)}" for r in rows
    ]
    return MessageView(
        title="Sessões de hoje",
        blocks=[Block(lines), Block(["Para marcar: /progresso 12 concluí (fácil, ok ou difícil)"])],
    )


def status_view(ctx: Ctx) -> MessageView:
    last = ctx.repo.last_run()
    ok = ctx.last_update()
    lines = [f"Última atualização: {fmt_full(ok, ctx.tz) if ok else 'ainda não houve'}"]
    if last:
        stats = json.loads(last["stats_json"] or "{}")
        state = {
            "success": "completa",
            "partial": "parcial",
            "failed": "falhou",
            "running": "em andamento",
        }.get(last["status"], last["status"])
        lines.append(
            f"Última coleta: {state}"
            + (
                f" ({stats.get('sections_failed', 0)} seção(ões) com falha)"
                if stats.get("sections_failed")
                else ""
            )
        )
    pend = Jobs(ctx.conn).pending_by_kind()
    lines.append(
        "Pendências: "
        + (", ".join(f"{v} {k.replace('_', ' ')}" for k, v in pend.items()) if pend else "nenhuma")
    )
    return MessageView(
        title="Status", emoji="⚙️", blocks=[Block(lines)], buttons=[Button("Detalhes", "status:full")]
    )


def status_full_view(ctx: Ctx) -> MessageView:
    ai = ctx.conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(total_tokens),0) t FROM llm_calls WHERE created_at >= date('now')"
    ).fetchone()
    downloaded = ctx.repo.count("materials", "status='downloaded'")
    lines = [
        f"Disciplinas: {ctx.repo.count('courses')}",
        f"Itens: {ctx.repo.count('items', 'removed_at IS NULL')}",
        f"Materiais baixados: {downloaded}",
        f"Mensagens: {Outbox(ctx.conn).counts() or {}}",
        f"IA hoje: {ai['c']} chamadas, {ai['t']} tokens",
    ]
    return MessageView(title="Detalhes do status", blocks=[Block(lines)])


# ------------------------------------------------------------------ dispatch
def handle(ctx: Ctx, cmd: str, arg: str) -> MessageView:
    if cmd in ("start", "ajuda", "help"):
        return T.help_short()
    if cmd == "prazos":
        return deadlines_view(ctx)
    if cmd in ("hoje", "plano"):
        day = ctx.today
        if arg:
            try:
                day = date.fromisoformat(arg)
            except ValueError:
                return MessageView(title="Use /plano AAAA-MM-DD")
        return study_view(ctx, day)
    if cmd == "notas":
        return grades_view(ctx)
    if cmd == "avisos":
        return announcements_view(ctx, arg)
    if cmd == "materiais":
        return materials_view(ctx, arg)
    if cmd == "buscar":
        return search_view(ctx, arg) if arg else MessageView(title="Use /buscar termo")
    if cmd == "perguntar":
        return (
            question_view(ctx, arg) if arg else MessageView(title="Escreva sua pergunta depois de /perguntar")
        )
    if cmd == "disciplinas":
        return courses_view(ctx)
    if cmd == "resumo":
        return digest_view(ctx)
    if cmd == "progresso":
        return progress_view(ctx, arg)
    if cmd == "status":
        return status_view(ctx)
    return T.help_short()


def handle_callback(ctx: Ctx, data: str) -> tuple[MessageView, bool]:
    """Retorna (view, editar_mensagem_original)."""
    parts = data.split(":")
    kind = parts[0]
    if kind == "pz":
        return deadlines_view(ctx, int(parts[1]) if len(parts) > 1 else 1), True
    if kind == "help":
        return (T.help_full() if len(parts) > 1 and parts[1] == "all" else T.help_short()), False
    if kind == "src":
        return source_view(ctx, parts[1]), False
    if kind == "av":
        course = (
            ctx.courses.get(next((c for c in ctx.courses if c.endswith(parts[1])), ""))
            if parts[1] != "all"
            else None
        )
        page = int(parts[2]) if len(parts) > 2 else 1
        return announcements_view(ctx, course.name if course else "", page), True
    if kind == "mat":
        course = (
            ctx.courses.get(next((c for c in ctx.courses if c.endswith(parts[1])), ""))
            if parts[1] != "all"
            else None
        )
        page = int(parts[2]) if len(parts) > 2 else 1
        return materials_view(ctx, course.name if course else "", page), True
    if kind == "notas":
        return grades_view(ctx), False
    if kind == "resumo":
        return digest_view(ctx, full=True), False
    if kind == "plan":
        return study_view(ctx, full=True), False
    if kind == "done":
        return progress_view(ctx, f"{parts[1]} concluí"), False
    if kind == "disc":
        return courses_full_view(ctx), False
    if kind == "status":
        return status_full_view(ctx), False
    return T.help_short(), False


def process_update(conn: sqlite3.Connection, settings: Settings, clock: Clock, update: dict, client) -> None:
    uid = int(update["update_id"])
    if conn.execute("SELECT 1 FROM telegram_updates WHERE update_id=?", (uid,)).fetchone():
        return
    cb = update.get("callback_query")
    msg = update.get("message") or (cb or {}).get("message") or {}
    text = (msg.get("text") or "").strip() if not cb else ""
    cmd, arg = parse_command(text)
    authorized = is_authorized(update, settings)
    frm = (cb or {}).get("from") if cb else msg.get("from")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO telegram_updates(update_id, from_user_id, chat_id, command, authorized, processed_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            uid,
            str((frm or {}).get("id", "")),
            str((msg.get("chat") or {}).get("id", "")),
            cmd or ("cb:" + (cb or {}).get("data", "")[:20] if cb else None),
            int(authorized),
            clock.now_utc_iso(),
        ),
    )
    conn.execute("COMMIT")
    if not authorized:
        log.warning("update %s de usuário não autorizado ignorado", uid)
        if cb:
            client.answer_callback(cb["id"])
        return
    ctx = Ctx(conn, settings, clock)
    try:
        if cb:
            view, edit = handle_callback(ctx, cb.get("data", ""))
            client.answer_callback(cb["id"])
            if edit and msg.get("message_id") is not None:
                client.edit_view(str(msg["message_id"]), view)
            else:
                client.send_view(view)
            return
        if cmd is None:
            if not text or text.startswith("/"):
                return
            view = question_view(ctx, text)
        else:
            view = handle(ctx, cmd, arg)
    except Exception as e:
        log.exception("erro ao processar update %s", uid)
        view = MessageView(
            title="Não consegui responder agora",
            blocks=[Block([f"Erro interno ({type(e).__name__}). Tente de novo em instantes."])],
        )
    client.send_view(view)


def poll_loop(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, once: bool = False, timeout: int = 30
) -> int:
    from sigaa_sim.telegram.client import HttpTelegramClient

    client = HttpTelegramClient(settings.secrets.telegram_bot_token, settings.secrets.telegram_chat_id)
    offset = int(get_meta(conn, "telegram_offset", "0") or 0)
    print("telegram-poll: aguardando comandos (Ctrl+C para sair)")
    try:
        while True:
            try:
                updates = client.get_updates(offset, timeout)
            except Exception as e:
                log.warning("getUpdates falhou: %s", type(e).__name__)
                time.sleep(5)
                if once:
                    return 1
                continue
            for upd in updates:
                process_update(conn, settings, clock, upd, client)
                offset = max(offset, int(upd["update_id"]) + 1)
                conn.execute("BEGIN IMMEDIATE")
                set_meta(conn, "telegram_offset", str(offset))
                conn.execute("COMMIT")
            if once:
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        client.close()
