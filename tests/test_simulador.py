"""SIGAA Simulado: geração determinística, integridade, visibilidade temporal e eventos."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from sigaa_sim.clock import FixedClock
from sigaa_sim.domain import AuthStatus, CollectionStatus
from sigaa_sim.planner import GRANULARIDADE, build_plan, collect_commitments, selecionar_sessoes
from sigaa_sim.sigaa import make_adapter, make_clock
from sigaa_sim.sigaa.simulado import SimuladoAdapter
from sigaa_sim.simulador import banco
from sigaa_sim.simulador.gerador import Parametros, gerar
from sigaa_sim.storage import Repo
from sigaa_sim.sync import run_sync

TABELAS = (
    "discente", "docente", "componente", "turma", "matricula_turma", "noticia", "topico_aula",
    "avaliacao_plano", "tarefa", "avaliacao_nota", "nota", "frequencia", "arquivo", "evento",
)  # fmt: skip


@pytest.fixture
def sim(settings) -> Path:
    settings.sigaa.profile = "simulado"
    gerar(settings.sim_db_path, Parametros(semente=7, discentes=25, timezone=settings.general.timezone))
    return settings.sim_db_path


def _dump(path: Path) -> dict[str, list]:
    conn = banco.conectar(path)
    try:
        return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in TABELAS}
    finally:
        conn.close()


# ------------------------------------------------------------------ geração
def test_mesma_semente_gera_o_mesmo_banco(tmp_path):
    a, b, c = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "c.db"
    gerar(a, Parametros(semente=42))
    gerar(b, Parametros(semente=42))
    gerar(c, Parametros(semente=43))
    assert _dump(a) == _dump(b)
    assert _dump(a) != _dump(c)


def test_nao_sobrescreve_sem_pedir(tmp_path):
    p = tmp_path / "x.db"
    gerar(p, Parametros(discentes=3))
    with pytest.raises(banco.SimuladorError):
        gerar(p, Parametros(discentes=3))
    gerar(p, Parametros(discentes=4), sobrescrever=True)
    assert banco.resumo(banco.conectar(p))["discente"] == 4


def test_integridade_referencial_e_regras_do_dominio(sim):
    conn = banco.conectar(sim)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # matrícula com 11 dígitos; um discente por turma no máximo uma vez (PK)
    assert all(len(r[0]) == 11 and r[0].isdigit() for r in conn.execute("SELECT matricula FROM discente"))
    # toda turma tem avaliações, tópicos e arquivos
    for (t,) in conn.execute("SELECT id_turma FROM turma"):
        for tabela in ("avaliacao_plano", "avaliacao_nota", "topico_aula", "arquivo"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabela} WHERE id_turma=?", (t,)).fetchone()[0] > 0
    # notas dentro de [0, máximo] e lançadas depois da (possivelmente nova) data da avaliação
    rows = conn.execute(
        """SELECT n.valor, a.nota_maxima, n.lancada_em, p.data FROM nota n
           JOIN avaliacao_nota a ON a.id=n.id_avaliacao
           JOIN avaliacao_plano p ON p.id_turma=a.id_turma AND p.descricao=a.denominacao"""
    ).fetchall()
    assert rows
    for valor, maximo, lancada, data_aval in rows:
        assert 0 <= valor <= maximo
        assert datetime.fromisoformat(lancada).date() > date.fromisoformat(data_aval)
    # horário no formato do SIGAA: dígitos de dia (2=seg..7=sáb) + turno (M/T/N) + aulas
    for (h,) in conn.execute("SELECT horario FROM turma"):
        assert re.fullmatch(r"[2-7]+[MTN][1-6]+", h), h
    # o discente logado existe e está matriculado
    m = banco.get_meta(conn, "discente_logado")
    assert conn.execute("SELECT COUNT(*) FROM matricula_turma WHERE matricula=?", (m,)).fetchone()[0] == 5
    # eventos anteriores ao relógio inicial já nascem aplicados
    agora = banco.relogio(conn)
    for ev in conn.execute("SELECT aplica_em, aplicado_em FROM evento"):
        assert (ev[1] is not None) == (datetime.fromisoformat(ev[0]) <= agora)
    conn.close()


# ----------------------------------------------------------- relógio e eventos
def test_relogio_nao_volta_e_eventos_so_alteram_colunas_permitidas(sim):
    conn = banco.conectar(sim)
    agora = banco.relogio(conn)
    with pytest.raises(banco.SimuladorError):
        banco.avancar(conn, agora - timedelta(minutes=1))
    with conn:
        conn.execute(
            "INSERT INTO evento(aplica_em, tipo, tabela, registro_id, alteracao_json, descricao) "
            "VALUES(?,?,?,?,?,?)",
            ((agora + timedelta(hours=1)).isoformat(), "x", "nota", 1, json.dumps({"valor": 10}), "fraude"),
        )
    with pytest.raises(banco.SimuladorError, match="não permitidas"):
        banco.avancar(conn, agora + timedelta(hours=2))
    conn.close()


def test_conteudo_aparece_conforme_o_relogio(sim):
    ad = SimuladoAdapter(sim)
    ad.authenticate()
    curso = ad.list_courses()[0]
    antes = len(ad.collect(curso, "announcements").records)
    conn = banco.conectar(sim)
    banco.avancar(conn, banco.relogio(conn) + timedelta(days=40))
    conn.close()
    depois = len(ad.collect(curso, "announcements").records)
    assert depois > antes
    ad.close()


def test_secoes_vazias_trazem_evidencia_do_portal(settings):
    settings.sigaa.profile = "simulado"
    tz = settings.tz
    inicio = datetime(2026, 8, 17, 9, 0, tzinfo=tz)
    gerar(settings.sim_db_path, Parametros(semente=1, discentes=3, relogio=inicio, timezone=str(tz)))
    ad = SimuladoAdapter(settings.sim_db_path)
    ad.authenticate()
    notas = ad.collect(ad.list_courses()[0], "grades")
    assert notas.status == CollectionStatus.EMPTY and notas.evidence == "Ainda não foram lançadas notas."
    ad.close()


def test_sessao_expirada_e_download(sim):
    ad = SimuladoAdapter(sim)
    assert ad.authenticate().status == AuthStatus.OK
    curso = ad.list_courses()[0]
    mat = ad.collect(curso, "materials").records[0]
    r1 = ad.download(mat["file_url"], max_bytes=10_000)
    assert r1.ok and r1.data.startswith(b"# ")
    assert ad.download(mat["file_url"], max_bytes=10_000, etag=r1.etag).not_modified
    assert not ad.download(mat["file_url"], max_bytes=10).ok  # limite de tamanho
    assert ad.download("https://sigaa.ufrn.br/x.pdf", max_bytes=10_000).error.startswith("URL fora")
    # arquivo ainda não publicado é 404
    conn = banco.conectar(sim)
    futuro = conn.execute(
        "SELECT id, id_turma FROM arquivo WHERE publicado_em > ? ORDER BY publicado_em LIMIT 1",
        (banco.relogio(conn).isoformat(),),
    ).fetchone()
    conn.close()
    assert ad.download(f"sim://arquivo/{futuro[1]}/{futuro[0]}", max_bytes=10_000).status_code == 404
    banco.definir_sessao(banco.conectar(sim), expirada=True)
    assert ad.authenticate().status == AuthStatus.NEEDS_INTERACTION
    ad.close()


# --------------------------------------------------- integração com o agente
def test_agente_detecta_prova_adiada_e_arquivo_substituido(conn, settings, sim):
    clock = make_clock(settings)
    assert clock.now() == banco.relogio(banco.conectar(sim)).astimezone(settings.tz)
    base = run_sync(conn, make_adapter(settings), settings, clock)
    assert base.status == "success" and base.baseline and base.items_seen > 50

    # avança até depois de todos os eventos e sincroniza de novo
    sc = banco.conectar(sim)
    # garante um arquivo substituído numa turma do discente (a semente pode não gerar um após o relógio)
    agora = banco.relogio(sc)
    arq = sc.execute(
        """SELECT a.id FROM arquivo a JOIN matricula_turma m ON m.id_turma=a.id_turma
           WHERE m.matricula=? AND a.publicado_em<=? ORDER BY a.id LIMIT 1""",
        (banco.get_meta(sc, "discente_logado"), agora.isoformat()),
    ).fetchone()[0]
    with sc:
        sc.execute(
            "INSERT INTO evento(aplica_em, tipo, tabela, registro_id, alteracao_json, descricao) "
            "VALUES(?,?,?,?,?,?)",
            (
                (agora + timedelta(hours=1)).isoformat(),
                "arquivo_substituido",
                "arquivo",
                arq,
                json.dumps({"conteudo": "# versão 2\\n", "versao": 2}),
                "teste",
            ),
        )
    ultimo = max(datetime.fromisoformat(r[0]) for r in sc.execute("SELECT aplica_em FROM evento"))
    aplicados = {e.id for e in banco.avancar(sc, ultimo + timedelta(minutes=1))}
    minhas_turmas = {
        r[0]
        for r in sc.execute(
            "SELECT id_turma FROM matricula_turma WHERE matricula=?", (banco.get_meta(sc, "discente_logado"),)
        )
    }
    esperados = set()
    for ev in sc.execute("SELECT * FROM evento"):
        turma = sc.execute(f"SELECT id_turma FROM {ev['tabela']} WHERE id=?", (ev["registro_id"],)).fetchone()
        if ev["id"] in aplicados and turma[0] in minhas_turmas:
            esperados.add(ev["tipo"])
    sc.close()
    assert esperados, "a semente 7 deve gerar eventos nas turmas do discente logado"

    st = run_sync(conn, make_adapter(settings), settings, clock)
    assert st.status == "success" and st.created > 0 and st.updated > 0
    tipos = {r[0] for r in conn.execute("SELECT event_type FROM change_events")}
    if esperados & {"prova_adiada", "prazo_prorrogado"}:
        assert "deadline_changed" in tipos
    if "arquivo_substituido" in esperados:
        assert "material_replaced" in tipos


def test_plano_usa_a_mochila_e_respeita_o_tempo(conn, settings, sim):
    clock = make_clock(settings)
    run_sync(conn, make_adapter(settings), settings, clock)
    dia = clock.today()
    commitments = collect_commitments(conn, settings, dia)
    assert commitments
    for minutos in (15, 50, 90, 180):
        escolhidas, res, _ = selecionar_sessoes(commitments, minutos, settings)
        assert sum(s.minutes for s in escolhidas) == res.peso_total * GRANULARIDADE <= minutos
    fixed = FixedClock(settings.tz, clock.now())
    plano = build_plan(conn, settings, fixed, dia, force=True)
    assert plano.tasks and sum(t.minutes for t in plano.tasks) <= plano.available_minutes
    assert any("Mochila 0/1" in n for n in plano.notes)
    assert Repo(conn).count("items") > 0


def test_lembretes_de_prova_citam_a_data_vigente(sim):
    conn = banco.conectar(sim)
    ultimo = max(datetime.fromisoformat(r[0]) for r in conn.execute("SELECT aplica_em FROM evento"))
    banco.avancar(conn, ultimo + timedelta(minutes=1))
    rows = conn.execute(
        """SELECT n.conteudo, p.data FROM noticia n JOIN avaliacao_plano p
           ON p.id_turma = n.id_turma AND n.titulo = 'Lembrete: ' || p.descricao"""
    ).fetchall()
    conn.close()
    assert rows
    for conteudo, data in rows:
        assert date.fromisoformat(data).strftime("%d/%m/%Y") in conteudo
