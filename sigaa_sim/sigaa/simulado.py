"""Adapter do SIGAA Simulado: lê o banco gerado por ``sigaa_sim.simulador`` em vez do portal.

Produz registros brutos no mesmo formato que o SIGAA exibe (datas ``dd/mm/aaaa hh:mm``,
notas com vírgula, seções vazias com a mensagem do portal como evidência), de modo que parser,
detecção de mudanças e planejador funcionam exatamente como funcionariam com dados reais.
Só aparece o que já foi publicado segundo o relógio simulado.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from sigaa_sim.domain import AuthResult, AuthStatus, CollectionStatus, Course
from sigaa_sim.sigaa.base import SECTIONS, DownloadResult, RawSection
from sigaa_sim.simulador import banco

FILE_SCHEME = "sim"


def _dh(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")


def _d(iso: str) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def _num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


class SimuladoAdapter:
    name = "simulado"
    supported_sections = SECTIONS

    def __init__(self, sim_db: Path, institution: str = "sim", account: str = "default"):
        self.sim_db = sim_db
        self.institution = institution
        self.account = account
        self._conn: sqlite3.Connection | None = None
        self._authed = False

    # ------------------------------------------------------------------ suporte
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = banco.abrir_existente(self.sim_db)
        return self._conn

    @property
    def agora(self) -> str:
        return banco.relogio(self.conn).isoformat()

    @property
    def matricula(self) -> str:
        m = banco.get_meta(self.conn, "discente_logado")
        if not m:
            raise banco.SimuladorError("banco simulado sem discente logado")
        return m

    def _publicado(self, sql: str, params: tuple) -> list[sqlite3.Row]:
        """Filtra por ``publicado_em <= relógio`` comparando instantes (não strings)."""
        corte = banco.relogio(self.conn)
        return [
            r for r in self.conn.execute(sql, params) if datetime.fromisoformat(r["publicado_em"]) <= corte
        ]

    # ------------------------------------------------------------------ contrato
    def authenticate(self) -> AuthResult:
        if banco.sessao_expirada(self.conn):
            return AuthResult(
                status=AuthStatus.NEEDS_INTERACTION,
                message="sessão simulada expirada (use `sigaa-sim simulador sessao --renovar`)",
            )
        self._authed = True
        return AuthResult(status=AuthStatus.OK, message=f"sessão simulada do discente {self.matricula}")

    def list_courses(self) -> list[Course]:
        rows = self.conn.execute(
            """SELECT t.id_turma, t.periodo, t.codigo_turma, c.codigo, c.nome, d.nome docente
               FROM matricula_turma mt JOIN turma t ON t.id_turma=mt.id_turma
               JOIN componente c ON c.codigo=t.componente JOIN docente d ON d.siape=t.siape
               WHERE mt.matricula=? ORDER BY c.nome""",
            (self.matricula,),
        ).fetchall()
        return [
            Course(
                id=Course.make_id(self.institution, self.account, r["periodo"], str(r["id_turma"])),
                source_id=str(r["id_turma"]),
                term=r["periodo"],
                code=r["codigo"],
                name=r["nome"],
                professor=r["docente"],
                url=f"{FILE_SCHEME}://turma/{r['id_turma']}",
            )
            for r in rows
        ]

    def collect(self, course: Course, section: str) -> RawSection:
        if not self._authed:
            return RawSection(status=CollectionStatus.FAILED, error="não autenticado")
        if section not in self.supported_sections:
            return RawSection(status=CollectionStatus.UNSUPPORTED)
        id_turma = int(course.source_id)
        fn = getattr(self, f"_{section}")
        recs, evidencia = fn(id_turma)
        if not recs:
            return RawSection(status=CollectionStatus.EMPTY, evidence=evidencia)
        return RawSection(status=CollectionStatus.COMPLETE, records=recs)

    # ------------------------------------------------------------------- seções
    def _announcements(self, id_turma: int) -> tuple[list[dict], str]:
        rows = self._publicado(
            "SELECT * FROM noticia WHERE id_turma=? ORDER BY publicado_em DESC, id DESC", (id_turma,)
        )
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["titulo"],
                "published": _dh(r["publicado_em"]),
                "body": r["conteudo"],
                "url": f"{FILE_SCHEME}://turma/{id_turma}/noticia/{r['id']}",
            }
            for r in rows
        ]
        return recs, "Nenhum item foi encontrado."

    def _activities(self, id_turma: int) -> tuple[list[dict], str]:
        rows = self._publicado("SELECT * FROM tarefa WHERE id_turma=? ORDER BY fim", (id_turma,))
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["titulo"],
                "body": r["descricao"],
                "deadline": _dh(r["fim"]),
                "weight": _num(r["peso"]),
                "extra": {"portal_kind": "tarefa", "nota_maxima": r["nota_maxima"]},
            }
            for r in rows
        ]
        return recs, "painel 'Minhas atividades' sem tarefas desta turma"

    def _assessments(self, id_turma: int) -> tuple[list[dict], str]:
        rows = self.conn.execute(
            "SELECT * FROM avaliacao_plano WHERE id_turma=? ORDER BY data, id", (id_turma,)
        ).fetchall()
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["descricao"],
                "deadline": _d(r["data"]),
                "scope": r["conteudo"],
                "weight": _num(r["peso"]) if r["peso"] is not None else None,
                "extra": {"unidade": r["unidade"]},
            }
            for r in rows
        ]
        return recs, "Esta turma ainda não possui um plano cadastrado."

    def _grades(self, id_turma: int) -> tuple[list[dict], str]:
        corte = banco.relogio(self.conn)
        rows = [
            r
            for r in self.conn.execute(
                """SELECT a.*, n.valor, n.lancada_em FROM avaliacao_nota a
                   JOIN nota n ON n.id_avaliacao=a.id AND n.matricula=?
                   WHERE a.id_turma=? ORDER BY a.unidade, a.id""",
                (self.matricula, id_turma),
            )
            if datetime.fromisoformat(r["lancada_em"]) <= corte
        ]
        if not rows:
            return [], "Ainda não foram lançadas notas."
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["denominacao"],
                "grade_value": _num(r["valor"]),
                "grade_max": _num(r["nota_maxima"]),
                "grade_label": r["abreviacao"],
                "weight": _num(r["peso"]),
                "extra": {"unidade": r["unidade"]},
            }
            for r in rows
        ]
        faltas = self.conn.execute(
            "SELECT COALESCE(SUM(faltas),0) f FROM frequencia WHERE id_turma=? AND matricula=? AND data<=?",
            (id_turma, self.matricula, corte.date().isoformat()),
        ).fetchone()["f"]
        recs.append({"source_id": "faltas", "title": "Faltas", "grade_value": str(faltas)})
        return recs, ""

    def _contents(self, id_turma: int) -> tuple[list[dict], str]:
        rows = self._publicado("SELECT * FROM topico_aula WHERE id_turma=? ORDER BY data_inicio", (id_turma,))
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["titulo"],
                "published": _d(r["data_inicio"]),
                "body": r["conteudo"],
                "extra": {"until": _d(r["data_fim"])},
            }
            for r in rows
        ]
        return recs, "turma virtual sem tópicos de aula"

    def _materials(self, id_turma: int) -> tuple[list[dict], str]:
        rows = self._publicado(
            """SELECT a.*, t.titulo topico FROM arquivo a LEFT JOIN topico_aula t ON t.id=a.id_topico
               WHERE a.id_turma=? ORDER BY a.publicado_em, a.id""",
            (id_turma,),
        )
        recs = [
            {
                "source_id": str(r["id"]),
                "title": r["descricao"] or r["nome"],
                "body": r["descricao"],
                "file_name": r["nome"],
                "file_url": f"{FILE_SCHEME}://arquivo/{id_turma}/{r['id']}",
                "file_version_hint": f"v{r['versao']} {len(r['conteudo'].encode())} B",
                "extra": {"topic": r["topico"]},
            }
            for r in rows
        ]
        return recs, "Nenhum item foi encontrado."

    # ----------------------------------------------------------------- download
    def download(
        self, url: str, *, max_bytes: int, etag: str | None = None, last_modified: str | None = None
    ) -> DownloadResult:
        prefixo = f"{FILE_SCHEME}://arquivo/"
        if not url.startswith(prefixo):
            return DownloadResult(ok=False, error=f"URL fora do SIGAA Simulado: {url}")
        try:
            id_turma, id_arquivo = (int(x) for x in url[len(prefixo) :].split("/"))
        except ValueError:
            return DownloadResult(ok=False, error=f"URL de arquivo malformada: {url}")
        if banco.sessao_expirada(self.conn):
            return DownloadResult(ok=False, status_code=401, error="sessão simulada expirada")
        r = self.conn.execute(
            "SELECT * FROM arquivo WHERE id=? AND id_turma=?", (id_arquivo, id_turma)
        ).fetchone()
        if not r or datetime.fromisoformat(r["publicado_em"]) > banco.relogio(self.conn):
            return DownloadResult(ok=False, status_code=404, error="arquivo não encontrado")
        data = r["conteudo"].encode("utf-8")
        if len(data) > max_bytes:
            return DownloadResult(ok=False, error=f"arquivo excede limite ({len(data)} bytes)")
        tag = f'"{id_arquivo}-v{r["versao"]}"'
        if etag and etag == tag:
            return DownloadResult(ok=True, not_modified=True, etag=tag, status_code=304)
        return DownloadResult(ok=True, data=data, content_type=r["content_type"], etag=tag, status_code=200)

    def close(self) -> None:
        self._authed = False
        if self._conn is not None:
            self._conn.close()
            self._conn = None
