"""CLI: ``sigaa-sim <comando>``."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from sigaa_sim.clock import Clock
from sigaa_sim.config import ConfigError, Settings, load_settings
from sigaa_sim.logging_setup import setup_logging
from sigaa_sim.storage import LockError, Repo, connect, get_meta, has_fts, migrate, run_lock
from sigaa_sim.storage.queues import Jobs, Outbox

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config.example.toml"
EXAMPLE_ENV = Path(__file__).resolve().parents[2] / ".env.example"


def _open(settings: Settings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = connect(settings.db_path)
    migrate(conn)
    return conn


def _clock(settings: Settings) -> Clock:
    from sigaa_sim.sigaa import make_clock

    return make_clock(settings)


# ------------------------------------------------------------------------- init
def cmd_init(args, settings: Settings) -> int:
    cfg = Path(args.config or "config.toml")
    if not cfg.exists() and EXAMPLE_CONFIG.exists():
        shutil.copy(EXAMPLE_CONFIG, cfg)
        print(f"criado {cfg} (a partir de config.example.toml)")
    else:
        print(f"{cfg} já existe; mantido.")
    env = Path(".env")
    if not env.exists() and EXAMPLE_ENV.exists():
        shutil.copy(EXAMPLE_ENV, env)
        env.chmod(0o600)
        print("criado .env (vazio; preencha se for usar Groq/Telegram)")
    settings = load_settings(cfg)
    conn = _open(settings)
    conn.close()
    print(
        f"banco pronto em {settings.db_path} (FTS5: {'sim' if has_fts(connect(settings.db_path)) else 'não'})"
    )
    return 0


# ------------------------------------------------------------------------- sync
def cmd_sync(args, settings: Settings) -> int:
    from sigaa_sim.sigaa import make_adapter
    from sigaa_sim.sync import run_sync

    conn = _open(settings)
    clock = _clock(settings)
    try:
        with run_lock(settings.data_dir / "sync.lock"):
            adapter = make_adapter(settings, scenario=getattr(args, "fixture", None))
            stats = run_sync(conn, adapter, settings, clock)
            _print_sync(stats)
            if not args.no_process:
                _process_pending(conn, settings, clock, echo=args.echo)
    except LockError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0 if stats.status != "failed" else 1


def _print_sync(stats) -> None:
    print(
        f"coleta: {stats.status}{' (baseline)' if stats.baseline else ''} em {stats.duration_s}s | "
        f"disciplinas={stats.courses} seções ok/parcial/vazia/falha="
        f"{stats.sections_ok}/{stats.sections_partial}/{stats.sections_empty}/{stats.sections_failed} | "
        f"itens={stats.items_seen} novos={stats.created} alterados={stats.updated} "
        f"removidos={stats.removed} | notificações={stats.notifications} jobs={stats.jobs}"
    )
    for e in stats.errors[:5]:
        print(f"  ! {e}")


def _process_pending(
    conn, settings: Settings, clock: Clock, echo: bool = False, job_limit: int | None = None
) -> None:
    # registra handlers (import lateral)
    import sigaa_sim.knowledge.jobs  # noqa: F401
    import sigaa_sim.llm.jobs  # noqa: F401
    from sigaa_sim import pipeline
    from sigaa_sim.notify import enqueue_digest
    from sigaa_sim.telegram import deliver_outbox, make_client

    enqueue_digest(conn, settings, clock)

    # 1) fatos primeiro: entrega da outbox nunca espera pela IA
    if settings.telegram.enabled:
        client = make_client(settings, echo=echo)
        st = deliver_outbox(conn, client, clock, settings)
        print(f"telegram: enviados={st['sent']} falhas={st['failed']} adiados={st['postponed']}")
    else:
        print("telegram: desativado (mensagens ficam na outbox)")

    # 2) trabalhos pendentes (download/extração/IA)
    from sigaa_sim.llm import make_llm
    from sigaa_sim.sigaa import make_adapter

    ctx = pipeline.JobContext(
        conn=conn,
        settings=settings,
        clock=clock,
        adapter_factory=lambda: make_adapter(settings),
        llm=make_llm(settings, conn, clock),
    )
    st = {"done": 0, "failed": 0, "deferred": 0, "skipped": 0}
    limit = job_limit or settings.knowledge.max_documents_per_run * 4
    for _ in range(3):
        r = pipeline.process_jobs(ctx, limit=limit)
        for k in st:
            st[k] += r[k]
        if not r["done"]:
            break
    print(
        f"jobs: concluídos={st['done']} falhas={st['failed']} adiados={st['deferred']} "
        f"sem_handler={st['skipped']}"
    )

    # 3) novos jobs derivados (ex.: resumos após extração) e nova rodada de entrega
    if settings.telegram.enabled:
        st2 = deliver_outbox(conn, make_client(settings, echo=echo), clock, settings)
        if st2["sent"]:
            print(f"telegram (2ª rodada): enviados={st2['sent']}")


def cmd_process_pending(args, settings: Settings) -> int:
    conn = _open(settings)
    try:
        with run_lock(settings.data_dir / "sync.lock"):
            _process_pending(conn, settings, _clock(settings), echo=args.echo, job_limit=args.limit)
    except LockError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


# ----------------------------------------------------------------------- status
def cmd_status(args, settings: Settings) -> int:
    conn = _open(settings)
    repo = Repo(conn)
    last = repo.last_run()
    print(
        f"config: {settings.config_path or '(defaults)'} | perfil sigaa: {settings.sigaa.profile} | "
        f"fuso: {settings.general.timezone}"
    )
    print(f"último sucesso: {get_meta(conn, 'last_success_at') or 'nunca'}")
    if last:
        stats = json.loads(last["stats_json"] or "{}")
        print(
            f"última execução #{last['id']}: {last['status']} em {last['started_at']} "
            f"({stats.get('duration_s', '?')}s) itens={stats.get('items_seen', '?')} "
            f"novos={stats.get('created', '?')} alterados={stats.get('updated', '?')}"
        )
        secs = repo.sections_for_run(last["id"])
        bad = [s for s in secs if s["status"] in ("failed", "partial")]
        for s in bad[:10]:
            print(f"  {s['status']:8} {s['course_id'].split(':')[-1]}/{s['section']}: {s['error'] or ''}")
    print(
        f"disciplinas: {repo.count('courses')} | itens ativos: {repo.count('items', 'removed_at IS NULL')} "
        f"| eventos: {repo.count('change_events')}"
    )
    print(f"outbox: {Outbox(conn).counts() or {}}")
    print(f"jobs: {Jobs(conn).counts() or {}} pendentes por tipo: {Jobs(conn).pending_by_kind() or {}}")
    row = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(total_tokens),0) t FROM llm_calls WHERE created_at >= date('now')"
    ).fetchone()
    print(f"IA hoje: chamadas={row['c']} tokens={row['t']} | cache de saídas: {repo.count('llm_outputs')}")
    downloaded = repo.count("materials", "status='downloaded'")
    print(
        f"materiais: {repo.count('materials')} (baixados: {downloaded}) | "
        f"documentos: {repo.count('documents')} | chunks: {repo.count('chunks')}"
    )
    if args.verbose:
        print("\núltimas mensagens da outbox:")
        for r in Outbox(conn).recent(5):
            print(
                f"  [{r['status']}] {r['kind']} {r['created_at']}\n    " + r["text"].replace("\n", "\n    ")
            )
    conn.close()
    return 0


# ----------------------------------------------------------------------- doctor
def cmd_doctor(args, settings: Settings) -> int:
    ok = True

    def check(label: str, cond: bool, hint: str = "") -> None:
        nonlocal ok
        mark = "ok " if cond else "!! "
        print(f"[{mark}] {label}" + ("" if cond or not hint else f" — {hint}"))
        ok = ok and cond

    check(f"config carregada ({settings.config_path or 'defaults'})", True)
    check(f"python {sys.version.split()[0]}", sys.version_info[:2] == (3, 12), "esperado 3.12")
    try:
        conn = _open(settings)
        check(f"banco {settings.db_path}", True)
        check("FTS5 disponível", has_fts(conn), "busca cairá para LIKE")
        conn.close()
    except Exception as e:
        check("banco", False, str(e))
    check("diretório de dados gravável", _writable(settings.data_dir))
    check(f"perfil sigaa = {settings.sigaa.profile}", settings.sigaa.profile in ("simulado", "demo"))
    if settings.sigaa.profile == "simulado":
        check(
            f"banco simulado {settings.sim_db_path}",
            settings.sim_db_path.exists(),
            "no painel: Controle → Preparar tudo (ou `sigaa-sim simulador gerar`)",
        )
    s = settings.secrets
    if settings.telegram.enabled:
        if settings.telegram.mode == "http":
            check("TELEGRAM_BOT_TOKEN", bool(s.telegram_bot_token))
            check("TELEGRAM_CHAT_ID", bool(s.telegram_chat_id))
        else:
            check("telegram em modo demo (arquivo local)", True)
    else:
        print("[ -- ] telegram desativado")
    if settings.groq.enabled:
        check("GROQ_API_KEY", bool(s.groq_api_key))
        check(f"modelo groq = {settings.groq.model}", True)
    else:
        print("[ -- ] groq desativado (alertas factuais funcionam; resumos/planos usam fallback)")
    env = Path(".env")
    if env.exists():
        mode = env.stat().st_mode & 0o777
        check(f".env com permissões restritas ({oct(mode)})", mode & 0o077 == 0, "chmod 600 .env")
    return 0 if ok else 1


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_test"
        t.write_text("x")
        t.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------------- demo
def cmd_demo(args, settings: Settings) -> int:
    """Fluxo completo com dados fictícios em um diretório temporário, sem rede."""
    import tempfile
    from datetime import datetime

    from sigaa_sim.clock import FixedClock
    from sigaa_sim.llm.fake import FakeGroqClient
    from sigaa_sim.sigaa import DemoAdapter
    from sigaa_sim.sync import run_sync

    tmp = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="sigaa_demo_"))
    settings = settings.model_copy(deep=True)
    settings.general.data_dir = tmp
    settings.sigaa.profile = "demo"
    settings.telegram.enabled = True
    settings.telegram.mode = "demo"
    settings.groq.enabled = False
    settings.ensure_dirs()
    print(f"demo em {tmp} (dados fictícios; nada é enviado)\n")
    conn = _open(settings)
    tz = settings.tz
    clock = FixedClock(tz, datetime(2026, 9, 29, 9, 0, tzinfo=tz))

    print("== 1) primeira coleta (baseline) ==")
    stats = run_sync(conn, DemoAdapter("baseline"), settings, clock)
    _print_sync(stats)
    _demo_process(conn, settings, clock, fake_llm=FakeGroqClient())

    print("\n== 2) repetição sem mudanças ==")
    clock.advance(hours=6)
    stats = run_sync(conn, DemoAdapter("baseline"), settings, clock)
    _print_sync(stats)
    _demo_process(conn, settings, clock, fake_llm=FakeGroqClient())

    print("\n== 3) fixture alterada (prazo adiado, aviso novo, nota, material substituído) ==")
    clock.advance(days=1)
    stats = run_sync(conn, DemoAdapter("changed"), settings, clock)
    _print_sync(stats)
    _demo_process(conn, settings, clock, fake_llm=FakeGroqClient())

    print("\n== 4) sessão expirada ==")
    clock.advance(days=1)
    stats = run_sync(conn, DemoAdapter("expired"), settings, clock)
    _print_sync(stats)
    _demo_process(conn, settings, clock, fake_llm=FakeGroqClient())

    print("\n== 5) plano de estudo para 2026-10-01 (IA simulada) ==")
    from sigaa_sim.planner import build_plan, render_plan

    plan = build_plan(conn, settings, clock, clock.today(), llm=FakeGroqClient())
    print(render_plan(plan, settings.tz))

    print("\n== mensagens geradas (outbox) ==")
    for r in Outbox(conn).recent(50)[::-1]:
        print(f"[{r['status']}] {r['kind']}: " + r["text"].split("\n")[0])
    print(f"\nlog completo das mensagens: {tmp / 'telegram_demo.log'}")
    conn.close()
    return 0


def _demo_process(conn, settings, clock, fake_llm) -> None:
    import sigaa_sim.knowledge.jobs  # noqa: F401
    import sigaa_sim.llm.jobs  # noqa: F401
    from sigaa_sim import pipeline
    from sigaa_sim.sigaa import DemoAdapter
    from sigaa_sim.telegram import deliver_outbox, make_client

    st = deliver_outbox(conn, make_client(settings), clock, settings)
    ctx = pipeline.JobContext(
        conn=conn,
        settings=settings,
        clock=clock,
        adapter_factory=lambda: DemoAdapter("changed"),
        llm=fake_llm,
    )
    js = {"done": 0, "failed": 0, "deferred": 0, "skipped": 0}
    for _ in range(3):  # jobs derivados (ex.: resumo após extração) na mesma rodada
        r = pipeline.process_jobs(ctx, limit=40)
        for k in js:
            js[k] += r[k]
        if not r["done"]:
            break
    st2 = deliver_outbox(conn, make_client(settings), clock, settings)
    print(f"telegram(demo): enviados={st['sent'] + st2['sent']} | jobs: {js}")


# ----------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sigaa-sim",
        description="SIGAA Simulado — agente de estudo sobre um SIGAA fictício. "
        "Uso normal: `sigaa-sim web` e o resto pelo painel; os demais comandos são opcionais.",
    )
    p.add_argument("--config", help="caminho do config.toml (padrão: ./config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="cria config/.env/banco sem sobrescrever").set_defaults(fn=cmd_init)

    s = sub.add_parser("sync", help="executa uma coleta e processa pendências")
    s.add_argument("--once", action="store_true", default=True, help="(padrão) uma execução")
    s.add_argument("--no-process", action="store_true", help="só coleta; não processa pendências")
    s.add_argument("--echo", action="store_true", help="imprime mensagens do telegram demo")
    s.add_argument("--fixture", help="(perfil demo) cenário: baseline|changed|expired")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("process-pending", help="retoma outbox e jobs sem coletar")
    s.add_argument("--limit", type=int, help="máximo de jobs")
    s.add_argument("--echo", action="store_true")
    s.set_defaults(fn=cmd_process_pending)

    s = sub.add_parser("status", help="saúde, pendências e última execução")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    sub.add_parser("doctor", help="verifica configuração e dependências").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("demo", help="fluxo completo com dados fictícios, sem rede")
    s.add_argument("--dir", help="diretório de dados da demo (padrão: temporário)")
    s.set_defaults(fn=cmd_demo)

    from sigaa_sim.cli import extra, simulador

    extra.register(sub)
    simulador.register(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(Path(args.config) if args.config else None)
    except ConfigError as e:
        print(f"erro de configuração: {e}", file=sys.stderr)
        return 2
    if args.cmd != "init":
        setup_logging(settings.logs_dir, settings.general.log_level, settings.general.log_retention_days)
    try:
        return args.fn(args, settings)
    except ConfigError as e:
        print(f"erro de configuração: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
