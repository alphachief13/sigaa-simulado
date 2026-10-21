"""Subcomandos adicionais: plan, progress, search, telegram-poll, web."""

from __future__ import annotations

import sys
from datetime import date

from sigaa_sim.config import ConfigError, Settings
from sigaa_sim.sigaa import make_clock


def _open(settings: Settings):
    from sigaa_sim.cli.main import _open as open_db

    return open_db(settings)


def cmd_plan(args, settings: Settings) -> int:
    from sigaa_sim.llm import make_llm
    from sigaa_sim.planner import build_plan, load_plan, needs_replan, render_plan

    conn = _open(settings)
    clock = make_clock(settings)
    day = date.fromisoformat(args.date) if args.date else clock.today()
    llm = make_llm(settings, conn, clock) if not args.no_ai else None
    existing = load_plan(conn, day)
    force = args.new or (existing is not None and needs_replan(conn, existing, settings))
    if force and existing is not None and not args.new:
        print("(compromissos ou disponibilidade mudaram; replanejando)")
    plan = build_plan(conn, settings, clock, day, llm=llm, force=force)
    print(render_plan(plan, settings.tz, conn))
    conn.close()
    return 0


def cmd_progress(args, settings: Settings) -> int:
    from sigaa_sim.planner import load_plan, plan_tasks_rows, record_progress

    conn = _open(settings)
    clock = make_clock(settings)
    if args.task is None:
        plan = load_plan(conn, date.fromisoformat(args.date) if args.date else clock.today())
        if not plan or not plan.id:
            print("nenhum plano para a data; gere um (Gerar plano, no painel).")
            return 1
        for r in plan_tasks_rows(conn, plan.id):
            print(
                f"#{r['id']} [{r['status']:<11}] {r['minutes']:>3} min  {r['objective'][:70]}"
                + (f"  (dificuldade: {r['difficulty']})" if r["difficulty"] else "")
            )
        return 0
    try:
        record_progress(conn, clock, args.task, args.status, args.minutes, args.difficulty, args.note)
    except ValueError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    print(f"tarefa #{args.task}: {args.status}" + (f" ({args.difficulty})" if args.difficulty else ""))
    conn.close()
    return 0


def cmd_search(args, settings: Settings) -> int:
    from sigaa_sim.knowledge import Knowledge

    conn = _open(settings)
    kb = Knowledge(conn, settings)
    for r in kb.search(" ".join(args.terms), course_id=args.course, limit=args.limit):
        print(f"[{r['title']} — {r['ref']}] {r['text'][:200].replace(chr(10), ' ')}…")
    conn.close()
    return 0


def cmd_telegram_poll(args, settings: Settings) -> int:
    settings.require_telegram()
    if settings.telegram.mode != "http":
        raise ConfigError("telegram-poll exige [telegram].mode = 'http'")
    if not settings.secrets.telegram_user_id:
        raise ConfigError("TELEGRAM_USER_ID ausente no .env (necessário para autorizar comandos)")
    from sigaa_sim.telegram.commands import poll_loop

    conn = _open(settings)
    try:
        return poll_loop(conn, settings, make_clock(settings), once=args.once)
    finally:
        conn.close()


def cmd_web(args, settings: Settings) -> int:
    from sigaa_sim.web.server import serve

    return serve(
        settings, host=args.host, port=args.port, insecure=args.insecure, browser=not args.no_browser
    )


def register(sub) -> None:
    s = sub.add_parser("plan", help="cria ou consulta o plano de estudo de uma data")
    s.add_argument("--date", help="AAAA-MM-DD (padrão: hoje)")
    s.add_argument("--new", action="store_true", help="força nova versão do plano")
    s.add_argument("--no-ai", action="store_true", help="plano básico sem IA")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("progress", help="lista tarefas do plano ou registra progresso")
    s.add_argument("--date", help="AAAA-MM-DD do plano (padrão: hoje)")
    s.add_argument("--task", type=int, help="ID da tarefa (#N no plano)")
    s.add_argument("--status", choices=["pending", "in_progress", "done", "skipped"], default="done")
    s.add_argument("--minutes", type=int)
    s.add_argument("--difficulty", choices=["easy", "ok", "hard"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_progress)

    s = sub.add_parser("search", help="busca textual nos materiais extraídos")
    s.add_argument("terms", nargs="+")
    s.add_argument("--course")
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("telegram-poll", help="atende comandos do Telegram por long polling")
    s.add_argument("--once", action="store_true", help="processa um lote e sai")
    s.set_defaults(fn=cmd_telegram_poll)

    s = sub.add_parser(
        "web", help="painel local: o comando principal (gera, coleta, avança o tempo, planeja)"
    )
    s.add_argument("--host", default="127.0.0.1", help="endereço (padrão: 127.0.0.1)")
    s.add_argument("--port", type=int, default=8765, help="porta (padrão: 8765)")
    s.add_argument(
        "--insecure",
        action="store_true",
        help="permite escutar fora do loopback sem WEB_TOKEN (não recomendado)",
    )
    s.add_argument("--no-browser", action="store_true", help="não abre o navegador automaticamente")
    s.set_defaults(fn=cmd_web)
