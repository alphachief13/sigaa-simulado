"""Biblioteca de mensagens (templates por intenção). Cada função devolve uma ``MessageView``.

Regras: título curto, blocos de até três linhas por compromisso, destaque só em título e
valor decisivo, sem IDs internos, sem cortes no meio de frase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from sigaa_sim.domain import Course, EventType, Item, ItemKind
from sigaa_sim.telegram.views import (
    Block,
    Button,
    MessageView,
    display_name,
    fmt_day,
    fmt_full,
    relative_days,
    sentence,
)

PAGE_SIZE = 3


@dataclass
class Agenda:
    """Compromisso normalizado para exibição."""

    item: Item
    course: Course
    due: date
    kind_label: str  # entrega | prova

    @classmethod
    def from_item(cls, item: Item, course: Course, tz: ZoneInfo) -> Agenda | None:
        due = item.due_at.astimezone(tz).date() if item.due_at else item.due_date
        if due is None:
            return None
        return cls(item, course, due, "prova" if item.kind == ItemKind.ASSESSMENT else "entrega")


def _name(course: Course, names: dict[str, str] | None) -> str:
    return display_name(course.name, names)


def _section(kind: ItemKind) -> str:
    return {
        ItemKind.ANNOUNCEMENT: "Notícias",
        ItemKind.ACTIVITY: "Tarefas",
        ItemKind.ASSESSMENT: "Avaliações",
        ItemKind.GRADE: "Notas",
        ItemKind.CONTENT: "Conteúdo",
        ItemKind.MATERIAL: "Arquivos",
    }[kind]


def source(item: Item, course: Course, names=None) -> str:
    return f"Fonte: SIGAA · {_name(course, names)} · {_section(item.kind)}"


# ------------------------------------------------------------ A. conexão
def first_connection(
    n_courses: int, nxt: Agenda | None, tz: ZoneInfo, today: date, names=None, partial: bool = False
) -> MessageView:
    blocks = [
        Block(
            [
                f"Encontrei suas {n_courses} disciplinas."
                if not partial
                else f"Encontrei {n_courses} disciplinas; uma parte dos dados não pôde ser lida ainda."
            ]
        )
    ]
    if nxt:
        when = fmt_day(nxt.item.due_at or nxt.item.due_date, tz, today)
        blocks.append(
            Block(
                [f"Próximo prazo: {nxt.item.title}", f"{_name(nxt.course, names)} · {when}"],
                bold=[nxt.item.title],
            )
        )
    blocks.append(Block(["Vou acompanhar as novidades por aqui.", "Você também pode me fazer perguntas."]))
    return MessageView(
        title="Seu SIGAA está conectado",
        emoji="✅",
        blocks=blocks,
        buttons=[Button("Ver prazos", "pz:1"), Button("Como usar", "help")],
    )


# ------------------------------------------------------------ B/C. prazos
def deadlines_page(
    agenda: list[Agenda],
    page: int,
    tz: ZoneInfo,
    today: date,
    horizon: int,
    names=None,
    updated_at: str | None = None,
) -> MessageView:
    total = len(agenda)
    if total == 0:
        return no_deadlines(horizon, updated_at, tz)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    chunk = agenda[start : start + PAGE_SIZE]
    blocks = []
    for a in chunk:
        when = fmt_day(a.item.due_at or a.item.due_date, tz, today)
        blocks.append(
            Block(
                [
                    f"{when} · {relative_days(a.due, today)}",
                    f"{_name(a.course, names)} · {a.kind_label}",
                    a.item.title,
                ],
                bold=[when, a.item.title],
            )
        )
    end = start + len(chunk)
    footer = f"Mostrando {start + 1}–{end} de {total} · fonte: SIGAA"
    buttons = []
    if end < total:
        rest = total - end
        buttons.append(Button(f"Ver os outros {rest}" if rest > 1 else "Ver o outro", f"pz:{page + 1}"))
    if page > 1:
        buttons.append(Button("Voltar aos próximos", "pz:1"))
    return MessageView(
        title="Próximos prazos",
        emoji="📅",
        subtitle=f"{total} encontrado{'s' if total != 1 else ''} · próximos {horizon} dias",
        blocks=blocks,
        footer=footer,
        buttons=buttons,
    )


def no_deadlines(horizon: int, updated_at: str | None, tz: ZoneInfo) -> MessageView:
    upd = fmt_full(updated_at, tz) if updated_at else "nenhuma coleta concluída"
    return MessageView(
        title=f"Nenhum prazo nos próximos {horizon} dias",
        blocks=[Block(["Considerando os dados consultados."]), Block([f"Última atualização: {upd}."])],
    )


# ------------------------------------------------------------ D. pergunta factual
def factual_deadline(a: Agenda, tz: ZoneInfo, today: date, names=None) -> MessageView:
    day = fmt_day(a.due, tz, today, weekday=True)
    when = day + (f", até {a.item.due_at.astimezone(tz):%Hh%M}" if a.item.due_at else "")
    what = a.item.title if a.kind_label == "prova" else f"entrega de {a.item.title}"
    blocks = [Block([f"Sua {what} de {_name(a.course, names)} é em", f"{when}."], bold=[when])]
    if a.item.due_at is None and a.kind_label == "prova":
        blocks.append(Block(["O horário não está informado no registro consultado."]))
    return MessageView(
        title=f"{_name(a.course, names)} · {a.item.title}",
        blocks=blocks,
        footer=source(a.item, a.course, names),
        buttons=[Button("Ver fonte", f"src:{a.item.id[-40:]}")],
    )


# ------------------------------------------------------------ E/F/G/H/I. alertas
def deadline_changed(
    item: Item, course: Course, old, new, tz: ZoneInfo, today: date, names=None
) -> MessageView:
    kind = "prova" if item.kind == ItemKind.ASSESSMENT else "entrega"
    new_s = fmt_day(new, tz, today, weekday=True)
    blocks = []
    if old:
        blocks.append(Block([f"Antes: {fmt_day(old, tz, today)} → agora: {new_s}"], bold=[new_s]))
    else:
        blocks.append(Block([f"A {kind} será em {new_s}."], bold=[new_s]))
    return MessageView(
        title=f"Nova data da {kind}",
        emoji="📅",
        subtitle=f"{_name(course, names)} · {item.title}",
        blocks=blocks,
        footer=source(item, course, names),
        buttons=[Button("Ver fonte", f"src:{item.id[-40:]}")],
    )


def reminder(a: Agenda, tz: ZoneInfo, today: date, names=None) -> MessageView:
    rel = relative_days(a.due, today)
    title = f"{'Prova' if a.kind_label == 'prova' else 'Entrega'} {rel}"
    when = fmt_day(a.item.due_at or a.item.due_date, tz, today)
    if a.item.due_at is not None:
        when = when.replace(" · ", ", até ")
    blocks = [Block([a.item.title, when], bold=[a.item.title])]
    if a.kind_label == "entrega":
        blocks.append(Block(["Se ainda não entregou, confira o enunciado", "e o que falta concluir."]))
    elif a.item.scope:
        blocks.append(Block([f"Conteúdo indicado: {sentence(a.item.scope, 160)}"]))
    return MessageView(
        title=title,
        emoji="⏰",
        subtitle=_name(a.course, names),
        blocks=blocks,
        buttons=[Button("Ver fonte", f"src:{a.item.id[-40:]}")],
    )


def date_to_confirm(
    item: Item,
    course: Course,
    quote: str,
    published,
    tz: ZoneInfo,
    today: date,
    what: str = "prova",
    names=None,
) -> MessageView:
    pub_d = published.astimezone(tz).date() if hasattr(published, "astimezone") else published
    pub = fmt_day(pub_d, tz, today) if pub_d else "data não informada"
    blocks = [
        Block([f"No aviso de {pub}, o professor escreveu", f"que a {what} ficou para “{quote}”."]),
        Block(["O texto não informa o dia exato."]),
    ]
    return MessageView(
        title=f"Data da {what} a confirmar",
        emoji="⚠️",
        subtitle=_name(course, names),
        blocks=blocks,
        footer=f"Fonte: aviso “{item.title}”",
        buttons=[Button("Ver aviso", f"src:{item.id[-40:]}")],
    )


def announcement_alert(
    item: Item,
    course: Course,
    summary: str,
    when_s: str | None,
    tz: ZoneInfo,
    today: date,
    names=None,
    title: str = "Aviso do professor",
) -> MessageView:
    blocks = [Block([sentence(summary, 260)] + ([when_s] if when_s else []), bold=[when_s] if when_s else [])]
    return MessageView(
        title=title,
        emoji="📢",
        subtitle=_name(course, names),
        blocks=blocks,
        footer=f"Fonte: aviso “{item.title}”",
        buttons=[Button("Ver aviso", f"src:{item.id[-40:]}")],
    )


def grade_published(item: Item, course: Course, old_value: str | None, names=None) -> MessageView:
    val = item.grade_value or "—"
    if item.grade_max:
        val += f" / {item.grade_max}"
    if old_value:
        blocks = [Block([f"{old_value} → {val}"], bold=[val])]
        title = "Nota atualizada"
    else:
        blocks = [Block([f"Sua nota: {val}"], bold=[val])]
        title = "Nota publicada"
    return MessageView(
        title=title,
        emoji="📝",
        subtitle=f"{_name(course, names)} · {item.title}",
        blocks=blocks,
        footer="Fonte: SIGAA · Notas",
        buttons=[Button("Ver notas", "notas")],
    )


def material_available(item: Item, course: Course, names=None, replaced: bool = False) -> MessageView:
    topic = item.extra.get("topic")
    line = (
        "O professor substituiu o arquivo " if replaced else "O professor disponibilizou "
    ) + f"“{item.title}”."
    blocks = [Block([line] + ([f"Tópico: {topic}"] if topic else []))]
    return MessageView(
        title="Material disponível" if not replaced else "Material atualizado",
        emoji="📎",
        subtitle=_name(course, names),
        blocks=blocks,
        footer=source(item, course, names),
        buttons=[Button("Ver materiais", f"mat:{course.id[-40:]}")],
    )


# ------------------------------------------------------------ J. resumo
def digest(
    groups: dict[str, list[str]],
    names_by_course: dict[str, str],
    more: int = 0,
    since_label: str = "Desde a última atualização",
) -> MessageView:
    blocks = [
        Block([names_by_course[cid]] + lines[:3], bold=[names_by_course[cid]])
        for cid, lines in list(groups.items())[:3]
    ]
    footer = f"{more} outras novidades" if more else None
    return MessageView(
        title="Novidades das disciplinas",
        emoji="📬",
        subtitle=since_label,
        blocks=blocks,
        footer=footer,
        buttons=[Button("Ver detalhes", "resumo:full")],
    )


EVENT_VERB = {
    EventType.CREATED: {
        "material": "novo arquivo",
        "grade": "nota publicada",
        "announcement": "aviso",
        "content": "novo tópico de aula",
        "activity": "nova tarefa",
        "assessment": "nova avaliação",
    },
    EventType.UPDATED: {
        "material": "arquivo alterado",
        "grade": "nota alterada",
        "announcement": "aviso editado",
        "content": "tópico atualizado",
        "activity": "tarefa alterada",
        "assessment": "avaliação alterada",
    },
    EventType.MATERIAL_REPLACED: {"material": "arquivo substituído"},
    EventType.GRADE_CHANGED: {"grade": "nota alterada"},
    EventType.DEADLINE_CHANGED: {"activity": "prazo alterado", "assessment": "data alterada"},
    EventType.REMOVED: {},
}


def digest_line(ev_type: EventType, item: Item, tz: ZoneInfo, today: date, extra: str | None = None) -> str:
    verb = EVENT_VERB.get(ev_type, {}).get(str(item.kind), str(ev_type))
    text = f"{verb}: {item.title}"
    if item.kind == ItemKind.GRADE and item.grade_value:
        text = f"{verb}: {item.title} — {item.grade_value}" + (
            f" / {item.grade_max}" if item.grade_max else ""
        )
    if item.kind in (ItemKind.ACTIVITY, ItemKind.ASSESSMENT) and (item.due_at or item.due_date):
        text += f" ({fmt_day(item.due_at or item.due_date, tz, today)})"
    if extra:
        text += f" — {sentence(extra, 120)}"
    return text


# ------------------------------------------------------------ K. estudo
def study_step(
    course: Course,
    minutes: int,
    objective: str,
    steps: list[tuple[int, str]],
    done_when: str,
    reason: str | None,
    names=None,
    has_full_plan: bool = True,
    task_id: int | None = None,
) -> MessageView:
    blocks = [Block([f"Sugestão: {sentence(objective, 160)}"] + ([sentence(reason, 120)] if reason else []))]
    if steps:
        blocks.append(
            Block([f"{i}. {m} min — {sentence(a, 110)}" for i, (m, a) in enumerate(steps[:3], start=1)])
        )
    blocks.append(Block([f"Ao terminar: {sentence(done_when, 140)}"]))
    buttons = []
    if task_id is not None:
        buttons.append(Button("Concluí a sessão", f"done:{task_id}"))
    if has_full_plan:
        buttons.append(Button("Ver plano completo", "plan:full"))
    return MessageView(
        title="Um passo para hoje",
        emoji="🎯",
        subtitle=f"{_name(course, names)} · {minutes} min",
        blocks=blocks,
        buttons=buttons,
    )


def no_study(reason: str) -> MessageView:
    return MessageView(
        title="Sem sugestão de estudo por enquanto",
        blocks=[Block([reason])],
        buttons=[Button("Ver prazos", "pz:1")],
    )


# ------------------------------------------------------------ L/M. conteúdo
def content_answer(answer: str, sources: list[str], gaps: list[str]) -> MessageView:
    paras = [p.strip() for p in answer.split("\n") if p.strip()] or [answer.strip()]
    blocks = [Block([paras[0]])]
    if len(paras) > 1:
        blocks.append(Block(paras[1:7]))
    if gaps:
        blocks.append(Block(["Não encontrei nos materiais: " + "; ".join(gaps[:2]) + "."]))
    footer = ("Fonte: " + " · ".join(sources[:2])) if sources else None
    buttons = [Button("Ver todas as fontes", "srcs")] if len(sources) > 2 else []
    return MessageView(title="Resposta", blocks=blocks, footer=footer, buttons=buttons)


def not_found(course: Course | None, names=None) -> MessageView:
    who = f" de {_name(course, names)}" if course else ""
    return MessageView(
        title="Ainda não encontrei isso nos materiais",
        blocks=[
            Block(["Procurei nos arquivos, avisos e prazos que consegui consultar."]),
            Block([f"Posso mostrar os avisos e arquivos disponíveis{who}."]),
        ],
        buttons=[
            Button("Ver avisos", f"av:{course.id[-40:] if course else 'all'}"),
            Button("Ver materiais", f"mat:{course.id[-40:] if course else 'all'}"),
        ],
    )


# ------------------------------------------------------------ N/O/P. sistema
def sync_failed(last_ok: str | None, tz: ZoneInfo, detail: str | None = None) -> MessageView:
    blocks = [Block(["Não consegui atualizar o SIGAA agora." + (f" {detail}" if detail else "")])]
    blocks.append(
        Block(
            [
                "Os dados disponíveis são da última consulta:",
                fmt_full(last_ok, tz) if last_ok else "ainda não houve coleta concluída.",
            ]
        )
    )
    return MessageView(title="Atualização do SIGAA falhou", blocks=blocks)


def session_expired() -> MessageView:
    fix = (
        "Para renovar a sessão simulada, use Controle → Renovar sessão no painel"
        " (ou `sigaa-sim simulador sessao --renovar`)."
    )
    return MessageView(title="Sessão do SIGAA expirou", blocks=[Block([fix])])


def help_short() -> MessageView:
    return MessageView(
        title="Posso te ajudar com prazos, materiais e estudo.",
        blocks=[
            Block(
                [
                    "Experimente perguntar:",
                    "• Quando é minha prova de Física II?",
                    "• Tem alguma novidade nas disciplinas?",
                    "• O que eu estudo hoje?",
                ]
            ),
            Block(["Atalhos: /prazos · /hoje · /notas"]),
        ],
        buttons=[Button("Todos os comandos", "help:all")],
    )


def help_full() -> MessageView:
    return MessageView(
        title="Comandos",
        blocks=[
            Block(
                [
                    "/prazos — entregas e provas",
                    "/hoje — um passo de estudo para hoje",
                    "/notas — notas lançadas",
                    "/avisos [disciplina] — últimos avisos",
                    "/materiais [disciplina] — arquivos",
                    "/buscar termo — procura nos materiais",
                    "/resumo — novidades recentes",
                    "/progresso — sessões de estudo",
                    "/disciplinas — turmas",
                    "/status — saúde do agente",
                ]
            ),
            Block(["Texto livre também funciona: é tratado como pergunta."]),
        ],
    )


def simple(title: str, *lines: str, buttons: list[Button] | None = None) -> MessageView:
    return MessageView(title=title, blocks=[Block(list(lines))] if lines else [], buttons=buttons or [])
