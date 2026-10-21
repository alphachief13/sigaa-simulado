"""Interface web: servidor stdlib em porta efêmera, banco demo, sem rede externa."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from sigaa_sim.clock import FixedClock
from sigaa_sim.config import Settings
from sigaa_sim.llm.fake import FakeGroqClient
from sigaa_sim.sigaa import DemoAdapter
from sigaa_sim.sync import run_sync
from sigaa_sim.web import api as web_api
from sigaa_sim.web.server import make_server


def _populate(conn, settings: Settings, clock: FixedClock) -> None:
    """Baseline → fixture alterada → jobs (extração) com IA simulada."""
    from sigaa_sim import pipeline

    run_sync(conn, DemoAdapter("baseline"), settings, clock)
    clock.advance(days=1)
    run_sync(conn, DemoAdapter("changed"), settings, clock)
    ctx = pipeline.JobContext(
        conn=conn,
        settings=settings,
        clock=clock,
        adapter_factory=lambda: DemoAdapter("changed"),
        llm=FakeGroqClient(),
    )
    for _ in range(3):
        if not pipeline.process_jobs(ctx, limit=40)["done"]:
            break


@pytest.fixture
def populated(conn, settings, clock):
    _populate(conn, settings, clock)
    return conn


@pytest.fixture
def server(settings, populated):
    srv = make_server(settings, "127.0.0.1", 0)
    # o servidor usa Clock real; os dados demo têm prazos em set/out 2026, mas as rotas não dependem disso
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(base: str, path: str, headers: dict | None = None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        ctype = r.headers.get("Content-Type", "")
        body = r.read()
        return r.status, (json.loads(body) if "json" in ctype else body), r.headers


def _post(base: str, path: str, data: dict, headers: dict | None = None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read()), r.headers


def _status(exc_or_call):
    try:
        exc_or_call()
    except urllib.error.HTTPError as e:
        return e.code
    return 200


# ------------------------------------------------------------------ api.py direto
def test_overview_and_lists(populated, settings, clock):
    ov = web_api.overview(populated, settings, clock)
    assert ov["kpis"]["courses"] == 3
    assert ov["kpis"]["items"] > 0
    assert ov["last_run"]["status"] in ("success", "partial")
    assert ov["kpis"]["events_7d"] > 0  # fixture alterada gerou eventos
    assert ov["next_deadlines"], "fixture demo tem prazos futuros"

    courses = web_api.courses(populated, settings, clock)
    assert len(courses) == 3 and all("short_name" in c for c in courses)
    detail = web_api.course_detail(populated, settings, clock, courses[0]["id"])
    assert detail["course"]["id"] == courses[0]["id"]

    dl = web_api.deadlines(populated, settings, clock, 120)
    assert dl and all(d["due_label"] for d in dl)

    evs = web_api.events(populated, settings, clock, days=30)
    kinds = {e["event_type"] for e in evs}
    assert "deadline_changed" in kinds  # prova adiada na fixture "changed"
    assert all("line" in e for e in evs)


def test_item_detail_and_material(populated, settings, clock):
    mats = web_api.materials(populated, settings, clock)
    assert mats
    downloaded = [m for m in mats if m["status"] == "downloaded"]
    assert downloaded, "demo baixa materiais dos fixtures"
    m = downloaded[0]
    assert m["file_available"] is True
    path, ctype = web_api.material_file(populated, settings, m["item_id"])
    assert path.exists() and ctype

    it = web_api.item_detail(populated, settings, clock, m["item_id"])
    assert it["kind"] == "material" and it["material"]["item_id"] == m["item_id"]
    assert it["revisions"]

    with pytest.raises(web_api.ApiError) as ei:
        web_api.item_detail(populated, settings, clock, "nao-existe")
    assert ei.value.status == 404
    with pytest.raises(web_api.ApiError):
        web_api.material_file(populated, settings, "nao-existe")


def test_plan_progress_search_chat(populated, settings, clock):
    day = clock.today()
    assert web_api.plan_get(populated, settings, clock, day) is None
    plan = web_api.plan_build(populated, settings, clock, day, new=False, use_ai=False)
    assert plan["tasks"], "há compromissos no horizonte → plano básico"
    assert plan["used_minutes"] <= plan["available_minutes"]
    tid = plan["tasks"][0]["task_id"]
    web_api.progress_record(populated, clock, tid, "done", 30, "hard", "confundi U e V")
    got = web_api.plan_get(populated, settings, clock, day)
    assert got["tasks"][0]["status"] == "done" and got["tasks"][0]["difficulty"] == "hard"
    log = web_api.progress_log(populated, settings, clock)
    assert log["entries"][0]["note"] == "confundi U e V"
    assert log["daily"] and log["daily"][0]["minutes"] == 30
    with pytest.raises(web_api.ApiError):
        web_api.progress_record(populated, clock, 999999, "done")

    res = web_api.search(populated, settings, clock, "jacobiano polares")
    assert isinstance(res["hits"], list)

    view = web_api.chat_message(populated, settings, clock, "/prazos")
    assert view["title"] and isinstance(view["blocks"], list)
    view = web_api.chat_message(populated, settings, clock, "quando é minha prova de cálculo?")
    assert view["title"]
    view = web_api.chat_action(populated, settings, clock, "status:full")
    assert "Disciplinas" in " ".join(view["blocks"][0]["lines"])
    # o chip "O que eu estudo hoje?" usa o callback "hoje": não pode cair na ajuda
    view = web_api.chat_action(populated, settings, clock, "hoje")
    assert not view["help"]
    assert web_api.chat_message(populated, settings, clock, "/ajuda")["help"]
    assert web_api.chat_action(populated, settings, clock, "xyz")["help"]
    with pytest.raises(web_api.ApiError):
        web_api.chat_message(populated, settings, clock, "   ")


def test_ops_and_config_hide_secrets(populated, settings, clock):
    settings.secrets.groq_api_key = "segredo-xyz"
    st = web_api.ops_status(populated, settings, clock)
    assert st["last_run"] and st["runs"]
    assert any(c["label"].startswith("perfil SIGAA") for c in st["checks"])
    cfg = web_api.config_view(settings)
    assert cfg["secrets_present"]["GROQ_API_KEY"] is True
    assert "segredo-xyz" not in json.dumps(cfg)
    assert isinstance(web_api.outbox_recent(populated), list)
    assert isinstance(web_api.log_tail(settings), list)


# ------------------------------------------------------------------ servidor HTTP
def test_http_routes(server):
    st, body, headers = _get(server, "/")
    assert st == 200 and b"SIGAA Simulado" in body and "text/html" in headers["Content-Type"]
    st, body, _ = _get(server, "/static/app.js")
    assert st == 200 and b"hashchange" in body
    st, body, _ = _get(server, "/#/plano")
    assert st == 200

    for path in (
        "/api/overview",
        "/api/courses",
        "/api/deadlines?horizon=90",
        "/api/items?kind=announcement",
        "/api/events?days=30",
        "/api/grades",
        "/api/materials",
        "/api/plan",
        "/api/progress",
        "/api/search?q=jacobiano",
        "/api/ops/status",
        "/api/ops/runs",
        "/api/ops/outbox",
        "/api/ops/logs",
        "/api/config",
    ):
        st, data, _ = _get(server, path)
        assert st == 200, path
        assert data is not None

    st, auth, _ = _get(server, "/api/auth")
    assert auth == {"required": False, "ok": True}

    st, data, _ = _post(server, "/api/chat", {"text": "/notas"})
    assert data["view"]["title"]
    st, data, _ = _post(server, "/api/chat/action", {"callback": "pz:1"})
    assert data["view"]["title"]

    st, data, _ = _post(server, "/api/plan", {"no_ai": True})
    assert "plan" in data

    # erros
    assert _status(lambda: _get(server, "/api/nao-existe")) == 404
    assert _status(lambda: _get(server, "/api/items?kind=xpto")) == 400
    assert _status(lambda: _get(server, "/api/materials/nao-existe/file")) == 404
    assert _status(lambda: _get(server, "/static/../server.py")) == 404
    assert _status(lambda: _post(server, "/api/ops/run", {"preset": "rm -rf"})) == 400
    assert _status(lambda: _post(server, "/api/plan", {"date": "ontem"})) == 400


def test_http_token_required(settings, populated):
    settings.secrets.web_token = "abc123"
    srv = make_server(settings, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st, body, _ = _get(base, "/")  # estático continua público (a tela de token vive nele)
        assert st == 200
        assert _status(lambda: _get(base, "/api/overview")) == 401
        assert _status(lambda: _post(base, "/api/auth", {"token": "errado"})) == 401
        st, data, _ = _get(base, "/api/overview", {"X-Web-Token": "abc123"})
        assert st == 200 and data["kpis"]["courses"] == 3
        st, data, headers = _post(base, "/api/auth", {"token": "abc123"})
        cookie = headers["Set-Cookie"].split(";")[0]
        assert cookie.startswith("sigaa_web_token=abc123")
        st, data, _ = _get(base, "/api/courses", {"Cookie": cookie})
        assert st == 200 and len(data) == 3
    finally:
        srv.shutdown()
        srv.server_close()


def test_run_registry_presets(settings):
    from sigaa_sim.web.server import RunRegistry

    reg = RunRegistry(settings)
    ids = {p["id"] for p in reg.presets()}
    assert "sync" in ids and "doctor" in ids
    assert "sync-fixture-changed" in ids  # perfil demo
    assert "sim-avancar-dia" not in ids  # só no perfil simulado
    with pytest.raises(web_api.ApiError):
        reg.start("login")  # não existe login: o projeto não acessa o SIGAA real
    settings.sigaa.profile = "simulado"
    ids = {p["id"] for p in reg.presets()}
    assert {"sim-avancar-dia", "mochila"} <= ids and "sync-fixture-changed" not in ids
    assert reg.list() == [] and reg.running() is None


def test_run_presets_params(settings):
    """Botões com opções: parâmetros validados no servidor viram argv; nada do cliente vai cru."""
    from sigaa_sim.web.server import RUN_PRESETS

    def build(preset, params):
        return RUN_PRESETS[preset]["build"](params, settings)

    assert build("sim-avancar", {"dias": "3"}) == [["simulador", "avancar", "--dias", "3", "--horas", "0"]]
    assert build("sim-avancar", {"ate": "2026-11-01T08:30"}) == [
        ["simulador", "avancar", "--ate", "2026-11-01T08:30"]
    ]
    assert build("mochila-custom", {"exemplo": "1", "capacidade": "9", "tabela": "on"}) == [
        ["mochila", "--exemplo", "--capacidade", "9", "--tabela"]
    ]
    assert build("plan", {"date": "2026-10-21", "no_ai": "on"}) == [
        ["plan", "--date", "2026-10-21", "--no-ai"]
    ]
    assert build("sim-gerar", {"semente": "7", "sobrescrever": "on"}) == [
        ["simulador", "gerar", "--semente", "7", "--sobrescrever"]
    ]
    # banco simulado ainda não existe: "Preparar tudo" gera, coleta e planeja
    assert build("setup-all", {}) == [["simulador", "gerar"], ["sync", "--once", "--echo"], ["plan", "--new"]]
    for preset, bad in (
        ("sim-avancar", {}),
        ("sim-avancar", {"dias": "-1"}),
        ("sim-avancar", {"ate": "amanhã; rm -rf /"}),
        ("mochila-custom", {"minutos": "abc"}),
        ("plan", {"date": "--help"}),
        ("sim-gerar", {"discentes": "99999"}),
    ):
        with pytest.raises(web_api.ApiError):
            build(preset, bad)
