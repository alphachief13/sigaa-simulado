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
from sigaa_sim.config import ConfigError, Settings
from sigaa_sim.sigaa import make_clock
from sigaa_sim.storage import connect, migrate
from sigaa_sim.web import api

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
COOKIE = "sigaa_web_token"

# Comandos da CLI que a interface pode disparar. Só estes; argumentos vêm daqui, nunca do cliente.
RUN_PRESETS: dict[str, dict[str, Any]] = {
    "sync": {"label": "Coletar e processar", "argv": ["sync", "--once", "--echo"]},
    "sync-collect": {"label": "Só coletar (sem processar)", "argv": ["sync", "--once", "--no-process"]},
    "process-pending": {"label": "Processar pendências", "argv": ["process-pending", "--echo"]},
    "doctor": {"label": "Diagnóstico (doctor)", "argv": ["doctor"]},
    "status": {"label": "Status detalhado", "argv": ["status", "-v"]},
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
    "sim-eventos": {
        "label": "Simulador: próximos eventos",
        "argv": ["simulador", "eventos"],
        "simulado": True,
    },
    "mochila": {"label": "Mochila 0/1 do dia", "argv": ["mochila", "--comparar"], "simulado": True},
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

    def __init__(self, settings: Settings, keep: int = 20):
        self.settings = settings
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
            out.append({"id": key, "label": p["label"], "argv": p["argv"]})
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

    def start(self, preset: str) -> dict[str, Any]:
        p = RUN_PRESETS.get(preset)
        if not p or {x["id"] for x in self.presets()}.isdisjoint({preset}):
            raise api.ApiError(f"comando não permitido: {preset}", 400)
        if self.running():
            raise api.ApiError("já existe um comando em execução", 409)
        argv = [sys.executable, "-m", "sigaa_sim.cli.main"]
        if self.settings.config_path:
            argv += ["--config", str(self.settings.config_path)]
        argv += p["argv"]
        with self._lock:
            self._seq += 1
            run = {
                "id": self._seq,
                "preset": preset,
                "label": p["label"],
                "argv": argv[3:],
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
                "lines": [],
            }
            self._runs.append(run)
            self._runs = self._runs[-self.keep :]
        threading.Thread(target=self._exec, args=(run, argv), daemon=True).start()
        return self._public(run)

    def _exec(self, run: dict[str, Any], argv: list[str]) -> None:
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
            code = proc.wait()
        except Exception as e:  # noqa: BLE001 — qualquer falha vira saída legível
            with self._lock:
                run["lines"].append(f"erro ao executar: {type(e).__name__}: {e}")
            code = -1
        with self._lock:
            run["exit_code"] = code
            run["status"] = "ok" if code == 0 else "failed"
            run["finished_at"] = time.time()

    @staticmethod
    def _public(r: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in r.items() if k != "lines"} | {"line_count": len(r["lines"])}


class WebApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.clock = make_clock(settings)
        self.runs = RunRegistry(settings)
        settings.ensure_dirs()
        conn = connect(settings.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()

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
            return self._send_json(self.app.runs.start(str(data.get("preset") or "")), 202)
        raise api.ApiError("rota não encontrada", 404)


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


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, insecure: bool = False) -> int:
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
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
