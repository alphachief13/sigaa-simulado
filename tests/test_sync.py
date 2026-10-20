from __future__ import annotations

from datetime import date

from conftest import adapter, load_fixture, write_scenario

from sigaa_sim.domain import EventType, ItemKind
from sigaa_sim.notify import schedule_reminders
from sigaa_sim.storage import Jobs, Outbox, Repo
from sigaa_sim.sync import run_sync
from sigaa_sim.telegram import FakeTelegramClient, deliver_outbox

FIS2 = "demo:default:2026.2:fis2_t01"
PROVA1 = f"{FIS2}:assessment:aval_301"


def test_baseline_creates_items_without_per_item_notifications(conn, settings, clock):
    stats = run_sync(conn, adapter("baseline"), settings, clock)
    assert stats.baseline is True
    assert stats.created == 26
    repo = Repo(conn)
    assert repo.count("courses") == 3
    # eventos de baseline registrados, mas só UMA mensagem (resumo inicial)
    assert all(e.event_type == EventType.BASELINE for e in repo.list_events())
    rows = Outbox(conn).recent()
    kinds = [r["kind"] for r in rows]
    assert kinds.count("baseline") == 1
    assert "event" not in kinds
    text = next(r["text"] for r in rows if r["kind"] == "baseline")
    assert text.startswith("✅ Seu SIGAA está conectado") and "Próximo prazo: Lista 1" in text


def test_repeat_sync_is_idempotent(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    repo = Repo(conn)
    clock.advance(hours=1)
    run_sync(conn, adapter("baseline"), settings, clock)
    # coleta parcial (uma seção falha na fixture) não gera alerta: só falha total avisa
    assert not [r for r in Outbox(conn).recent(100) if r["kind"] == "ops"]
    items, events = repo.count("items"), repo.count("change_events")
    outbox, jobs = Outbox(conn).counts(), Jobs(conn).counts()
    clock.advance(hours=6)
    stats = run_sync(conn, adapter("baseline"), settings, clock)
    assert stats.created == 0 and stats.updated == 0 and stats.notifications == 0
    assert repo.count("items") == items
    assert repo.count("change_events") == events
    assert Outbox(conn).counts() == outbox
    assert Jobs(conn).counts() == jobs
    # revisão não muda quando o conteúdo não muda
    assert repo.get_item_row(PROVA1)["revision"] == 1


def test_deadline_change_keeps_identity_and_records_old_new(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    clock.advance(days=1)
    stats = run_sync(conn, adapter("changed"), settings, clock)
    assert stats.updated >= 1
    repo = Repo(conn)
    evs = [e for e in repo.list_events(item_id=PROVA1) if e.event_type == EventType.DEADLINE_CHANGED]
    assert len(evs) == 1
    assert evs[0].old["due_date"] == "2026-10-15" and evs[0].new["due_date"] == "2026-10-22"
    assert evs[0].changed_fields == ["due_date"]
    assert repo.get_item_row(PROVA1)["revision"] == 2
    assert repo.count("items", "id LIKE ?", (f"{FIS2}:assessment:%",)) == 1
    msg = next(r for r in Outbox(conn).recent() if "Nova data da prova" in r["text"])
    assert "Antes: 15 out → agora: 22 out" in msg["text"]
    assert msg["urgent"] == 1


def test_deadline_a_b_a_b_all_detected(conn, settings, clock, fixtures_copy):
    seq = ["baseline", "changed", "baseline", "changed"]
    for sc in seq:
        run_sync(conn, adapter(sc, fixtures_copy), settings, clock)
        clock.advance(days=1)
    repo = Repo(conn)
    evs = [e for e in repo.list_events(item_id=PROVA1) if e.event_type == EventType.DEADLINE_CHANGED]
    assert [(e.old["due_date"], e.new["due_date"]) for e in evs] == [
        ("2026-10-15", "2026-10-22"),
        ("2026-10-22", "2026-10-15"),
        ("2026-10-15", "2026-10-22"),
    ]
    assert repo.get_item_row(PROVA1)["revision"] == 4
    assert len(repo.item_revisions(PROVA1)) == 4


def test_failed_and_partial_sections_do_not_remove_or_block_others(conn, settings, clock, fixtures_copy):
    run_sync(conn, adapter("baseline", fixtures_copy), settings, clock)
    repo = Repo(conn)
    before = repo.count("items", "removed_at IS NULL")
    # cenário: Física II inteira falha (login), demais seguem
    data = load_fixture(fixtures_copy, "baseline", "FIS2-T01")
    for sec in data.values():
        if isinstance(sec, dict):
            sec["status"], sec["records"], sec["error"] = "failed", [], "página de login"
    write_scenario(fixtures_copy, "fis_down", "FIS2-T01", data)
    settings.collection.removal_after_missing_collections = 1
    for _ in range(3):
        clock.advance(days=1)
        stats = run_sync(conn, adapter("fis_down", fixtures_copy), settings, clock)
    assert stats.status == "partial"
    assert stats.removed == 0
    assert repo.count("items", "removed_at IS NULL") == before
    assert repo.count("items", "course_id=? AND removed_at IS NOT NULL", (FIS2,)) == 0
    # coleta parcial não vira alerta; a disciplina afetada aparece em `status`
    assert not [r for r in Outbox(conn).recent(100) if r["kind"] == "ops"]


def test_removal_requires_consecutive_complete_collections(conn, settings, clock, fixtures_copy):
    run_sync(conn, adapter("baseline", fixtures_copy), settings, clock)
    data = load_fixture(fixtures_copy, "baseline", "FIS2-T01")
    data["assessments"] = {"status": "empty", "records": [], "evidence": "Nenhuma avaliação cadastrada"}
    write_scenario(fixtures_copy, "no_exam", "FIS2-T01", data)
    settings.collection.removal_after_missing_collections = 2
    repo = Repo(conn)
    clock.advance(days=1)
    run_sync(conn, adapter("no_exam", fixtures_copy), settings, clock)
    assert repo.get_item_row(PROVA1)["removed_at"] is None
    clock.advance(days=1)
    stats = run_sync(conn, adapter("no_exam", fixtures_copy), settings, clock)
    assert stats.removed == 1
    assert repo.get_item_row(PROVA1)["removed_at"] is not None
    assert any(e.event_type == EventType.REMOVED for e in repo.list_events(item_id=PROVA1))


def test_empty_without_evidence_is_treated_as_failure(conn, settings, clock, fixtures_copy):
    data = load_fixture(fixtures_copy, "baseline", "FIS2-T01")
    data["assessments"] = {"status": "complete", "records": []}  # suspeito: vazio sem evidência
    write_scenario(fixtures_copy, "suspicious", "FIS2-T01", data)
    stats = run_sync(conn, adapter("suspicious", fixtures_copy), settings, clock)
    rows = Repo(conn).sections_for_run(1)
    sec = next(r for r in rows if r["course_id"] == FIS2 and r["section"] == "assessments")
    assert sec["status"] == "failed" and "sem evidência" in sec["error"]
    assert stats.sections_failed >= 1


def test_expired_session_pauses_and_alerts_once(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    for _ in range(2):
        clock.advance(days=1)
        stats = run_sync(conn, adapter("expired"), settings, clock)
    assert stats.status == "failed" and stats.auth == "needs_interaction"
    assert Repo(conn).count("items") == 26  # nada apagado
    ops = [r for r in Outbox(conn).recent(100) if "Sessão do SIGAA expirou" in r["text"]]
    assert len(ops) == 2  # um por dia, não um por tentativa
    assert "simulador sessao --renovar" in ops[0]["text"]


def test_telegram_failure_preserves_outbox_and_resumes(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    client = FakeTelegramClient()
    client.fail_next = 1
    st = deliver_outbox(conn, client, clock, settings)
    assert st["failed"] == 1 and st["sent"] == 0
    row = Outbox(conn).recent()[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and row["last_error"]
    # "reinicia": nova execução após o backoff
    clock.advance(hours=3)
    st = deliver_outbox(conn, FakeTelegramClient(), clock, settings)
    assert st["sent"] == 1
    row = Outbox(conn).recent()[0]
    assert row["status"] == "sent" and row["message_id"] == "fake-1"


def test_reminders_dedupe_and_follow_deadline_changes(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    # 08/10 é a Lista 1; 7 dias antes = 01/10
    clock.set(clock.now().replace(month=10, day=1))
    n1 = schedule_reminders(conn, settings, clock)
    n2 = schedule_reminders(conn, settings, clock)
    assert n1 >= 1 and n2 == 0
    rems = [r for r in Outbox(conn).recent(100) if r["kind"] == "reminder"]
    assert any("Entrega em 7 dias" in r["text"] and "Lista 1" in r["text"] for r in rems)
    # prazo da Prova 1 muda para 22/10 -> em 15/10 haverá novo lembrete (7 dias) apesar do antigo
    run_sync(conn, adapter("changed"), settings, clock)
    clock.set(clock.now().replace(day=15))
    schedule_reminders(conn, settings, clock)
    prova = [r for r in Outbox(conn).recent(100) if r["kind"] == "reminder" and "Prova 1" in r["text"]]
    assert any("22 out" in r["text"] for r in prova)


def test_dates_without_time_are_not_given_a_time(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    repo = Repo(conn)
    prova = repo.get_item(PROVA1)
    assert prova.due_date == date(2026, 10, 15) and prova.due_at is None
    lista = repo.get_item(f"{FIS2}:activity:tar_201")
    assert lista.due_at is not None and lista.due_at.hour == 23 and lista.due_at.tzinfo is not None
    assert lista.due_date is None
    trabalho = repo.get_item("demo:default:2026.2:prog2_t01:activity:tar_3201")
    assert trabalho.due_date is None and trabalho.due_at is None
    assert trabalho.extra["deadline_ambiguous"] and trabalho.extra["deadline_text"] == "na próxima aula"
    assert trabalho.kind == ItemKind.ACTIVITY
