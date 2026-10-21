"""Acesso ao banco do SIGAA Simulado: conexão, relógio simulado e linha do tempo de eventos."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sigaa_sim.clock import Clock

SCHEMA = Path(__file__).with_name("schema.sql")

# Tabelas e colunas que um evento pode alterar (evita SQL arbitrário vindo do banco).
COLUNAS_ALTERAVEIS = {
    "avaliacao_plano": {"data", "descricao", "conteudo"},
    "tarefa": {"fim", "descricao", "titulo"},
    "arquivo": {"conteudo", "versao", "descricao", "nome"},
    "topico_aula": {"conteudo", "titulo"},
}


class SimuladorError(Exception):
    pass


@dataclass
class EventoAplicado:
    id: int
    aplica_em: str
    tipo: str
    descricao: str


def conectar(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def criar_esquema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def abrir_existente(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SimuladorError(
            f"banco simulado não encontrado em {path}; gere pelo painel (Controle → Preparar tudo)"
            " ou com `sigaa-sim simulador gerar`"
        )
    return conectar(path)


def get_meta(conn: sqlite3.Connection, chave: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT valor FROM sim_meta WHERE chave=?", (chave,)).fetchone()
    return row["valor"] if row else default


def set_meta(conn: sqlite3.Connection, chave: str, valor: str) -> None:
    conn.execute(
        "INSERT INTO sim_meta(chave, valor) VALUES(?,?) "
        "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
        (chave, valor),
    )


def relogio(conn: sqlite3.Connection) -> datetime:
    valor = get_meta(conn, "relogio")
    if not valor:
        raise SimuladorError("banco simulado sem relógio (meta 'relogio')")
    return datetime.fromisoformat(valor)


def avancar(conn: sqlite3.Connection, ate: datetime) -> list[EventoAplicado]:
    """Move o relógio simulado para ``ate`` e aplica, em ordem, os eventos vencidos.

    O relógio nunca volta: datas anteriores ao relógio atual são rejeitadas, porque eventos já
    aplicados não são desfeitos.
    """
    if ate.tzinfo is None:
        raise SimuladorError("data do relógio precisa de fuso")
    atual = relogio(conn)
    if ate < atual:
        raise SimuladorError(f"o relógio não volta no tempo (atual: {atual.isoformat()})")
    aplicados: list[EventoAplicado] = []
    with conn:
        pendentes = conn.execute(
            "SELECT * FROM evento WHERE aplicado_em IS NULL ORDER BY aplica_em, id"
        ).fetchall()
        for ev in pendentes:
            if datetime.fromisoformat(ev["aplica_em"]) > ate:
                continue
            _aplicar(conn, ev)
            conn.execute("UPDATE evento SET aplicado_em=? WHERE id=?", (ate.isoformat(), ev["id"]))
            aplicados.append(EventoAplicado(ev["id"], ev["aplica_em"], ev["tipo"], ev["descricao"]))
        set_meta(conn, "relogio", ate.isoformat())
    return aplicados


def _aplicar(conn: sqlite3.Connection, ev: sqlite3.Row) -> None:
    tabela = ev["tabela"]
    mudancas: dict = json.loads(ev["alteracao_json"])
    permitidas = COLUNAS_ALTERAVEIS.get(tabela)
    if not permitidas or not set(mudancas) <= permitidas:
        raise SimuladorError(
            f"evento {ev['id']} altera colunas não permitidas em {tabela}: {sorted(mudancas)}"
        )
    pk = "id"
    sets = ", ".join(f"{c}=?" for c in mudancas)
    conn.execute(f"UPDATE {tabela} SET {sets} WHERE {pk}=?", [*mudancas.values(), ev["registro_id"]])


def sessao_expirada(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "sessao", "ok") == "expirada"


def definir_sessao(conn: sqlite3.Connection, expirada: bool) -> None:
    with conn:
        set_meta(conn, "sessao", "expirada" if expirada else "ok")


def resumo(conn: sqlite3.Connection) -> dict[str, int | str]:
    """Contagens por tabela + metadados (para `simulador info`)."""
    out: dict[str, int | str] = {}
    for chave in ("semente", "periodo", "relogio", "discente_logado", "sessao"):
        out[chave] = get_meta(conn, chave, "-") or "-"
    for tabela in (
        "discente",
        "docente",
        "componente",
        "turma",
        "matricula_turma",
        "noticia",
        "topico_aula",
        "avaliacao_plano",
        "tarefa",
        "avaliacao_nota",
        "nota",
        "frequencia",
        "arquivo",
        "evento",
    ):
        out[tabela] = conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
    out["eventos_pendentes"] = conn.execute(
        "SELECT COUNT(*) FROM evento WHERE aplicado_em IS NULL"
    ).fetchone()[0]
    return out


class SimClock(Clock):
    """Relógio do agente amarrado ao relógio do SIGAA Simulado.

    Assim prazos, lembretes e o plano de estudo usam o mesmo "agora" que o portal simulado.
    """

    def __init__(self, tz: ZoneInfo, sim_db: Path):
        super().__init__(tz)
        self.sim_db = sim_db

    def now(self) -> datetime:
        conn = abrir_existente(self.sim_db)
        try:
            return relogio(conn).astimezone(self.tz)
        finally:
            conn.close()
