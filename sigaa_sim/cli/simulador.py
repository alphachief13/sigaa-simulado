"""Subcomandos do SIGAA Simulado (``sigaa-sim simulador ...``) e da Mochila 0/1 (``sigaa-sim mochila``)."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta

from sigaa_sim.config import Settings
from sigaa_sim.simulador import banco


def _parse_instante(txt: str, settings: Settings) -> datetime:
    """Aceita ``AAAA-MM-DD`` (vira 09:00) ou ``AAAA-MM-DDTHH:MM``; sem fuso usa o configurado."""
    dt = datetime.fromisoformat(txt) if "T" in txt else datetime.combine(date.fromisoformat(txt), time(9, 0))
    return dt if dt.tzinfo else dt.replace(tzinfo=settings.tz)


# ------------------------------------------------------------------- simulador
def cmd_gerar(args, settings: Settings) -> int:
    from sigaa_sim.simulador.gerador import Parametros, gerar

    p = Parametros(
        semente=args.semente,
        discentes=args.discentes,
        timezone=settings.general.timezone,
        relogio=_parse_instante(args.relogio, settings) if args.relogio else None,
    )
    settings.ensure_dirs()
    res = gerar(settings.sim_db_path, p, sobrescrever=args.sobrescrever)
    print(f"SIGAA Simulado gerado em {settings.sim_db_path} (semente {p.semente}; dados fictícios)")
    _imprimir_resumo(res)
    return 0


def _imprimir_resumo(res: dict) -> None:
    for k, v in res.items():
        print(f"  {k:<18} {v}")


def cmd_info(args, settings: Settings) -> int:
    conn = banco.abrir_existente(settings.sim_db_path)
    try:
        _imprimir_resumo(banco.resumo(conn))
        m = banco.get_meta(conn, "discente_logado")
        row = conn.execute("SELECT nome, matricula FROM discente WHERE matricula=?", (m,)).fetchone()
        print(f"\ndiscente logado: {row['nome']} ({row['matricula']})")
        for t in conn.execute(
            """SELECT t.id_turma, c.codigo, c.nome, t.codigo_turma, t.horario, d.nome docente
               FROM matricula_turma mt JOIN turma t ON t.id_turma=mt.id_turma
               JOIN componente c ON c.codigo=t.componente JOIN docente d ON d.siape=t.siape
               WHERE mt.matricula=? ORDER BY c.nome""",
            (m,),
        ):
            print(
                f"  {t['id_turma']}  {t['codigo']} T{t['codigo_turma']}  {t['nome']:<28} "
                f"{t['horario']:<8} {t['docente']}"
            )
    finally:
        conn.close()
    return 0


def cmd_avancar(args, settings: Settings) -> int:
    conn = banco.abrir_existente(settings.sim_db_path)
    try:
        atual = banco.relogio(conn)
        if args.ate:
            alvo = _parse_instante(args.ate, settings)
        else:
            alvo = atual + timedelta(days=args.dias, hours=args.horas)
        aplicados = banco.avancar(conn, alvo)
    except banco.SimuladorError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(f"relógio: {atual:%d/%m/%Y %H:%M} → {alvo:%d/%m/%Y %H:%M}")
    print(f"eventos aplicados: {len(aplicados)}")
    for ev in aplicados:
        print(f"  [{ev.tipo}] {ev.descricao}")
    print("execute `sigaa-sim sync` para o agente perceber as mudanças")
    return 0


def cmd_eventos(args, settings: Settings) -> int:
    conn = banco.abrir_existente(settings.sim_db_path)
    try:
        filtro = "" if args.todos else "WHERE aplicado_em IS NULL"
        for ev in conn.execute(f"SELECT * FROM evento {filtro} ORDER BY aplica_em, id"):
            quando = datetime.fromisoformat(ev["aplica_em"])
            st = "aplicado" if ev["aplicado_em"] else "pendente"
            print(f"{quando:%d/%m %H:%M}  {st:<8}  [{ev['tipo']}] {ev['descricao']}")
    finally:
        conn.close()
    return 0


def cmd_sessao(args, settings: Settings) -> int:
    conn = banco.abrir_existente(settings.sim_db_path)
    try:
        if args.expirar or args.renovar:
            banco.definir_sessao(conn, expirada=bool(args.expirar))
        print("sessão simulada:", "expirada" if banco.sessao_expirada(conn) else "ok")
    finally:
        conn.close()
    return 0


# --------------------------------------------------------------------- mochila
def cmd_mochila(args, settings: Settings) -> int:
    from sigaa_sim.algoritmos.mochila import (
        ItemMochila,
        forca_bruta,
        formatar_tabela,
        guloso_por_densidade,
        mochila_01,
    )

    if args.exemplo:
        # instância clássica em que o guloso por densidade falha
        itens = [
            ItemMochila("A", 1, 1),
            ItemMochila("B", 3, 4),
            ItemMochila("C", 4, 5),
            ItemMochila("D", 5, 7),
        ]
        cap = args.capacidade if args.capacidade is not None else 7
        unidade = "u"
    else:
        from sigaa_sim.cli.main import _open
        from sigaa_sim.planner import GRANULARIDADE, available_minutes, candidatas, collect_commitments
        from sigaa_sim.sigaa import make_clock

        conn = _open(settings)
        clock = make_clock(settings)
        day = date.fromisoformat(args.date) if args.date else clock.today()
        minutes = args.minutos if args.minutos is not None else available_minutes(settings, day)
        commitments = collect_commitments(conn, settings, day)
        conn.close()
        if not commitments:
            print(f"nenhum compromisso no horizonte a partir de {day:%d/%m/%Y}; rode `sigaa-sim sync` antes")
            return 1
        itens = candidatas(commitments, minutes, settings)
        cap = minutes // GRANULARIDADE
        unidade = f"×{GRANULARIDADE}min"
        print(
            f"dia {day:%d/%m/%Y}: {minutes} min livres = capacidade {cap} ({unidade}); "
            f"{len(itens)} candidatas"
        )

    res = mochila_01(itens, cap, guardar_tabela=args.tabela)
    print(f"\nMochila 0/1 (PD): valor {res.valor_total}, peso {res.peso_total}/{cap} {unidade}")
    for k in res.indices:
        it = itens[k]
        print(f"  ✔ [{k}] {it.nome}  (peso {it.peso}, valor {it.valor})")
    if args.tabela:
        print("\nTabela T[i][w] (* = item escolhido na reconstrução):")
        print(formatar_tabela(res, itens))
    if args.comparar:
        g = guloso_por_densidade(itens, cap)
        print(f"\nGuloso por densidade: valor {g.valor_total}, peso {g.peso_total}/{cap}")
        for k in g.indices:
            print(f"  · [{k}] {itens[k].nome}")
        if len(itens) <= 20:
            b = forca_bruta(itens, cap)
            ok = "igual" if b.valor_total == res.valor_total else "DIFERENTE"
            print(f"Força bruta (2^{len(itens)} subconjuntos): valor {b.valor_total} — {ok} ao da PD")
        if g.valor_total < res.valor_total:
            perda = 100 * (res.valor_total - g.valor_total) / res.valor_total
            print(f"O guloso perde {perda:.1f}% do valor ótimo nesta instância.")
    return 0


def register(sub) -> None:
    s = sub.add_parser("simulador", help="gera e controla o SIGAA Simulado (banco fictício)")
    ss = s.add_subparsers(dest="acao", required=True)

    g = ss.add_parser("gerar", help="cria o banco simulado a partir de uma semente")
    g.add_argument("--semente", type=int, default=2026)
    g.add_argument("--discentes", type=int, default=40)
    g.add_argument("--relogio", help="instante inicial (AAAA-MM-DD[THH:MM]); padrão: 9 semanas após o início")
    g.add_argument("--sobrescrever", action="store_true", help="apaga o banco simulado existente")
    g.set_defaults(fn=cmd_gerar)

    ss.add_parser("info", help="contagens, relógio e turmas do discente logado").set_defaults(fn=cmd_info)

    a = ss.add_parser("avancar", help="avança o relógio simulado e aplica os eventos vencidos")
    a.add_argument("--dias", type=int, default=1)
    a.add_argument("--horas", type=int, default=0)
    a.add_argument("--ate", help="instante alvo (AAAA-MM-DD[THH:MM])")
    a.set_defaults(fn=cmd_avancar)

    e = ss.add_parser("eventos", help="linha do tempo de alterações (pendentes por padrão)")
    e.add_argument("--todos", action="store_true", help="inclui os já aplicados")
    e.set_defaults(fn=cmd_eventos)

    x = ss.add_parser("sessao", help="simula expiração/renovação da sessão")
    grp = x.add_mutually_exclusive_group()
    grp.add_argument("--expirar", action="store_true")
    grp.add_argument("--renovar", action="store_true")
    x.set_defaults(fn=cmd_sessao)

    m = sub.add_parser("mochila", help="mostra a Mochila 0/1 que monta o plano de estudo")
    m.add_argument("--date", help="AAAA-MM-DD (padrão: hoje no relógio simulado)")
    m.add_argument("--minutos", type=int, help="tempo livre (padrão: disponibilidade do dia)")
    m.add_argument("--tabela", action="store_true", help="imprime a tabela T[i][w]")
    m.add_argument("--comparar", action="store_true", help="compara com guloso e força bruta")
    m.add_argument("--exemplo", action="store_true", help="instância didática (4 itens) em vez dos dados")
    m.add_argument("--capacidade", type=int, help="capacidade da instância didática (padrão 7)")
    m.set_defaults(fn=cmd_mochila)
