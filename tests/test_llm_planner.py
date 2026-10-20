from __future__ import annotations

import json
from datetime import date

from conftest import adapter

from sigaa_sim import pipeline
from sigaa_sim.llm import LLMInvalidOutput, LLMUnavailable
from sigaa_sim.llm.fake import FakeGroqClient
from sigaa_sim.llm.prompts import SYSTEM_BASE
from sigaa_sim.llm.schemas import DocumentSummary, PlanSuggestion, strict_json_schema
from sigaa_sim.planner import (
    build_plan,
    collect_commitments,
    load_plan,
    needs_replan,
    priority_score,
    record_progress,
    render_plan,
    validate_plan,
)
from sigaa_sim.storage import Jobs, Outbox, Repo
from sigaa_sim.sync import run_sync
from sigaa_sim.telegram import FakeTelegramClient, deliver_outbox

FIS2 = "demo:default:2026.2:fis2_t01"


def _ctx(conn, settings, clock, llm):
    return pipeline.JobContext(
        conn=conn, settings=settings, clock=clock, adapter_factory=lambda: adapter("baseline"), llm=llm
    )


def _drain(ctx):
    tot = {"done": 0, "failed": 0, "deferred": 0}
    for _ in range(3):
        r = pipeline.process_jobs(ctx, limit=50)
        for k in tot:
            tot[k] += r[k]
        if not r["done"]:
            break
    return tot


def test_budget_exhaustion_defers_ai_but_keeps_alerts(conn, settings, clock):
    settings.groq.enabled = True
    settings.groq.max_calls_per_run = 2
    run_sync(conn, adapter("baseline"), settings, clock)
    llm = FakeGroqClient.with_db(conn, settings, clock)
    r = _drain(_ctx(conn, settings, clock, llm))
    # 2 chamadas de IA passaram, o resto foi adiado (não falhou, não se perdeu)
    calls = conn.execute("SELECT COUNT(*) c FROM llm_calls WHERE status='ok'").fetchone()["c"]
    assert calls == 2
    assert r["deferred"] >= 1 and Jobs(conn).counts().get("deferred", 0) >= 1
    # alertas factuais seguem: mudança de prazo é entregue sem IA
    clock.advance(days=1)
    run_sync(conn, adapter("changed"), settings, clock)
    client = FakeTelegramClient()
    deliver_outbox(conn, client, clock, settings)
    assert any("Nova data da prova" in t for t in client.sent)
    # jobs adiados voltam quando o orçamento libera (nova execução)
    settings.groq.max_calls_per_run = 50
    clock.advance(hours=2)
    llm2 = FakeGroqClient.with_db(conn, settings, clock)
    _drain(_ctx(conn, settings, clock, llm2))
    assert Jobs(conn).counts().get("deferred", 0) == 0


def test_daily_budget_blocks_until_tomorrow(conn, settings, clock):
    settings.groq.enabled = True
    settings.groq.max_calls_per_day = 1
    llm = FakeGroqClient.with_db(conn, settings, clock)
    llm.complete("document_summary", "h1", "texto [p.1] A", DocumentSummary)
    try:
        llm.complete("document_summary", "h2", "texto [p.1] B", DocumentSummary)
        raise AssertionError("deveria falhar")
    except LLMUnavailable as e:
        assert e.retry_seconds > 600


def test_cache_avoids_repeated_calls_and_changes_with_prompt(conn, settings, clock):
    settings.groq.enabled = True
    llm = FakeGroqClient.with_db(conn, settings, clock)
    _, c1 = llm.complete("document_summary", "hash1", "Cap. 21: Carga [p.1] texto", DocumentSummary)
    _, c2 = llm.complete("document_summary", "hash1", "Cap. 21: Carga [p.1] texto", DocumentSummary)
    _, c3 = llm.complete("document_summary", "hash1", "Cap. 21: Carga [p.1] texto DIFERENTE", DocumentSummary)
    assert (c1, c2, c3) == (False, True, False)
    assert conn.execute("SELECT COUNT(*) c FROM llm_calls").fetchone()["c"] == 2


def test_invalid_json_gets_one_fix_then_fails(conn, settings, clock):
    settings.groq.enabled = True
    llm = FakeGroqClient.with_db(conn, settings, clock)
    llm.raw_responses = ["isto não é json", '{"summary": "faltam campos"}']
    try:
        llm.complete("document_summary", "h", "x", DocumentSummary)
        raise AssertionError("deveria falhar")
    except LLMInvalidOutput:
        pass
    statuses = [r["status"] for r in conn.execute("SELECT status FROM llm_calls ORDER BY id")]
    assert statuses == ["ok", "ok", "invalid_output"]  # 2 chamadas reais + registro da falha
    # a correção funciona quando a 2ª resposta é válida
    llm.raw_responses = [
        "```json\n{bad",
        json.dumps(
            {
                "summary": "ok",
                "topics": [],
                "prerequisites": [],
                "exercises_refs": [],
                "missing_or_unclear": [],
                "confidence": "low",
            }
        ),
    ]
    obj, cached = llm.complete("document_summary", "h2", "y", DocumentSummary)
    assert obj.confidence == "low" and not cached


def test_invalid_output_job_fails_without_blocking_alerts(conn, settings, clock):
    settings.groq.enabled = True
    run_sync(conn, adapter("baseline"), settings, clock)
    llm = FakeGroqClient.with_db(conn, settings, clock)
    llm.raw_responses = ["{", "{"] * 20
    r = _drain(_ctx(conn, settings, clock, llm))
    assert r["failed"] >= 1
    assert Outbox(conn).counts().get("pending") == 1  # baseline segue na outbox intacta


def test_strict_schema_shape():
    s = strict_json_schema(PlanSuggestion)
    assert s["additionalProperties"] is False and set(s["required"]) == {"tasks", "notes"}
    task = s["$defs"]["PlanTask"]
    assert task["additionalProperties"] is False and "material_refs" in task["required"]


def test_prompt_does_not_contain_secrets(conn, settings, clock):
    settings.groq.enabled = True
    settings.secrets.groq_api_key = "gsk_SECRET123"
    settings.secrets.telegram_bot_token = "123:TOKEN-SECRETO"
    run_sync(conn, adapter("baseline"), settings, clock)
    llm = FakeGroqClient.with_db(conn, settings, clock)
    _drain(_ctx(conn, settings, clock, llm))
    plan = build_plan(conn, settings, clock, date(2026, 10, 1), llm=llm)
    joined = " ".join(p for _, p in llm.calls) + SYSTEM_BASE + render_plan(plan, settings.tz)
    assert "gsk_SECRET123" not in joined and "TOKEN-SECRETO" not in joined
    assert "SECRET" not in repr(settings.secrets)
    log = settings.logs_dir / "sigaa_sim.log"
    if log.exists():
        assert "gsk_SECRET123" not in log.read_text()


def test_priority_formula_is_monotonic(settings):
    near, _ = priority_score(settings, 1, 4.0, 2, 1.0)
    far, _ = priority_score(settings, 14, 4.0, 2, 1.0)
    heavier, _ = priority_score(settings, 14, 8.0, 2, 1.0)
    assert near > far and heavier > far


def test_plan_validation_rejects_bad_plans(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    day = date(2026, 10, 1)
    commitments = collect_commitments(conn, settings, day)
    known = {c.item.id for c in commitments}
    good = PlanSuggestion.model_validate(
        {
            "tasks": [
                {
                    "course_id": FIS2,
                    "objective": "x",
                    "minutes": 30,
                    "why_now": "y",
                    "prerequisites": [],
                    "material_refs": [commitments[0].item.id],
                    "steps": [{"minutes": 30, "action": "a"}],
                    "done_when": "z",
                    "link_to_assessment": "inferred",
                    "is_suggested_exercise": False,
                }
            ],
            "notes": [],
        }
    )
    assert validate_plan(good, 90, commitments, known, 15, day) == []
    too_long = good.model_copy(deep=True)
    too_long.tasks[0].minutes = 120
    assert any("disponíveis" in p for p in validate_plan(too_long, 90, commitments, known, 15, day))
    ghost = good.model_copy(deep=True)
    ghost.tasks[0].material_refs = ["material:inexistente"]
    assert any("inexistente" in p for p in validate_plan(ghost, 90, commitments, known, 15, day))
    wrong_course = good.model_copy(deep=True)
    wrong_course.tasks[0].course_id = "outra"
    assert any("desconhecida" in p for p in validate_plan(wrong_course, 90, commitments, known, 15, day))


def test_build_plan_falls_back_when_ai_invalid_and_respects_time(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    llm = FakeGroqClient()
    llm.raw_responses = [
        json.dumps(
            {
                "tasks": [
                    {
                        "course_id": FIS2,
                        "objective": "x",
                        "minutes": 500,
                        "why_now": "y",
                        "prerequisites": [],
                        "material_refs": ["fantasma"],
                        "steps": [],
                        "done_when": "z",
                        "link_to_assessment": "confirmed",
                        "is_suggested_exercise": False,
                    }
                ],
                "notes": [],
            }
        )
    ]
    day = date(2026, 10, 1)  # quinta: 90 min
    plan = build_plan(conn, settings, clock, day, llm=llm)
    assert plan.source == "fallback"
    assert any("rejeitada" in n for n in plan.notes)
    assert sum(t.minutes for t in plan.tasks) <= 90
    assert all(t.minutes >= settings.planner.min_session_minutes for t in plan.tasks)
    assert all(ref for t in plan.tasks for ref in t.material_refs)
    # sem IA: também gera plano básico, e diz o que falta
    plan2 = build_plan(conn, settings, clock, day, llm=None, force=True)
    assert plan2.version == 2 and any("IA desativada" in n for n in plan2.notes)
    # domingo sem disponibilidade
    plan3 = build_plan(conn, settings, clock, date(2026, 10, 4), llm=None)
    assert plan3.tasks == [] and plan3.available_minutes == 0


def test_plan_reuses_existing_and_replans_on_deadline_change(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    day = date(2026, 10, 1)
    p1 = build_plan(conn, settings, clock, day, llm=FakeGroqClient())
    p1b = build_plan(conn, settings, clock, day, llm=FakeGroqClient())
    assert p1b.id == p1.id
    assert not needs_replan(conn, p1, settings)
    run_sync(conn, adapter("changed"), settings, clock)
    assert needs_replan(conn, load_plan(conn, day), settings)
    p2 = build_plan(conn, settings, clock, day, llm=FakeGroqClient(), force=True)
    assert p2.version == 2


def test_progress_recording(conn, settings, clock):
    run_sync(conn, adapter("baseline"), settings, clock)
    plan = build_plan(conn, settings, clock, date(2026, 10, 1), llm=None)
    task_id = conn.execute("SELECT id FROM study_tasks WHERE plan_id=?", (plan.id,)).fetchone()["id"]
    record_progress(conn, clock, task_id, "done", 30, "hard", "confundi U e V")
    row = conn.execute("SELECT * FROM study_tasks WHERE id=?", (task_id,)).fetchone()
    assert row["status"] == "done" and row["difficulty"] == "hard"
    assert Repo(conn).count("progress_log") == 1
    try:
        record_progress(conn, clock, task_id, "finished")
        raise AssertionError
    except ValueError:
        pass
