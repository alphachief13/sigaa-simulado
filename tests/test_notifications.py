from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from conftest import adapter

from sigaa_sim import pipeline
from sigaa_sim.domain import Course, Item, ItemKind
from sigaa_sim.llm.fake import FakeGroqClient
from sigaa_sim.llm.jobs import alert_decision
from sigaa_sim.llm.schemas import AnnouncementInterpretation
from sigaa_sim.notify import build_digest, enqueue_digest
from sigaa_sim.storage import Jobs, Outbox, Repo
from sigaa_sim.sync import run_sync
from sigaa_sim.telegram import templates as T
from sigaa_sim.telegram.client import FakeTelegramClient, deliver_outbox
from sigaa_sim.telegram.commands import Ctx, factual_date_question, handle, process_update
from sigaa_sim.telegram.templates import Agenda
from sigaa_sim.telegram.views import (
    Block,
    MessageView,
    display_name,
    fmt_day,
    keyboard,
    relative_days,
    render_html,
    render_plain,
    sentence,
)

TZ = ZoneInfo("America/Fortaleza")
TODAY = date(2026, 9, 30)


def _course(n, name):
    return Course(id=f"ufrn:default:2026.2:{n}", source_id=str(n), term="2026.2", code=str(n), name=name)


def _agenda():
    specs = [
        (
            "Cálculo III",
            "Relatório de Raízes",
            ItemKind.ACTIVITY,
            datetime(2026, 10, 6, 23, 59, tzinfo=TZ),
            None,
        ),
        (
            "Física II",
            "1ª Avaliação",
            ItemKind.ASSESSMENT,
            None,
            date(2026, 10, 7),
        ),
        ("Programação Avançada", "2ª Avaliação", ItemKind.ASSESSMENT, None, date(2026, 10, 14)),
        ("Álgebra Linear", "2ª Avaliação", ItemKind.ASSESSMENT, None, date(2026, 10, 23)),
        ("Estruturas de Dados", "2ª Avaliação", ItemKind.ASSESSMENT, None, date(2026, 10, 29)),
    ]
    out = []
    for i, (cname, title, kind, due_at, due_date) in enumerate(specs):
        c = _course(i, cname)
        it = Item(
            id=f"{c.id}:{kind}:{i}", course_id=c.id, kind=kind, title=title, due_at=due_at, due_date=due_date
        )
        out.append(Agenda.from_item(it, c, TZ))
    return out


# ------------------------------------------------------------------ §3 leitura
def test_display_names_and_dates():
    longo = "INTRODUÇÃO À FÍSICA CLÁSSICA II"
    assert display_name(longo, {longo.lower(): "Física II"}) == "Física II"
    assert display_name("Cálculo III") == "Cálculo III"
    assert display_name("DISCIPLINA DESCONHECIDA") == "DISCIPLINA DESCONHECIDA"  # sem title()
    assert display_name("X", {"x": "Y"}) == "Y"
    assert fmt_day(datetime(2026, 10, 6, 23, 59, tzinfo=TZ), TZ, TODAY) == "06 out · 23h59"
    assert fmt_day(date(2026, 10, 7), TZ, TODAY, weekday=True) == "07 out, quarta-feira"
    assert fmt_day(date(2027, 1, 5), TZ, TODAY) == "05 jan 2027"
    # às 00h39 de 30/09 em Fortaleza, 06/10 continua "em 6 dias" (diferença de datas locais)
    assert relative_days(date(2026, 10, 6), TODAY) == "em 6 dias"
    assert relative_days(date(2026, 10, 7), TODAY) == "em 7 dias"
    assert relative_days(TODAY, TODAY) == "hoje"


def test_deadlines_pagination_two_pages_three_and_two():
    agenda = _agenda()
    p1 = T.deadlines_page(agenda, 1, TZ, TODAY, 30)
    p2 = T.deadlines_page(agenda, 2, TZ, TODAY, 30)
    text1, text2 = render_plain(p1), render_plain(p2)
    assert text1.startswith("📅 Próximos prazos\n5 encontrados · próximos 30 dias")
    assert "06 out · 23h59 · em 6 dias\nCálculo III · entrega\nRelatório de Raízes" in text1
    assert "07 out · em 7 dias\nFísica II · prova\n1ª Avaliação" in text1
    assert "Mostrando 1–3 de 5" in text1 and "Álgebra" not in text1
    assert [b.label for b in p1.buttons] == ["Ver os outros 2"] and p1.buttons[0].callback == "pz:2"
    assert "Mostrando 4–5 de 5" in text2 and "Estruturas de Dados · prova" in text2
    assert [b.label for b in p2.buttons] == ["Voltar aos próximos"]
    assert "•" not in text1  # blocos, sem marcadores
    html = render_html(p1)
    assert "<b>06 out · 23h59</b>" in html and "<b>Relatório de Raízes</b>" in html
    assert "<b>em 6 dias" not in html  # contagem relativa em peso normal


def test_render_escapes_once_and_keeps_literal_tags():
    v = MessageView(title="Aviso <b>prova</b> & tal", blocks=[Block(["a < b & c > d", "x"], bold=["c > d"])])
    html = render_html(v)
    assert html.startswith("<b>Aviso &lt;b&gt;prova&lt;/b&gt; &amp; tal</b>")
    assert "a &lt; b &amp; <b>c &gt; d</b>" in html
    assert render_plain(v) == "Aviso <b>prova</b> & tal\n\na < b & c > d\nx"
    kb = keyboard(
        MessageView(title="t", buttons=[T.Button("A", "a"), T.Button("B", "b"), T.Button("C", "c")])
    )
    assert [len(r) for r in kb["inline_keyboard"]] == [2, 1]


def test_sentence_never_cuts_mid_reasoning():
    long = "Primeira frase completa. Segunda frase que também termina. Terceira frase muito longa " * 3
    s = sentence(long, 80)
    assert s.endswith(".") and len(s) <= 81
    assert sentence("palavra " * 30, 40).endswith("…") and not sentence("palavra " * 30, 40).endswith(" …")


# ------------------------------------------------------------------ §5 datas e relevância
def _interp(**kw):
    base = dict(
        kind="exam",
        summary="s",
        date_mentions=[],
        structured_date=None,
        needs_confirmation=False,
        action="a",
        urgency="high",
        facts=[],
        inferences=[],
    )
    base.update(kw)
    return AnnouncementInterpretation.model_validate(base)


def _ann(published: date, body="x"):
    return Item(
        id="c:announcement:1",
        course_id="c",
        kind=ItemKind.ANNOUNCEMENT,
        title="t",
        body=body,
        published_at=datetime(published.year, published.month, published.day, 9, tzinfo=TZ),
    )


def test_temporal_rules_for_announcement_alerts():
    # prova de 16/09 (publicada 15/09) e entrega de 17/09: histórico, nunca alerta em 30/09
    assert (
        alert_decision(
            _interp(structured_date="2026-09-16", date_mentions=["amanhã"]), _ann(date(2026, 9, 15)), TODAY
        )
        is None
    )
    assert (
        alert_decision(_interp(kind="deadline", structured_date="2026-09-17"), _ann(date(2026, 9, 10)), TODAY)
        is None
    )
    # data futura explícita e comprovada → alerta
    assert (
        alert_decision(_interp(structured_date="2026-10-07"), _ann(date(2026, 9, 25)), TODAY) == "confirmed"
    )
    # "próxima semana" publicado há 5 dias: sem data exata inventada; recente (≤3 dias) pede confirmação
    assert (
        alert_decision(
            _interp(needs_confirmation=True, date_mentions=["a próxima semana"]),
            _ann(date(2026, 9, 25)),
            TODAY,
        )
        is None
    )
    assert (
        alert_decision(
            _interp(needs_confirmation=True, date_mentions=["a próxima semana"]),
            _ann(date(2026, 9, 28)),
            TODAY,
        )
        == "confirm"
    )
    # aviso de material não tem data a resolver: nunca "data ambígua"
    assert alert_decision(_interp(kind="material", needs_confirmation=True), _ann(TODAY), TODAY) is None
    # "hoje" em aviso antigo não vira hoje pela execução atual
    assert (
        alert_decision(
            _interp(kind="schedule_change", date_mentions=["hoje"], needs_confirmation=True),
            _ann(date(2026, 9, 25)),
            TODAY,
        )
        is None
    )


def test_date_to_confirm_template_quotes_publication_not_today():
    c = _course(1, "Álgebra Linear")
    it = _ann(date(2026, 9, 25), "A prova ficou para a próxima semana.")
    v = T.date_to_confirm(it, c, "a próxima semana", it.published_at, TZ, TODAY, what="prova")
    text = render_plain(v)
    assert text.startswith("⚠️ Data da prova a confirmar\nÁlgebra Linear")
    assert "No aviso de 25 set, o professor escreveu" in text and "“a próxima semana”" in text
    assert "O texto não informa o dia exato." in text and "ufrn:" not in text


# ------------------------------------------------------------------ §6 política de envio
def test_first_sync_sends_single_connection_message(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    rows = Outbox(conn).recent(50)
    assert [r["kind"] for r in rows] == ["baseline"]
    text = rows[0]["text"]
    # a fixture demo tem uma seção com falha: a confirmação não afirma sucesso completo
    assert text.startswith("✅ Seu SIGAA está conectado\n\nEncontrei 3 disciplinas; uma parte dos dados")
    assert "Próximo prazo:" in text and "Vou acompanhar as novidades por aqui." in text
    assert "aviso(s)" not in text  # contagens ficam em /status
    # interpretações da baseline nunca notificam
    jobs = Jobs(conn).due(clock.now_utc_iso(), kinds=["interpret_announcement"], limit=50)
    assert jobs and all(j["payload"]["notify"] is False for j in jobs)


def test_deadline_change_is_immediate_and_others_go_to_digest(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    clock.advance(days=1)
    run_sync(
        conn, adapter("changed"), settings, clock
    )  # prazo adiado, aviso novo, nota, material substituído
    rows = [r for r in Outbox(conn).recent(50) if r["kind"] == "event"]
    assert len(rows) == 1
    text = rows[0]["text"]
    assert (
        text.startswith("📅 Nova data da prova\nFísica II · Prova 1")
        and "Antes: 15 out → agora: 22 out" in text
    )
    # nota e material não dependem de data futura: entram no resumo
    view, total = build_digest(conn, settings, clock, "2000-01-01T00:00:00Z")
    digest = render_plain(view)
    assert digest.startswith("📬 Novidades das disciplinas")
    assert (
        "nota publicada: Lista 1 — 8,5 / 10" in digest
        and "arquivo substituído: Slides — Cap. 21 e 22" in digest
    )
    assert (
        "Prova 1" not in digest.split("\n\n", 1)[1] or "prazo" not in digest
    )  # alerta já enviado não reaparece
    # resumo só nos horários 08h/18h
    clock.set(clock.now().replace(hour=7, minute=0))
    assert enqueue_digest(conn, settings, clock) is False
    clock.set(clock.now().replace(hour=8, minute=5))
    assert enqueue_digest(conn, settings, clock) is True
    assert enqueue_digest(conn, settings, clock) is False  # mesmo slot não repete


def test_equivalent_announcements_yield_one_alert(conn, settings, clock):
    settings.groq.enabled = True
    run_sync(conn, adapter("baseline"), settings, clock)
    repo = Repo(conn)
    course_id = "demo:default:2026.2:fis2_t01"
    now = clock.now_utc_iso()
    body = "A prova fica adiada para a próxima semana."
    for n in (1, 2):
        it = Item(
            id=f"{course_id}:announcement:dup{n}",
            course_id=course_id,
            kind=ItemKind.ANNOUNCEMENT,
            title=f"Adiamento {n}",
            body=body,
            published_at=clock.now(),
        )
        with conn:
            repo.insert_item(it, now, None)
        Jobs(conn).enqueue(
            "interpret_announcement", f"interpret:{it.id}", {"item_id": it.id, "notify": True}, now, 10
        )
    llm = FakeGroqClient.with_db(conn, settings, clock)
    pipeline.process_jobs(pipeline.JobContext(conn=conn, settings=settings, clock=clock, llm=llm), limit=50)
    interps = [r for r in Outbox(conn).recent(50) if r["kind"] == "interpretation"]
    assert len(interps) == 1 and "Data da prova a confirmar" in interps[0]["text"]


def test_sync_failure_message_keeps_last_update(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    clock.advance(days=1)
    run_sync(conn, adapter("expired"), settings, clock)
    ops = [r for r in Outbox(conn).recent(50) if r["kind"] == "ops"]
    assert len(ops) == 1 and ops[0]["text"].startswith("Sessão do SIGAA expirou")
    assert "simulador sessao --renovar" in ops[0]["text"]  # sem credenciais automáticas no teste
    client = FakeTelegramClient()
    deliver_outbox(conn, client, clock, settings)
    assert client.views and all("ufrn:" not in t and "demo:default" not in t for t in client.sent)


# ------------------------------------------------------------------ §7 interação
def test_factual_question_answers_without_llm(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    ctx = Ctx(conn, settings, clock)
    v = factual_date_question(ctx, "Quando é minha prova de Física?")
    text = render_plain(v)
    assert "Sua Prova 1 de Física II é em\n15 out, quinta-feira." in text
    assert "O horário não está informado" in text and text.endswith("Fonte: SIGAA · Física II · Avaliações")
    assert v.buttons[0].label == "Ver fonte"
    assert factual_date_question(ctx, "o que é jacobiano?") is None
    v2 = factual_date_question(ctx, "quando é a entrega da lista de física?")
    assert "23h59" in render_plain(v2) and "horário não está informado" not in render_plain(v2)


def test_commands_render_views_and_callbacks_edit(conn, settings, clock):
    settings.secrets.telegram_user_id = settings.secrets.telegram_chat_id = "1"
    run_sync(conn, adapter("baseline"), settings, clock)
    ctx = Ctx(conn, settings, clock)
    help_text = render_plain(handle(ctx, "start", ""))
    assert (
        help_text.startswith("Posso te ajudar com prazos, materiais e estudo.") and help_text.count("/") <= 4
    )
    prazos = render_plain(handle(ctx, "prazos", ""))
    assert "Mostrando 1–3 de 4" in prazos and "Cálculo III · prova" in prazos
    client = FakeTelegramClient()

    def upd(uid, user, text=None, cb=None):
        u = {"update_id": uid}
        if cb:
            u["callback_query"] = {
                "id": "cb1",
                "from": {"id": user},
                "data": cb,
                "message": {"message_id": 77, "chat": {"id": 1}},
            }
        else:
            u["message"] = {"from": {"id": user}, "chat": {"id": 1}, "text": text}
        return u

    process_update(conn, settings, clock, upd(1, 1, "/prazos"), client)
    assert len(client.sent) == 1
    process_update(conn, settings, clock, upd(2, 1, cb="pz:2"), client)
    assert (
        len(client.sent) == 1 and client.edits and client.edits[0][0] == "77"
    )  # paginação edita, não reenvia
    assert "Mostrando 4–4 de 4" in render_plain(client.edits[0][1])
    process_update(conn, settings, clock, upd(3, 99, cb="pz:2"), client)  # não autorizado
    assert len(client.edits) == 1 and len(client.sent) == 1
    process_update(conn, settings, clock, upd(4, 1, "quando é minha prova de cálculo?"), client)
    assert "AP1 de Cálculo III é em" in client.sent[-1]
    process_update(conn, settings, clock, upd(5, 1, "/status; rm -rf /"), client)
    assert len(client.sent) == 2  # comando malformado ignorado
    for t in client.sent:
        assert "demo:default" not in t and "json" not in t.lower()
