"""Gerador determinístico do SIGAA Simulado.

Mesma semente + mesmos parâmetros = mesmo banco, byte a byte nos dados (o que permite
reproduzir cenários em testes e na apresentação). O período letivo é dividido em 3 unidades;
tópicos, materiais, avaliações, tarefas, notícias, notas e faltas são distribuídos no
calendário e ficam visíveis conforme o relógio simulado avança.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sigaa_sim.simulador import banco
from sigaa_sim.simulador.catalogo import (
    COMPONENTES,
    CURSOS,
    DEPARTAMENTOS,
    NOTICIAS_MODELO,
    PRENOMES,
    SALAS,
    SOBRENOMES,
    Componente,
)

TURNOS = ("M", "T", "N")
PARES_DIAS = ("24", "35", "246", "46", "25")


@dataclass
class Parametros:
    semente: int = 2026
    periodo: str = "2026.2"
    inicio: date = date(2026, 8, 17)
    fim: date = date(2026, 12, 19)
    discentes: int = 40
    turmas_por_discente: int = 5
    relogio: datetime | None = None  # padrão: 9 semanas após o início (meio da 2ª unidade), 09:00
    timezone: str = "America/Fortaleza"


@dataclass
class _Turma:
    id_turma: int
    codigo: str
    comp: Componente
    horario: str
    dias_semana: tuple[int, ...]  # 0 = segunda


def gerar(path: Path, p: Parametros | None = None, *, sobrescrever: bool = False) -> dict:
    """Cria o banco simulado em ``path`` e devolve o resumo."""
    p = p or Parametros()
    if path.exists():
        if not sobrescrever:
            raise banco.SimuladorError(f"{path} já existe (use --sobrescrever)")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = banco.conectar(path)
    try:
        banco.criar_esquema(conn)
        with conn:
            relogio = _Gerador(conn, p).executar()
        # eventos anteriores ao relógio inicial já aconteceram: aplica-os
        banco.avancar(conn, relogio)
        return banco.resumo(conn)
    finally:
        conn.close()


class _Gerador:
    def __init__(self, conn: sqlite3.Connection, p: Parametros):
        self.conn = conn
        self.p = p
        self.tz = ZoneInfo(p.timezone)
        self.rnd = random.Random(p.semente)
        self._ids: dict[str, int] = {}
        self.unidades = _dividir_unidades(p.inicio, p.fim)

    # ---------------------------------------------------------------- utilidades
    def _novo_id(self, tabela: str, base: int) -> int:
        self._ids[tabela] = self._ids.get(tabela, base) + 1
        return self._ids[tabela]

    def _dt(self, d: date, h: int = 8, m: int = 0) -> str:
        return datetime.combine(d, time(h, m), tzinfo=self.tz).isoformat()

    def _nome(self) -> str:
        s1, s2 = self.rnd.sample(SOBRENOMES, 2)
        return f"{self.rnd.choice(PRENOMES)} {s1} {s2}"

    def _dia_de_aula(self, t: _Turma, perto_de: date) -> date:
        """Primeiro dia de aula da turma em ``perto_de`` ou depois."""
        d = perto_de
        while d.weekday() not in t.dias_semana:
            d += timedelta(days=1)
        return d

    # ------------------------------------------------------------------- etapas
    def executar(self) -> datetime:
        relogio = self.p.relogio or datetime.combine(
            self.p.inicio + timedelta(weeks=9), time(9, 0), tzinfo=self.tz
        )
        for k, v in {
            "semente": str(self.p.semente),
            "periodo": self.p.periodo,
            "inicio": self.p.inicio.isoformat(),
            "fim": self.p.fim.isoformat(),
            "relogio": relogio.isoformat(),
            "sessao": "ok",
            "aviso": "DADOS FICTÍCIOS gerados para fins acadêmicos.",
        }.items():
            banco.set_meta(self.conn, k, v)

        self.conn.executemany("INSERT INTO curso(id, nome) VALUES(?,?)", CURSOS)
        docentes = self._docentes()
        for c in COMPONENTES:
            self.conn.execute(
                "INSERT INTO componente VALUES(?,?,?,?)", (c.codigo, c.nome, c.carga_horaria, c.departamento)
            )
        turmas = self._turmas(docentes)
        matriculas = self._discentes(turmas)
        for t in turmas:
            alunos = [
                r[0]
                for r in self.conn.execute(
                    "SELECT matricula FROM matricula_turma WHERE id_turma=? ORDER BY matricula", (t.id_turma,)
                )
            ]
            topicos = self._topicos_e_arquivos(t)
            self._avaliacoes_e_notas(t, topicos, alunos)
            self._tarefas(t)
            self._noticias(t, topicos)
            self._frequencia(t, alunos)
            self._eventos(t)
        banco.set_meta(self.conn, "discente_logado", matriculas[0])
        return relogio

    def _docentes(self) -> list[str]:
        out = []
        for _ in range(len(COMPONENTES) + 2):
            siape = str(self.rnd.randint(1_000_000, 3_999_999))
            self.conn.execute(
                "INSERT INTO docente VALUES(?,?,?)", (siape, self._nome(), self.rnd.choice(DEPARTAMENTOS))
            )
            out.append(siape)
        return out

    def _turmas(self, docentes: list[str]) -> list[_Turma]:
        turmas = []
        usados: set[tuple[str, str]] = set()
        for i, c in enumerate(COMPONENTES):
            for n in range(1, 3 if c.carga_horaria >= 90 else 2):
                while True:
                    dias, turno = self.rnd.choice(PARES_DIAS), self.rnd.choice(TURNOS)
                    if (dias, turno) not in usados:
                        usados.add((dias, turno))
                        break
                aulas = "12" if c.carga_horaria == 60 else "123"
                id_turma = self.rnd.randint(570_000, 589_999)
                while any(t.id_turma == id_turma for t in turmas):
                    id_turma += 1
                self.conn.execute(
                    "INSERT INTO turma VALUES(?,?,?,?,?,?,?,?)",
                    (
                        id_turma,
                        c.codigo,
                        f"{n:02d}",
                        self.p.periodo,
                        docentes[i if n == 1 else -n],
                        f"{dias}{turno}{aulas}",
                        self.rnd.choice(SALAS),
                        self.rnd.choice((40, 50, 60)),
                    ),
                )
                turmas.append(
                    _Turma(id_turma, f"{n:02d}", c, f"{dias}{turno}{aulas}", tuple(int(d) - 2 for d in dias))
                )
        return turmas

    def _discentes(self, turmas: list[_Turma]) -> list[str]:
        por_comp: dict[str, list[_Turma]] = {}
        for t in turmas:
            por_comp.setdefault(t.comp.codigo, []).append(t)
        matriculas = []
        for i in range(self.p.discentes):
            ano = self.rnd.choice((2024, 2025, 2026))
            sem = self.rnd.choice((1, 2)) if ano < 2026 else 1
            matricula = f"{ano}{sem}{i + 1:06d}"
            nome = self._nome()
            login = nome.split()[0].lower() + nome.split()[-1].lower()
            self.conn.execute(
                "INSERT INTO discente VALUES(?,?,?,?,?)",
                (
                    matricula,
                    nome,
                    self.rnd.choice(CURSOS)[0],
                    f"{ano}.{sem}",
                    f"{login}.{i + 1:03d}@aluno.sim.edu.br",
                ),
            )
            # o discente logado (o primeiro) fica nas turmas 01 dos primeiros componentes
            if i == 0:
                escolhidos = list(por_comp)[: self.p.turmas_por_discente]
                minhas = [por_comp[c][0] for c in escolhidos]
            else:
                escolhidos = self.rnd.sample(list(por_comp), k=min(self.p.turmas_por_discente, len(por_comp)))
                minhas = [self.rnd.choice(por_comp[c]) for c in escolhidos]
            self.conn.executemany(
                "INSERT INTO matricula_turma(matricula, id_turma) VALUES(?,?)",
                [(matricula, t.id_turma) for t in minhas],
            )
            matriculas.append(matricula)
        return matriculas

    def _topicos_e_arquivos(self, t: _Turma) -> list[tuple[int, int, str, tuple[str, ...]]]:
        """Distribui os tópicos pelas semanas. Retorna (id, unidade, título, palavras)."""
        n = len(t.comp.topicos)
        total = (self.p.fim - self.p.inicio).days
        out = []
        for k, top in enumerate(t.comp.topicos):
            ini = self._dia_de_aula(t, self.p.inicio + timedelta(days=k * total // n))
            fim = ini + timedelta(days=max(6, total // n - 1))
            pub = self._dt(ini - timedelta(days=2), 8)
            tid = self._novo_id("topico_aula", t.id_turma * 100)
            self.conn.execute(
                "INSERT INTO topico_aula VALUES(?,?,?,?,?,?,?)",
                (tid, t.id_turma, top.titulo, top.conteudo, ini.isoformat(), fim.isoformat(), pub),
            )
            unidade = _unidade_de(self.unidades, ini)
            out.append((tid, unidade, top.titulo, top.palavras))
            texto = _nota_de_aula(t.comp, top.titulo, top.conteudo, top.palavras, versao=1)
            aid = self._novo_id("arquivo", 900_000)
            self.conn.execute(
                "INSERT INTO arquivo VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    t.id_turma,
                    tid,
                    f"notas_{t.comp.codigo.lower()}_{k + 1:02d}.md",
                    f"Notas de aula — {top.titulo}",
                    "text/markdown",
                    texto,
                    1,
                    pub,
                ),
            )
        return out

    def _avaliacoes_e_notas(self, t: _Turma, topicos: list, alunos: list[str]) -> None:
        for unidade, abrev, denominacao, maximo, peso in t.comp.avaliacoes:
            u_ini, u_fim = self.unidades[unidade - 1]
            alvo = u_fim - timedelta(days=4 if abrev.startswith("P") else 10)
            data = self._dia_de_aula(t, alvo)
            escopo = "; ".join(tt for (_, u, tt, _) in topicos if u == unidade) or "conteúdo da unidade"
            pid = self._novo_id("avaliacao_plano", 700_000)
            self.conn.execute(
                "INSERT INTO avaliacao_plano VALUES(?,?,?,?,?,?,?)",
                (pid, t.id_turma, denominacao, data.isoformat(), unidade, escopo, peso),
            )
            nid = self._novo_id("avaliacao_nota", 800_000)
            self.conn.execute(
                "INSERT INTO avaliacao_nota VALUES(?,?,?,?,?,?,?)",
                (nid, t.id_turma, unidade, abrev, denominacao, maximo, peso),
            )
            lancada = self._dt(data + timedelta(days=self.rnd.randint(5, 12)), 18, 30)
            for m in alunos:
                valor = max(0.0, min(maximo, round(self.rnd.gauss(7.0, 1.8), 1)))
                self.conn.execute("INSERT INTO nota VALUES(?,?,?,?)", (nid, m, valor, lancada))

    def _tarefas(self, t: _Turma) -> None:
        for unidade, titulo, descricao in t.comp.tarefas:
            u_ini, u_fim = self.unidades[unidade - 1]
            ini = u_ini + timedelta(days=self.rnd.randint(7, 14))
            fim = min(ini + timedelta(days=self.rnd.choice((10, 14, 21))), u_fim)
            self.conn.execute(
                "INSERT INTO tarefa VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    self._novo_id("tarefa", 600_000),
                    t.id_turma,
                    titulo,
                    descricao,
                    self._dt(ini, 8),
                    self._dt(fim, 23, 59),
                    10.0,
                    float(self.rnd.choice((1, 2, 3))),
                    self._dt(ini, 8),
                ),
            )

    def _noticias(self, t: _Turma, topicos: list) -> None:
        def add(titulo: str, conteudo: str, quando: str) -> None:
            self.conn.execute(
                "INSERT INTO noticia VALUES(?,?,?,?,?)",
                (self._novo_id("noticia", 400_000), t.id_turma, titulo, conteudo, quando),
            )

        (tb, cb), (tm, cm), (tl, cl), (ta, ca) = NOTICIAS_MODELO
        add(tb, cb.format(nome=t.comp.nome), self._dt(self.p.inicio - timedelta(days=1), 10, 15))
        for _, _, titulo, _ in topicos[1::2]:
            row = self.conn.execute(
                "SELECT data_inicio FROM topico_aula WHERE id_turma=? AND titulo=?", (t.id_turma, titulo)
            ).fetchone()
            d = date.fromisoformat(row["data_inicio"]) - timedelta(days=1)
            add(tm, cm.format(topico=titulo), self._dt(d, 19, self.rnd.choice((0, 15, 40))))
        for r in self.conn.execute(
            "SELECT descricao, data, conteudo FROM avaliacao_plano "
            "WHERE id_turma=? AND descricao LIKE 'Prova%'",
            (t.id_turma,),
        ).fetchall():
            d = date.fromisoformat(r["data"])
            add(
                tl.format(avaliacao=r["descricao"]),
                cl.format(avaliacao=r["descricao"], data=d.strftime("%d/%m/%Y"), escopo=r["conteudo"]),
                self._dt(d - timedelta(days=5), 20, 0),
            )
        if self.rnd.random() < 0.6:
            d = self.p.inicio + timedelta(days=self.rnd.randint(20, 60))
            add(ta, ca, self._dt(d, 11, 0))

    def _frequencia(self, t: _Turma, alunos: list[str]) -> None:
        d = self.p.inicio
        linhas = []
        while d <= self.p.fim:
            if d.weekday() in t.dias_semana:
                for m in alunos:
                    faltas = 2 if self.rnd.random() < 0.06 else 0
                    if faltas:
                        linhas.append((t.id_turma, m, d.isoformat(), faltas))
            d += timedelta(days=1)
        self.conn.executemany("INSERT INTO frequencia VALUES(?,?,?,?)", linhas)

    def _eventos(self, t: _Turma) -> None:
        """Alterações que acontecem durante o semestre (o que o agente precisa detectar)."""
        provas = self.conn.execute(
            "SELECT id, descricao, data FROM avaliacao_plano WHERE id_turma=? AND descricao LIKE 'Prova%' "
            "ORDER BY data",
            (t.id_turma,),
        ).fetchall()
        if provas and self.rnd.random() < 0.7:
            pr = self.rnd.choice(provas)
            antiga = date.fromisoformat(pr["data"])
            nova = self._dia_de_aula(t, antiga + timedelta(days=self.rnd.choice((2, 5, 7))))
            quando = self._dt(antiga - timedelta(days=self.rnd.randint(3, 8)), 14, 0)
            self._evento(
                quando,
                "prova_adiada",
                "avaliacao_plano",
                pr["id"],
                {"data": nova.isoformat()},
                f"{t.comp.nome} (T{t.codigo}): {pr['descricao']} adiada de {antiga:%d/%m} para {nova:%d/%m}",
            )
            # o lembrete da prova sai depois do aviso de adiamento e já cita a nova data
            lembrete = max(
                datetime.combine(nova - timedelta(days=5), time(20, 0), tzinfo=self.tz),
                datetime.fromisoformat(quando) + timedelta(days=1),
            )
            self.conn.execute(
                "UPDATE noticia SET conteudo=replace(conteudo, ?, ?), publicado_em=? "
                "WHERE id_turma=? AND titulo=?",
                (
                    f"{antiga:%d/%m/%Y}",
                    f"{nova:%d/%m/%Y}",
                    lembrete.isoformat(),
                    t.id_turma,
                    f"Lembrete: {pr['descricao']}",
                ),
            )
            # notas só podem ser lançadas depois da nova data
            self.conn.execute(
                "UPDATE nota SET lancada_em=? WHERE id_avaliacao IN "
                "(SELECT id FROM avaliacao_nota WHERE id_turma=? AND denominacao=?)",
                (
                    self._dt(nova + timedelta(days=self.rnd.randint(5, 12)), 18, 30),
                    t.id_turma,
                    pr["descricao"],
                ),
            )
            self.conn.execute(
                "INSERT INTO noticia VALUES(?,?,?,?,?)",
                (
                    self._novo_id("noticia", 400_000),
                    t.id_turma,
                    f"{pr['descricao']} adiada",
                    f"Atenção: a {pr['descricao']} foi remarcada para {nova:%d/%m/%Y}, no mesmo horário.",
                    quando,
                ),
            )
        tarefas = self.conn.execute(
            "SELECT id, titulo, fim FROM tarefa WHERE id_turma=?", (t.id_turma,)
        ).fetchall()
        if tarefas and self.rnd.random() < 0.5:
            ta = self.rnd.choice(tarefas)
            fim = datetime.fromisoformat(ta["fim"])
            self._evento(
                self._dt(fim.date() - timedelta(days=2), 17, 0),
                "prazo_prorrogado",
                "tarefa",
                ta["id"],
                {"fim": (fim + timedelta(days=3)).isoformat()},
                f"{t.comp.nome} (T{t.codigo}): prazo de '{ta['titulo']}' prorrogado em 3 dias",
            )
        arq = self.conn.execute(
            "SELECT a.id, a.nome, a.publicado_em, tp.titulo, tp.conteudo FROM arquivo a "
            "JOIN topico_aula tp ON tp.id=a.id_topico WHERE a.id_turma=? ORDER BY a.id LIMIT 1 OFFSET 2",
            (t.id_turma,),
        ).fetchone()
        if arq:
            top = next(x for x in t.comp.topicos if x.titulo == arq["titulo"])
            quando = datetime.fromisoformat(arq["publicado_em"]) + timedelta(days=self.rnd.randint(4, 9))
            self._evento(
                quando.isoformat(),
                "arquivo_substituido",
                "arquivo",
                arq["id"],
                {
                    "conteudo": _nota_de_aula(t.comp, top.titulo, top.conteudo, top.palavras, versao=2),
                    "versao": 2,
                },
                f"{t.comp.nome} (T{t.codigo}): '{arq['nome']}' substituído pela versão 2 (com errata)",
            )

    def _evento(self, quando: str, tipo: str, tabela: str, rid: int, mud: dict, desc: str) -> None:
        self.conn.execute(
            "INSERT INTO evento(aplica_em, tipo, tabela, registro_id, alteracao_json, descricao) "
            "VALUES(?,?,?,?,?,?)",
            (quando, tipo, tabela, rid, json.dumps(mud, ensure_ascii=False), desc),
        )


# ---------------------------------------------------------------------- funções
def _dividir_unidades(inicio: date, fim: date) -> list[tuple[date, date]]:
    total = (fim - inicio).days
    cortes = [inicio + timedelta(days=total * k // 3) for k in range(4)]
    return [(cortes[k], cortes[k + 1] - timedelta(days=1 if k < 2 else 0)) for k in range(3)]


def _unidade_de(unidades: list[tuple[date, date]], d: date) -> int:
    for k, (a, b) in enumerate(unidades, start=1):
        if a <= d <= b:
            return k
    return 3


def _nota_de_aula(
    comp: Componente, titulo: str, conteudo: str, palavras: tuple[str, ...], versao: int
) -> str:
    termos = ", ".join(palavras) or titulo.lower()
    linhas = [
        f"# {comp.nome} — {titulo}",
        "",
        f"*{comp.codigo} · material fictício gerado pelo SIGAA Simulado · versão {versao}*",
        "",
        "## Resumo",
        conteudo,
        "",
        "## Conceitos-chave",
        *(f"- **{p}**: definição, exemplo resolvido e erro comum ao aplicar {p}." for p in palavras),
        "",
        "## Exercícios",
        f"1. Explique com suas palavras: {termos}.",
        f"2. Resolva um exemplo numérico envolvendo {palavras[0] if palavras else titulo.lower()}.",
        f"3. Compare {titulo.lower()} com o tópico anterior da disciplina.",
    ]
    if versao > 1:
        linhas += ["", "## Errata", "O exemplo 2 da versão anterior tinha um sinal trocado; corrigido."]
    return "\n".join(linhas) + "\n"
