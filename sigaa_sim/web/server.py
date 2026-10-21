"""Servidor HTTP local (stdlib) da interface web.

- ``/``             painel (HTML estático + Tailwind CDN + JS vanilla)
- ``/static/*``     arquivos do painel
- ``/api/*``        JSON; consultas em ``api.py``; ações operacionais viram subprocessos da CLI

Segurança: escuta em 127.0.0.1 por padrão. Se ``WEB_TOKEN`` estiver no ``.env``, toda rota
``/api/*`` exige o token (header ``X-Web-Token`` ou cookie definido por ``POST /api/auth``).
Sem token e fora do loopback o servidor recusa subir, a menos que ``--insecure`` seja passado.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sigaa_sim.clock import Clock
from sigaa_sim.config import ConfigError, Settings, load_settings
from sigaa_sim.sigaa import make_clock
from sigaa_sim.storage import connect, migrate
from sigaa_sim.web import api

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
COOKIE = "sigaa_web_token"

# Ações do painel: cada uma vira um ou mais comandos da CLI em subprocesso. Só estas; o cliente
# manda o id e parâmetros, que são validados aqui antes de virar argv (nunca argv vindo do cliente).
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")


def _p_int(params: dict[str, Any], key: str, lo: int, hi: int) -> int | None:
    v = params.get(key)
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise api.ApiError(f"{key}: número inválido") from None
    if not lo <= n <= hi:
        raise api.ApiError(f"{key}: use um valor entre {lo} e {hi}")
    return n


def _p_text(params: dict[str, Any], key: str, pattern: re.Pattern[str], hint: str) -> str | None:
    v = str(params.get(key) or "").strip()
    if not v:
        return None
    if not pattern.match(v):
        raise api.ApiError(f"{key}: formato inválido (use {hint})")
    return v


def _flag(params: dict[str, Any], key: str, opt: str) -> list[str]:
    return [opt] if params.get(key) in (True, "1", "true", "on") else []


def _opt(opt: str, v: Any) -> list[str]:
    return [] if v is None else [opt, str(v)]


def _b_gerar(p: dict[str, Any], settings: Settings) -> list[list[str]]:
    return [
        ["simulador", "gerar"]
        + _opt("--semente", _p_int(p, "semente", 0, 2**31 - 1))
        + _opt("--discentes", _p_int(p, "discentes", 1, 500))
        + _opt("--relogio", _p_text(p, "relogio", _INSTANT_RE, "AAAA-MM-DD ou AAAA-MM-DDTHH:MM"))
        + _flag(p, "sobrescrever", "--sobrescrever")
    ]


def _b_avancar(p: dict[str, Any], settings: Settings) -> list[list[str]]:
    ate = _p_text(p, "ate", _INSTANT_RE, "AAAA-MM-DD ou AAAA-MM-DDTHH:MM")
    if ate:
        return [["simulador", "avancar", "--ate", ate]]
    dias, horas = _p_int(p, "dias", 0, 365) or 0, _p_int(p, "horas", 0, 23) or 0
    if not dias and not horas:
        raise api.ApiError("informe dias, horas ou uma data alvo")
    return [["simulador", "avancar", "--dias", str(dias), "--horas", str(horas)]]


def _b_mochila(p: dict[str, Any], settings: Settings) -> list[list[str]]:
    argv = ["mochila"]
    if p.get("exemplo") in (True, "1", "true", "on"):
        argv += ["--exemplo"] + _opt("--capacidade", _p_int(p, "capacidade", 1, 50))
    else:
        argv += _opt("--date", _p_text(p, "date", _DAY_RE, "AAAA-MM-DD"))
        argv += _opt("--minutos", _p_int(p, "minutos", 1, 1440))
    return [argv + _flag(p, "tabela", "--tabela") + _flag(p, "comparar", "--comparar")]


def _b_plan(p: dict[str, Any], settings: Settings) -> list[list[str]]:
    return [
        ["plan"]
        + _opt("--date", _p_text(p, "date", _DAY_RE, "AAAA-MM-DD"))
        + _flag(p, "new", "--new")
        + _flag(p, "no_ai", "--no-ai")
    ]


def _b_setup(p: dict[str, Any], settings: Settings) -> list[list[str]]:
    """Do zero ao painel cheio: SIGAA fictício (se faltar) → 1ª coleta → plano de hoje."""
    steps = [] if settings.sim_db_path.exists() else [["simulador", "gerar"]]
    return steps + [["sync", "--once", "--echo"], ["plan", "--new"]]


RUN_PRESETS: dict[str, dict[str, Any]] = {
    # início
    "setup-all": {"label": "Preparar tudo", "build": _b_setup, "simulado": True},
    "init": {"label": "Criar config.toml e .env", "argv": ["init"], "reload": True},
    # coleta
    "sync": {"label": "Coletar e processar", "argv": ["sync", "--once", "--echo"]},
    "sync-collect": {"label": "Só coletar (sem processar)", "argv": ["sync", "--once", "--no-process"]},
    "process-pending": {"label": "Processar pendências", "argv": ["process-pending", "--echo"]},
    "telegram-poll": {"label": "Atender Telegram (uma rodada)", "argv": ["telegram-poll", "--once"]},
    # plano e mochila
    "plan": {"label": "Gerar plano", "build": _b_plan},
    "mochila": {"label": "Mochila 0/1 do dia", "argv": ["mochila", "--comparar"], "simulado": True},
    "mochila-custom": {"label": "Mochila 0/1", "build": _b_mochila, "simulado": True},
    # SIGAA Simulado
    "sim-gerar": {"label": "Simulador: gerar SIGAA", "build": _b_gerar, "simulado": True},
    "sim-info": {"label": "Simulador: info", "argv": ["simulador", "info"], "simulado": True},
    "sim-avancar-hora": {
        "label": "Simulador: avançar 1 hora",
        "argv": ["simulador", "avancar", "--dias", "0", "--horas", "1"],
        "simulado": True,
    },
    "sim-avancar-dia": {
        "label": "Simulador: avançar 1 dia",
        "argv": ["simulador", "avancar", "--dias", "1"],
        "simulado": True,
    },
    "sim-avancar-semana": {
        "label": "Simulador: avançar 1 semana",
        "argv": ["simulador", "avancar", "--dias", "7"],
        "simulado": True,
    },
    "sim-avancar": {"label": "Simulador: avançar o relógio", "build": _b_avancar, "simulado": True},
    "sim-eventos": {
        "label": "Simulador: próximos eventos",
        "argv": ["simulador", "eventos"],
        "simulado": True,
    },
    "sim-eventos-todos": {
        "label": "Simulador: todos os eventos",
        "argv": ["simulador", "eventos", "--todos"],
        "simulado": True,
    },
    "sim-sessao-expirar": {
        "label": "Simulador: expirar sessão",
        "argv": ["simulador", "sessao", "--expirar"],
        "simulado": True,
    },
    "sim-sessao-renovar": {
        "label": "Simulador: renovar sessão",
        "argv": ["simulador", "sessao", "--renovar"],
        "simulado": True,
    },
    # diagnóstico
    "doctor": {"label": "Diagnóstico (doctor)", "argv": ["doctor"]},
    "status": {"label": "Status detalhado", "argv": ["status", "-v"]},
    "demo": {"label": "Demo completa (pasta temporária)", "argv": ["demo"]},
    # perfil demo
    "sync-fixture-changed": {
        "label": "Demo: fixture alterada",
        "argv": ["sync", "--once", "--fixture", "changed"],
        "demo": True,
    },
    "sync-fixture-baseline": {
        "label": "Demo: fixture baseline",
        "argv": ["sync", "--once", "--fixture", "baseline"],
        "demo": True,
    },
    "sync-fixture-expired": {
        "label": "Demo: sessão expirada",
        "argv": ["sync", "--once", "--fixture", "expired"],
        "demo": True,
    },
}


class RunRegistry:
    """Executa presets da CLI em subprocesso, um por vez, guardando a saída em memória."""

    def __init__(self, settings: Settings, keep: int = 20, on_reload=None):
        self.settings = settings
        self.on_reload = on_reload
        self.keep = keep
        self._lock = threading.Lock()
        self._runs: list[dict[str, Any]] = []
        self._seq = 0

    def presets(self) -> list[dict[str, Any]]:
        demo = self.settings.sigaa.profile == "demo"
        out = []
        for key, p in RUN_PRESETS.items():
            if p.get("demo") and not demo:
                continue
            if p.get("simulado") and demo:
                continue
            out.append({"id": key, "label": p["label"]})
        return out

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(r) for r in reversed(self._runs)]

    def get(self, run_id: int, tail: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            for r in self._runs:
                if r["id"] == run_id:
                    d = self._public(r)
                    lines = r["lines"]
                    d["output"] = lines[-tail:] if tail else list(lines)
                    return d
        return None

    def running(self) -> dict[str, Any] | None:
        with self._lock:
            for r in self._runs:
                if r["status"] == "running":
                    return self._public(r)
        return None

    def start(self, preset: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = RUN_PRESETS.get(preset)
        if not p or {x["id"] for x in self.presets()}.isdisjoint({preset}):
            raise api.ApiError(f"comando não permitido: {preset}", 400)
        steps = p["build"](params or {}, self.settings) if "build" in p else [p["argv"]]
        if self.running():
            raise api.ApiError("já existe um comando em execução", 409)
        base = [sys.executable, "-m", "sigaa_sim.cli.main"]
        if self.settings.config_path:
            base += ["--config", str(self.settings.config_path)]
        with self._lock:
            self._seq += 1
            run = {
                "id": self._seq,
                "preset": preset,
                "label": p["label"],
                "commands": [" ".join(step) for step in steps],
                "reload": bool(p.get("reload")),
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
                "lines": [],
            }
            self._runs.append(run)
            self._runs = self._runs[-self.keep :]
        threading.Thread(target=self._exec, args=(run, [base + step for step in steps]), daemon=True).start()
        return self._public(run)

    def _exec(self, run: dict[str, Any], argvs: list[list[str]]) -> None:
        code = 0
        for argv in argvs:  # passos em sequência; para no primeiro que falhar
            if len(argvs) > 1:
                with self._lock:
                    run["lines"].append(f"$ sigaa-sim {' '.join(argv[3:])}")
            code = self._exec_one(run, argv)
            if code != 0:
                break
        with self._lock:
            run["exit_code"] = code
            run["status"] = "ok" if code == 0 else "failed"
            run["finished_at"] = time.time()
        if code == 0 and run["reload"] and self.on_reload:
            self.on_reload()

    def _exec_one(self, run: dict[str, Any], argv: list[str]) -> int:
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(Path.cwd()),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with self._lock:
                    run["lines"].append(line.rstrip("\n"))
                    if len(run["lines"]) > 2000:
                        del run["lines"][: len(run["lines"]) - 2000]
            return proc.wait()
        except Exception as e:  # noqa: BLE001 — qualquer falha vira saída legível
            with self._lock:
                run["lines"].append(f"erro ao executar: {type(e).__name__}: {e}")
            return -1

    @staticmethod
    def _public(r: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in r.items() if k not in ("lines", "reload")} | {"line_count": len(r["lines"])}


class WebApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.runs = RunRegistry(settings, on_reload=self.reload)
        settings.ensure_dirs()
        conn = connect(settings.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()

    # recriado a cada request: o banco simulado pode nascer (ou o relógio andar) com o servidor no ar
    @property
    def clock(self) -> Clock:
        return make_clock(self.settings)

    def reload(self) -> None:
        """Relê config.toml/.env (depois de `init` pelo painel), sem reiniciar o servidor."""
        try:
            new = load_settings(self.settings.config_path or Path("config.toml"))
        except ConfigError as e:
            log.warning("config não recarregada: %s", e)
            return
        new.ensure_dirs()
        self.settings = new
        self.runs.settings = new

    # cada request abre a própria conexão (SQLite + threads)
    def conn(self):
        return connect(self.settings.db_path)

    @property
    def token(self) -> str:
        return self.settings.secrets.web_token

    def token_ok(self, presented: str | None) -> bool:
        if not self.token:
            return True
        return bool(presented) and hmac.compare_digest(presented, self.token)


class Handler(BaseHTTPRequestHandler):
    server_version = "sigaa-sim-web/0.1"
    app: WebApp  # definido em make_server

    # ------------------------------------------------------------ utilidades
    def log_message(self, fmt, *args):  # noqa: D401 — log padrão do http.server é ruidoso
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send_json(self, data: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(
        self, path: Path, content_type: str | None = None, download_name: str | None = None
    ) -> None:
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", ctype if not ctype.startswith("text/") else f"{ctype}; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header("Content-Disposition", f'inline; filename="{download_name}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise api.ApiError("corpo muito grande", 413)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise api.ApiError("JSON inválido") from e
        if not isinstance(data, dict):
            raise api.ApiError("JSON deve ser um objeto")
        return data

    def _presented_token(self) -> str | None:
        h = self.headers.get("X-Web-Token")
        if h:
            return h.strip()
        raw = self.headers.get("Cookie")
        if raw:
            c = SimpleCookie()
            try:
                c.load(raw)
            except Exception:  # noqa: BLE001
                return None
            if COOKIE in c:
                return c[COOKIE].value
        return None

    # -------------------------------------------------------------- roteamento
    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        try:
            if path.startswith("/api/"):
                self._api(method, path[5:].strip("/"), query)
            elif method == "GET":
                self._static(path)
            else:
                self._send_json({"error": "método não permitido"}, 405)
        except api.ApiError as e:
            self._send_json({"error": str(e)}, e.status)
        except ConfigError as e:
            self._send_json({"error": f"configuração: {e}"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("erro na rota %s %s", method, path)
            self._send_json({"error": f"erro interno ({type(e).__name__})"}, 500)

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html") or (
            not path.startswith("/static/") and "." not in path.rsplit("/", 1)[-1]
        ):
            self._send_file(STATIC_DIR / "index.html", "text/html")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() in target.parents and target.is_file():
                self._send_file(target)
                return
        self._send_json({"error": "não encontrado"}, 404)

    def _api(self, method: str, route: str, q: dict[str, str]) -> None:
        app = self.app
        # autenticação (rota /auth é pública para permitir o login)
        if route == "auth":
            if method == "GET":
                self._send_json({"required": bool(app.token), "ok": app.token_ok(self._presented_token())})
                return
            data = self._read_json()
            if not app.token_ok(str(data.get("token") or "")):
                self._send_json({"error": "token inválido"}, 401)
                return
            cookie = f"{COOKIE}={data.get('token', '')}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
            self._send_json({"ok": True}, extra_headers={"Set-Cookie": cookie} if app.token else None)
            return
        if not app.token_ok(self._presented_token()):
            self._send_json({"error": "token necessário"}, 401)
            return

        settings, clock = app.settings, app.clock
        segs = route.split("/")
        conn = app.conn()
        try:
            if method == "GET":
                self._get(conn, settings, clock, segs, q)
            else:
                self._post(conn, settings, clock, segs, self._read_json())
        finally:
            conn.close()

    # --------------------------------------------------------------------- GET
    def _get(self, conn, settings: Settings, clock: Clock, segs: list[str], q: dict[str, str]) -> None:
        head, rest = segs[0], segs[1:]
        if head == "overview":
            return self._send_json(api.overview(conn, settings, clock))
        if head == "courses":
            if rest:
                return self._send_json(api.course_detail(conn, settings, clock, rest[0]))
            return self._send_json(api.courses(conn, settings, clock))
        if head == "deadlines":
            return self._send_json(api.deadlines(conn, settings, clock, _int(q.get("horizon"), 60, 1, 365)))
        if head == "items":
            if rest:
                return self._send_json(api.item_detail(conn, settings, clock, "/".join(rest)))
            return self._send_json(
                api.items(
                    conn,
                    settings,
                    clock,
                    q.get("kind") or None,
                    q.get("course") or None,
                    q.get("q") or None,
                    _int(q.get("limit"), 200, 1, 1000),
                )
            )
        if head == "events":
            return self._send_json(
                api.events(
                    conn, settings, clock, _int(q.get("days"), 14, 1, 365), _int(q.get("limit"), 200, 1, 1000)
                )
            )
        if head == "grades":
            return self._send_json(api.grades(conn, settings, clock))
        if head == "materials":
            if rest and rest[-1] == "file":
                item_id = "/".join(rest[:-1])
                path, ctype = api.material_file(conn, settings, item_id)
                return self._send_file(path, ctype, path.name)
            return self._send_json(api.materials(conn, settings, clock, q.get("course") or None))
        if head == "plan":
            day = _date(q.get("date")) or clock.today()
            plan = api.plan_get(conn, settings, clock, day)
            return self._send_json(
                {
                    "date": day.isoformat(),
                    "plan": plan,
                    "available_minutes": settings.planner.minutes_for_weekday(day.weekday()),
                }
            )
        if head == "progress":
            return self._send_json(api.progress_log(conn, settings, clock, _int(q.get("days"), 30, 1, 365)))
        if head == "search":
            term = (q.get("q") or "").strip()
            if not term:
                return self._send_json({"fts": False, "hits": []})
            return self._send_json(
                api.search(
                    conn, settings, clock, term, q.get("course") or None, _int(q.get("limit"), 12, 1, 50)
                )
            )
        if head == "ops":
            sub = rest[0] if rest else "status"
            if sub == "status":
                d = api.ops_status(conn, settings, clock)
                d["presets"] = self.app.runs.presets()
                d["setup"] = _setup_state(conn, settings, clock)
                d["running"] = self.app.runs.running()
                return self._send_json(d)
            if sub == "runs":
                if len(rest) > 1:
                    r = self.app.runs.get(_int(rest[1], 0), _int(q.get("tail"), 0, 0, 5000) or None)
                    if not r:
                        raise api.ApiError("execução não encontrada", 404)
                    return self._send_json(r)
                return self._send_json(self.app.runs.list())
            if sub == "outbox":
                return self._send_json(api.outbox_recent(conn, _int(q.get("limit"), 30, 1, 200)))
            if sub == "logs":
                return self._send_json({"lines": api.log_tail(settings, _int(q.get("lines"), 200, 10, 2000))})
        if head == "config":
            return self._send_json(api.config_view(settings))
        raise api.ApiError("rota não encontrada", 404)

    # -------------------------------------------------------------------- POST
    def _post(self, conn, settings: Settings, clock: Clock, segs: list[str], data: dict[str, Any]) -> None:
        head, rest = segs[0], segs[1:]
        if head == "chat":
            if rest and rest[0] == "action":
                return self._send_json(
                    {"view": api.chat_action(conn, settings, clock, str(data.get("callback") or ""))}
                )
            return self._send_json(
                {"view": api.chat_message(conn, settings, clock, str(data.get("text") or ""))}
            )
        if head == "plan":
            day = _date(data.get("date")) or clock.today()
            plan = api.plan_build(
                conn, settings, clock, day, bool(data.get("new")), not bool(data.get("no_ai"))
            )
            return self._send_json({"date": day.isoformat(), "plan": plan})
        if head == "progress":
            api.progress_record(
                conn,
                clock,
                _int(data.get("task_id"), 0),
                str(data.get("status") or "done"),
                _int(data.get("minutes"), 0) or None,
                (data.get("difficulty") or None),
                (str(data.get("note") or "").strip()[:500] or None),
            )
            return self._send_json({"ok": True})
        if head == "ops" and rest and rest[0] == "run":
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            return self._send_json(self.app.runs.start(str(data.get("preset") or ""), params), 202)
        raise api.ApiError("rota não encontrada", 404)


def _setup_state(conn, settings: Settings, clock: Clock) -> dict[str, Any]:
    """Checklist de primeiros passos mostrado no painel de controle."""
    simulado = settings.sigaa.profile == "simulado"
    synced = conn.execute("SELECT 1 FROM sync_runs WHERE status != 'failed' LIMIT 1").fetchone() is not None
    planned = conn.execute(
        "SELECT 1 FROM plans WHERE plan_date = ? LIMIT 1", (clock.today().isoformat(),)
    ).fetchone()
    return {
        "profile": settings.sigaa.profile,
        "config": settings.config_path is not None,
        "sim_db": settings.sim_db_path.exists() if simulado else None,
        "synced": synced,
        "plan_today": planned is not None,
        "now": clock.now().isoformat(),
        "cwd": str(Path.cwd()),
    }


def _int(v: Any, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v))
    except ValueError as e:
        raise api.ApiError("data inválida (use AAAA-MM-DD)") from e


def make_server(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    app = WebApp(settings)
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def serve(
    settings: Settings,
    host: str = "127.0.0.1",
    port: int = 8765,
    insecure: bool = False,
    browser: bool = False,
) -> int:
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.secrets.web_token and not insecure:
        print(
            "erro: para escutar fora do loopback defina WEB_TOKEN no .env (ou passe --insecure).",
            file=sys.stderr,
        )
        return 2
    srv = make_server(settings, host, port)
    shown = f"http://{host}:{srv.server_address[1]}/"
    print(f"interface web em {shown}  (Ctrl+C para sair)")
    if not settings.secrets.web_token:
        print("aviso: sem WEB_TOKEN — qualquer processo local com acesso à porta pode usar a interface.")
    print("tudo o mais (gerar o SIGAA, coletar, avançar o tempo, mochila…) é pelo painel: Controle.")
    if browser:
        import webbrowser

        threading.Timer(0.6, webbrowser.open, (shown,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
