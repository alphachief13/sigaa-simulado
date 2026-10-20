"""Study Planner.

Código calcula prazos, disponibilidade, prioridade e restrições; a IA (opcional) sugere a
sequência pedagógica. Todo plano é validado antes de ser armazenado; se a IA falhar ou
estiver indisponível, um plano básico determinístico é gerado com o que falta explicitado.

O plano básico escolhe as sessões do dia resolvendo uma **Mochila 0/1**
(``sigaa_sim.algoritmos.mochila``): cada sessão candidata é um item (peso = duração em blocos
de ``GRANULARIDADE`` minutos, valor = prioridade do compromisso, com retorno decrescente para
sessões repetidas da mesma disciplina) e a capacidade é o tempo livre do dia.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from sigaa_sim.algoritmos.mochila import ItemMochila, ResultadoMochila, mochila_01
from sigaa_sim.clock import Clock
from sigaa_sim.config import Settings
from sigaa_sim.domain import Course, Item, ItemKind
from sigaa_sim.llm.client import LLMInvalidOutput, LLMUnavailable, OutputCache
from sigaa_sim.llm.prompts import PLAN
from sigaa_sim.llm.schemas import PlanSuggestion, PlanTask
from sigaa_sim.storage import Repo, transaction
from sigaa_sim.telegram.format import fmt_when

log = logging.getLogger(__name__)


@dataclass
class Commitment:
    item: Item
    course: Course
    due: date
    days_left: int
    priority: float
    reasons: list[str]
    topics_frequency: int = 0
    progress_pending: float = 1.0


@dataclass
class Plan:
    plan_date: date
    available_minutes: int
    source: str  # llm | fallback
    tasks: list[PlanTask]
    notes: list[str] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    id: int | None = None
    version: int = 1


# ------------------------------------------------------------------ prioridade
def priority_score(
    settings: Settings, days_left: int, weight: float | None, frequency: int, pending: float
) -> tuple[float, list[str]]:
    """score = w_deadline*urgência + w_weight*peso + w_frequency*frequência + w_progress*pendência.

    urgência = 1/(1+dias) (0 dias → 1.0; 7 dias → 0.125); peso normalizado por 10;
    frequência = nº de materiais distintos que mencionam o tópico (cap 5)/5; pendência ∈ [0,1].
    """
    p = settings.planner
    urgency = 1.0 / (1 + max(days_left, 0))
    w = min((weight or 0) / 10.0, 1.0)
    f = min(frequency, 5) / 5.0
    score = p.w_deadline * urgency + p.w_weight * w + p.w_frequency * f + p.w_progress * pending
    reasons = [f"prazo em {days_left} dia(s)"]
    if weight:
        reasons.append(f"peso {weight:g}")
    if frequency:
        reasons.append(f"tópico em {frequency} material(is)")
    if pending < 1:
        reasons.append(f"{int(pending * 100)}% pendente")
    return round(score, 3), reasons


def available_minutes(settings: Settings, day: date) -> int:
    return settings.planner.minutes_for_weekday(day.weekday())


def collect_commitments(conn: sqlite3.Connection, settings: Settings, today: date) -> list[Commitment]:
    repo = Repo(conn)
    courses = {c.id: c for c in repo.list_courses()}
    out = []
    for it in repo.list_items():
        if it.kind not in (ItemKind.ACTIVITY, ItemKind.ASSESSMENT):
            continue
        due = it.due_at.astimezone(settings.tz).date() if it.due_at else it.due_date
        if due is None or due < today or (due - today).days > settings.planner.horizon_days:
            continue
        freq = _topic_frequency(conn, it)
        pending = _pending_ratio(conn, it.course_id)
        score, reasons = priority_score(settings, (due - today).days, it.weight, freq, pending)
        out.append(
            Commitment(it, courses[it.course_id], due, (due - today).days, score, reasons, freq, pending)
        )
    out.sort(key=lambda c: (-c.priority, c.due))
    return out


def _topic_frequency(conn: sqlite3.Connection, item: Item) -> int:
    """Nº de materiais distintos (por hash) cujos chunks mencionam termos do escopo.
    Evita contar cópias: agrupa por file_hash, não por chunk."""
    if not item.scope:
        return 0
    import re

    terms = [t for t in re.findall(r"\w{5,}", item.scope.lower())][:6]
    if not terms:
        return 0
    likes = " OR ".join("lower(c.text) LIKE ?" for _ in terms)
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT c.file_hash) n FROM chunks c
            JOIN materials m ON m.current_file_hash=c.file_hash
            WHERE m.course_id=? AND ({likes})""",
        [item.course_id] + [f"%{t}%" for t in terms],
    ).fetchone()
    return int(row["n"])


def _pending_ratio(conn: sqlite3.Connection, course_id: str) -> float:
    row = conn.execute(
        """SELECT SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) d, COUNT(*) n
           FROM study_tasks WHERE course_id=?""",
        (course_id,),
    ).fetchone()
    if not row or not row["n"]:
        return 1.0
    return round(1 - row["d"] / row["n"], 2)


# ----------------------------------------------------------------- contexto IA
def _materials_context(
    conn: sqlite3.Connection, course_ids: set[str], max_items: int = 12
) -> tuple[str, set[str]]:
    cache = OutputCache(conn, None)  # só leitura
    kb_rows = conn.execute(
        """SELECT m.item_id, m.course_id, m.current_file_hash, i.title, d.status
           FROM materials m JOIN items i ON i.id=m.item_id
           LEFT JOIN documents d ON d.file_hash=m.current_file_hash
           WHERE i.removed_at IS NULL AND m.status='downloaded' ORDER BY i.updated_at DESC"""
    ).fetchall()
    lines, ids = [], set()
    for r in kb_rows:
        if r["course_id"] not in course_ids or len(lines) >= max_items:
            continue
        summ = cache.latest("document_summary", r["current_file_hash"]) if r["current_file_hash"] else None
        if summ:
            topics = "; ".join(f"{t['topic']} ({', '.join(t['refs'][:2])})" for t in summ["topics"][:4])
            desc = f"{summ['summary'][:200]} | tópicos: {topics}"
            if summ.get("exercises_refs"):
                desc += f" | exercícios em: {', '.join(summ['exercises_refs'][:3])}"
        elif r["status"] == "extracted":
            desc = "(texto extraído, sem resumo ainda)"
        else:
            desc = f"(sem texto utilizável: {r['status']})"
        lines.append(f'- [{r["item_id"]}] course={r["course_id"]} "{r["title"]}": {desc}')
        ids.add(r["item_id"])
    return "\n".join(lines) or "(nenhum material processado)", ids


def _progress_context(conn: sqlite3.Connection, course_ids: set[str]) -> str:
    rows = conn.execute(
        """SELECT t.course_id, t.objective, t.status, t.difficulty, t.note
           FROM study_tasks t WHERE t.updated_at IS NOT NULL ORDER BY t.updated_at DESC LIMIT 12"""
    ).fetchall()
    lines = [
        f"- course={r['course_id']} [{r['status']}{'/' + r['difficulty'] if r['difficulty'] else ''}] "
        f"{r['objective'][:80]}" + (f" — nota: {r['note'][:80]}" if r["note"] else "")
        for r in rows
        if r["course_id"] in course_ids
    ]
    return "\n".join(lines) or "(sem progresso registrado)"


# ----------------------------------------------------------------- validação
class PlanValidationError(Exception):
    pass


def validate_plan(
    sug: PlanSuggestion,
    minutes: int,
    commitments: list[Commitment],
    known_refs: set[str],
    min_session: int,
    day: date | None = None,
) -> list[str]:
    """Retorna lista de problemas (vazia = válido)."""
    day = day or date.today()
    problems = []
    total = sum(t.minutes for t in sug.tasks)
    if total > minutes:
        problems.append(f"plano usa {total} min > {minutes} disponíveis")
    course_ids = {c.course.id for c in commitments}
    due_by_course = {c.course.id: c.due for c in commitments}
    for i, t in enumerate(sug.tasks):
        if t.minutes < min_session:
            problems.append(f"tarefa {i + 1} com {t.minutes} min (< mínimo {min_session})")
        if t.course_id not in course_ids:
            problems.append(f"tarefa {i + 1} cita disciplina desconhecida {t.course_id}")
        for ref in t.material_refs:
            if ref not in known_refs:
                problems.append(f"tarefa {i + 1} cita material inexistente {ref}")
        if sum(s.minutes for s in t.steps) > t.minutes:
            problems.append(f"tarefa {i + 1}: passos somam mais que a duração")
        if (
            t.course_id in due_by_course
            and due_by_course[t.course_id] < day
            and "atras" not in t.why_now.lower()
        ):
            problems.append(f"tarefa {i + 1}: compromisso já vencido sem justificativa")
    return problems


# ----------------------------------------------------------------- construção
def build_plan(
    conn: sqlite3.Connection, settings: Settings, clock: Clock, day: date, llm=None, force: bool = False
) -> Plan:
    existing = load_plan(conn, day)
    if existing and not force:
        return existing
    minutes = available_minutes(settings, day)
    commitments = collect_commitments(conn, settings, day)
    notes: list[str] = []
    if minutes <= 0:
        plan = Plan(day, 0, "fallback", [], ["sem disponibilidade configurada para este dia"], commitments)
        return save_plan(conn, plan, clock)
    if not commitments:
        plan = Plan(
            day,
            minutes,
            "fallback",
            [],
            ["nenhum prazo/avaliação no horizonte; registre compromissos ou aguarde a coleta"],
            commitments,
        )
        return save_plan(conn, plan, clock)

    course_ids = {c.course.id for c in commitments}
    mat_ctx, mat_ids = _materials_context(conn, course_ids)
    known_refs = mat_ids | {c.item.id for c in commitments}
    tasks: list[PlanTask] = []
    source = "fallback"

    if llm is not None and getattr(llm, "enabled", False):
        prompt = PLAN.format(
            today=day.isoformat(),
            minutes=minutes,
            session=settings.planner.preferred_session_minutes,
            commitments="\n".join(
                f"- [{c.item.id}] course={c.course.id} {c.course.name}: {c.item.title} | prazo "
                f"{c.due.isoformat()} ({c.days_left}d) | peso {c.item.weight or '?'} | escopo: "
                f"{(c.item.scope or 'não informado')[:160]} | prio={c.priority}"
                for c in commitments[:6]
            ),
            materials=mat_ctx,
            progress=_progress_context(conn, course_ids),
        )
        try:
            sug, cached = llm.complete(
                "plan_suggestion",
                day.isoformat(),
                prompt,
                PlanSuggestion,
                cache_extra=_context_fingerprint(commitments, mat_ids, minutes),
            )
            problems = validate_plan(
                sug, minutes, commitments, known_refs, settings.planner.min_session_minutes, day
            )
            if problems:
                log.warning("plano da IA rejeitado: %s", problems)
                notes.append("sugestão da IA rejeitada na validação: " + "; ".join(problems[:3]))
            else:
                tasks, source = sug.tasks, "llm"
                notes.extend(sug.notes)
        except LLMUnavailable as e:
            notes.append(f"IA indisponível ({e}); plano básico gerado")
        except LLMInvalidOutput as e:
            notes.append(f"IA retornou saída inválida ({e}); plano básico gerado")
    else:
        notes.append("IA desativada; plano básico gerado pelo sistema")

    if not tasks:
        tasks = fallback_tasks(commitments, minutes, settings, mat_ids, conn)
        if not tasks:
            notes.append("tempo disponível abaixo da sessão mínima")
        else:
            _, res, itens = selecionar_sessoes(commitments, minutes, settings)
            notes.append(
                f"sessões escolhidas pela Mochila 0/1: {len(res.indices)} de {len(itens)} candidatas, "
                f"{res.peso_total * GRANULARIDADE}/{minutes} min, valor {res.valor_total}"
            )
    plan = Plan(day, minutes, source, tasks, notes, commitments)
    return save_plan(conn, plan, clock)


def _context_fingerprint(commitments: list[Commitment], mat_ids: set[str], minutes: int) -> str:
    import hashlib

    raw = json.dumps(
        [[c.item.id, c.item.content_hash()[:8]] for c in commitments] + sorted(mat_ids) + [minutes],
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------- mochila 0/1
GRANULARIDADE = 5  # minutos por unidade de peso (reduz W e, portanto, o custo Θ(n·W))
DECAIMENTO = 0.6  # valor da j-ésima sessão longa do mesmo compromisso = prioridade·0,6^j
FATOR_CURTA = 0.3  # sessão curta (revisão) vale 30% da prioridade


@dataclass
class SessaoCandidata:
    commitment: Commitment
    minutes: int
    ordem: int  # 0,1,2... sessões longas; -1 = revisão curta

    @property
    def rotulo(self) -> str:
        tipo = "revisão curta" if self.ordem < 0 else f"sessão {self.ordem + 1}"
        return f"{self.commitment.course.name}: {self.commitment.item.title} ({tipo})"


def candidatas(commitments: list[Commitment], minutes: int, settings: Settings) -> list[ItemMochila]:
    """Gera os itens da mochila: até ``minutes // sessão`` sessões longas por compromisso, com
    valor decrescente, mais uma revisão curta. Pesos em blocos de ``GRANULARIDADE`` minutos."""
    session = settings.planner.preferred_session_minutes
    short = settings.planner.min_session_minutes
    itens: list[ItemMochila] = []
    max_longas = max(1, minutes // session)
    for c in commitments:
        base = 1000 * c.priority
        for j in range(max_longas):
            s = SessaoCandidata(c, session, j)
            itens.append(ItemMochila(s.rotulo, -(-session // GRANULARIDADE), round(base * DECAIMENTO**j), s))
        s = SessaoCandidata(c, short, -1)
        itens.append(ItemMochila(s.rotulo, -(-short // GRANULARIDADE), round(base * FATOR_CURTA), s))
    return itens


def selecionar_sessoes(
    commitments: list[Commitment], minutes: int, settings: Settings
) -> tuple[list[SessaoCandidata], ResultadoMochila, list[ItemMochila]]:
    """Resolve a mochila e devolve as sessões escolhidas, em ordem de prioridade."""
    itens = candidatas(commitments, minutes, settings)
    res = mochila_01(itens, minutes // GRANULARIDADE)
    escolhidas = [it.ref for it in res.escolhidos]
    escolhidas.sort(key=lambda s: (-s.commitment.priority, s.commitment.due, s.ordem < 0, s.ordem))
    return escolhidas, res, itens


def fallback_tasks(
    commitments: list[Commitment],
    minutes: int,
    settings: Settings,
    mat_ids: set[str],
    conn: sqlite3.Connection,
) -> list[PlanTask]:
    """Plano determinístico: sessões escolhidas pela Mochila 0/1 dentro do tempo disponível."""
    if minutes < settings.planner.min_session_minutes:
        return []
    escolhidas, _, _ = selecionar_sessoes(commitments, minutes, settings)
    tasks = []
    mats_by_course: dict[str, list[tuple[str, str]]] = {}
    for r in conn.execute(
        "SELECT m.item_id, m.course_id, i.title FROM materials m JOIN items i ON i.id=m.item_id "
        "WHERE m.status='downloaded'"
    ).fetchall():
        mats_by_course.setdefault(r["course_id"], []).append((r["item_id"], r["title"]))
    for sessao in escolhidas:
        c, dur = sessao.commitment, sessao.minutes
        mats = _rank_materials(mats_by_course.get(c.course.id, []), f"{c.item.title} {c.item.scope or ''}")[
            :2
        ]
        scope = c.item.scope or "conteúdo indicado na atividade"
        steps = [
            {
                "minutes": max(5, dur // 3),
                "action": f"Reler o escopo: {scope[:100]}"
                + (f" usando {mats[0][1]}" if mats else " (nenhum material processado ainda)"),
            },
            {
                "minutes": dur - max(5, dur // 3) - 5,
                "action": "Resolver exercícios do material; se não houver, formular 2 questões próprias",
            },
            {"minutes": 5, "action": "Anotar a principal dúvida e registrar progresso (`progress`)"},
        ]
        tasks.append(
            PlanTask(
                course_id=c.course.id,
                objective=f'Preparar "{c.item.title}" — {scope[:80]}',
                minutes=dur,
                why_now="; ".join(c.reasons),
                prerequisites=[],
                material_refs=[m[0] for m in mats] + [c.item.id],
                steps=[{"minutes": s["minutes"], "action": s["action"]} for s in steps],
                done_when="exercícios tentados e dúvida registrada",
                link_to_assessment="confirmed" if c.item.kind == ItemKind.ASSESSMENT else "none",
                is_suggested_exercise=not mats,
            )
        )
    return tasks


def _rank_materials(mats: list[tuple[str, str]], query: str) -> list[tuple[str, str]]:
    """Ordena materiais pela sobreposição de termos (≥4 letras) com o título/escopo do compromisso.
    Sem sobreposição, mantém a ordem original (mais recentes primeiro)."""
    import re
    import unicodedata

    def norm(t: str) -> set[str]:
        t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
        return {w for w in re.findall(r"[a-z0-9]{4,}", t)}

    q = norm(query)
    scored = [(len(q & norm(title)), -i, m) for i, m in enumerate(mats) for title in [m[1]]]
    scored.sort(reverse=True)
    return [m for _, _, m in scored]


# --------------------------------------------------------------- persistência
def save_plan(conn: sqlite3.Connection, plan: Plan, clock: Clock) -> Plan:
    with transaction(conn):
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0) v FROM plans WHERE plan_date=?", (plan.plan_date.isoformat(),)
        ).fetchone()
        plan.version = int(row["v"]) + 1
        payload = {
            "tasks": [t.model_dump() for t in plan.tasks],
            "notes": plan.notes,
            "commitments": [
                {
                    "item_id": c.item.id,
                    "course_id": c.course.id,
                    "due": c.due.isoformat(),
                    "priority": c.priority,
                    "reasons": c.reasons,
                }
                for c in plan.commitments
            ],
        }
        cur = conn.execute(
            "INSERT INTO plans(plan_date, version, source, available_minutes, json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                plan.plan_date.isoformat(),
                plan.version,
                plan.source,
                plan.available_minutes,
                json.dumps(payload, ensure_ascii=False),
                clock.now_utc_iso(),
            ),
        )
        plan.id = int(cur.lastrowid)
        for i, t in enumerate(plan.tasks, start=1):
            prio = next((c.priority for c in plan.commitments if c.course.id == t.course_id), 0.0)
            conn.execute(
                "INSERT INTO study_tasks(plan_id, course_id, seq, objective, minutes, priority, json) "
                "VALUES(?,?,?,?,?,?,?)",
                (plan.id, t.course_id, i, t.objective, t.minutes, prio, t.model_dump_json()),
            )
    return plan


def load_plan(conn: sqlite3.Connection, day: date) -> Plan | None:
    row = conn.execute(
        "SELECT * FROM plans WHERE plan_date=? ORDER BY version DESC LIMIT 1", (day.isoformat(),)
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["json"])
    repo = Repo(conn)
    courses = {c.id: c for c in repo.list_courses()}
    commitments = []
    for c in data.get("commitments", []):
        item = repo.get_item(c["item_id"])
        if item and c["course_id"] in courses:
            due = date.fromisoformat(c["due"])
            commitments.append(
                Commitment(item, courses[c["course_id"]], due, (due - day).days, c["priority"], c["reasons"])
            )
    return Plan(
        day,
        row["available_minutes"],
        row["source"],
        [PlanTask.model_validate(t) for t in data["tasks"]],
        data.get("notes", []),
        commitments,
        id=row["id"],
        version=row["version"],
    )


def plan_tasks_rows(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM study_tasks WHERE plan_id=? ORDER BY seq", (plan_id,)).fetchall()


def record_progress(
    conn: sqlite3.Connection,
    clock: Clock,
    task_id: int,
    status: str,
    minutes: int | None = None,
    difficulty: str | None = None,
    note: str | None = None,
) -> None:
    if status not in ("pending", "in_progress", "done", "skipped"):
        raise ValueError("status deve ser pending|in_progress|done|skipped")
    if difficulty and difficulty not in ("easy", "ok", "hard"):
        raise ValueError("dificuldade deve ser easy|ok|hard")
    row = conn.execute("SELECT course_id FROM study_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError(f"tarefa {task_id} não existe")
    now = clock.now_utc_iso()
    with transaction(conn):
        conn.execute(
            "UPDATE study_tasks SET status=?, difficulty=COALESCE(?,difficulty), note=COALESCE(?,note), "
            "updated_at=? WHERE id=?",
            (status, difficulty, note, now, task_id),
        )
        conn.execute(
            "INSERT INTO progress_log(task_id, course_id, status, minutes, difficulty, note, recorded_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (task_id, row["course_id"], status, minutes, difficulty, note, now),
        )


def needs_replan(conn: sqlite3.Connection, plan: Plan, settings: Settings) -> bool:
    """Replaneja se compromissos/prazos mudaram desde o plano ou disponibilidade divergiu."""
    current = collect_commitments(conn, settings, plan.plan_date)
    old = {(c.item.id, c.due.isoformat()) for c in plan.commitments}
    new = {(c.item.id, c.due.isoformat()) for c in current}
    return old != new or plan.available_minutes != available_minutes(settings, plan.plan_date)


# ------------------------------------------------------------------ render
def render_plan(plan: Plan, tz: ZoneInfo, conn: sqlite3.Connection | None = None) -> str:
    courses = {c.course.id: c.course for c in plan.commitments}
    head = [
        f"📚 Plano de {plan.plan_date.strftime('%d/%m/%Y')} — {plan.available_minutes} min disponíveis "
        f"(v{plan.version}, {'IA' if plan.source == 'llm' else 'básico'})"
    ]
    if plan.commitments:
        head.append("Compromissos considerados:")
        for c in plan.commitments[:6]:
            head.append(
                f"  • {fmt_when(c.due, tz)} {c.course.name}: {c.item.title} (prio {c.priority}; "
                f"{', '.join(c.reasons)})"
            )
    task_ids = {}
    titles: dict[str, str] = {}
    if conn is not None:
        if plan.id:
            task_ids = {r["seq"]: (r["id"], r["status"]) for r in plan_tasks_rows(conn, plan.id)}
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM items")}

    def ref(r: str) -> str:
        return titles.get(r, r.rsplit(":", 1)[-1])

    for i, t in enumerate(plan.tasks, start=1):
        cname = courses[t.course_id].name if t.course_id in courses else t.course_id
        tid, st = task_ids.get(i, (None, "pending"))
        head.append("")
        head.append(f"{cname} — sessão {i}: {t.minutes} min" + (f" [#{tid} {st}]" if tid else ""))
        head.append(f"Objetivo: {t.objective}")
        head.append(f"Por que agora: {t.why_now}")
        if t.prerequisites:
            head.append(f"Pré-requisitos: {', '.join(ref(r) for r in t.prerequisites)}")
        for s in t.steps:
            head.append(f"  {s.minutes} min: {s.action}")
        head.append(f"Concluído quando: {t.done_when}")
        link = {"confirmed": "confirmada", "inferred": "inferida", "none": "—"}[t.link_to_assessment]
        refs = ", ".join(ref(r) for r in t.material_refs) or "nenhuma"
        head.append(
            f"Fontes: {refs} | associação com avaliação: {link}"
            + (" | exercícios sugeridos (autorais)" if t.is_suggested_exercise else "")
        )
    if plan.notes:
        head.append("")
        head.append("Observações: " + " / ".join(plan.notes))
    return "\n".join(head)
