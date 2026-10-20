"""Problema da Mochila Booleana (Knapsack 0/1) por Programação Dinâmica.

Problema: dados ``n`` itens, cada um com peso inteiro ``p_i >= 0`` e valor ``v_i >= 0``, e uma
capacidade ``W``, escolher um subconjunto ``S`` que maximize ``sum(v_i, i em S)`` sujeito a
``sum(p_i, i em S) <= W``. Cada item entra no máximo uma vez (daí "0/1": leva ou não leva).

Recorrência (subestrutura ótima), com ``T[i][w]`` = melhor valor usando os ``i`` primeiros
itens e capacidade ``w``::

    T[0][w] = 0
    T[i][w] = T[i-1][w]                                   se p_i > w
    T[i][w] = max(T[i-1][w], T[i-1][w - p_i] + v_i)       caso contrário

A resposta é ``T[n][W]``. Os itens escolhidos são recuperados percorrendo a tabela de trás para
frente: se ``T[i][w] != T[i-1][w]``, o item ``i`` foi usado e ``w -= p_i``.

Complexidade: tempo Θ(n·W) e memória Θ(n·W) (pseudo-polinomial: depende do *valor* de W, não
do seu tamanho em bits). ``valor_maximo`` mostra a versão com memória Θ(W) quando só o valor
importa. ``forca_bruta`` (Θ(2^n·n)) e ``guloso_por_densidade`` existem para validação e
comparação; o guloso **não** é ótimo para a versão 0/1.

No projeto, os "itens" são sessões de estudo candidatas (peso = duração em blocos de minutos,
valor = prioridade do compromisso) e a capacidade é o tempo livre do dia: ver
``sigaa_sim.planner``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any


@dataclass(frozen=True)
class ItemMochila:
    nome: str
    peso: int
    valor: int
    ref: Any = field(default=None, compare=False)  # objeto de domínio associado (opcional)


@dataclass
class ResultadoMochila:
    valor_total: int
    peso_total: int
    capacidade: int
    indices: list[int]  # posições (0-based) dos itens escolhidos, em ordem crescente
    escolhidos: list[ItemMochila]
    tabela: list[list[int]] | None = None  # T[i][w], presente se pedida

    @property
    def folga(self) -> int:
        return self.capacidade - self.peso_total


def _validar(itens: Sequence[ItemMochila], capacidade: int) -> None:
    if not isinstance(capacidade, int) or isinstance(capacidade, bool) or capacidade < 0:
        raise ValueError(f"capacidade deve ser inteiro >= 0 (recebido {capacidade!r})")
    for k, it in enumerate(itens):
        for campo in ("peso", "valor"):
            v = getattr(it, campo)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"item {k} ({it.nome!r}): {campo} deve ser inteiro >= 0 (recebido {v!r})")


def mochila_01(
    itens: Sequence[ItemMochila], capacidade: int, *, guardar_tabela: bool = False
) -> ResultadoMochila:
    """Resolve a mochila 0/1 de forma exata (PD bottom-up + reconstrução).

    Em empates, prefere **não** levar o item (comparação estrita), o que dá preferência a
    soluções que usam os itens de menor índice e torna o resultado determinístico.
    """
    _validar(itens, capacidade)
    n, W = len(itens), capacidade
    # T tem n+1 linhas (linha 0 = nenhum item) e W+1 colunas (capacidades 0..W)
    T = [[0] * (W + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        p, v = itens[i - 1].peso, itens[i - 1].valor
        ant, atual = T[i - 1], T[i]
        for w in range(W + 1):
            sem = ant[w]
            if p <= w and ant[w - p] + v > sem:
                atual[w] = ant[w - p] + v
            else:
                atual[w] = sem

    # reconstrução: de (n, W) até a linha 0
    indices: list[int] = []
    w = W
    for i in range(n, 0, -1):
        if T[i][w] != T[i - 1][w]:
            indices.append(i - 1)
            w -= itens[i - 1].peso
    indices.reverse()
    escolhidos = [itens[k] for k in indices]
    return ResultadoMochila(
        valor_total=T[n][W],
        peso_total=sum(it.peso for it in escolhidos),
        capacidade=W,
        indices=indices,
        escolhidos=escolhidos,
        tabela=T if guardar_tabela else None,
    )


def valor_maximo(itens: Sequence[ItemMochila], capacidade: int) -> int:
    """Apenas o valor ótimo, com memória Θ(W) (um vetor percorrido de trás para frente).

    Percorrer ``w`` em ordem decrescente garante que ``V[w - p]`` ainda é o valor da linha
    anterior, ou seja, que cada item é usado no máximo uma vez.
    """
    _validar(itens, capacidade)
    V = [0] * (capacidade + 1)
    for it in itens:
        for w in range(capacidade, it.peso - 1, -1):
            cand = V[w - it.peso] + it.valor
            if cand > V[w]:
                V[w] = cand
    return V[capacidade]


def forca_bruta(itens: Sequence[ItemMochila], capacidade: int) -> ResultadoMochila:
    """Enumera todos os 2^n subconjuntos. Só para validação (n pequeno)."""
    _validar(itens, capacidade)
    if len(itens) > 22:
        raise ValueError("força bruta limitada a 22 itens")
    melhor: tuple[int, ...] = ()
    melhor_valor = 0
    for r in range(1, len(itens) + 1):
        for comb in combinations(range(len(itens)), r):
            peso = sum(itens[k].peso for k in comb)
            if peso <= capacidade:
                valor = sum(itens[k].valor for k in comb)
                if valor > melhor_valor:
                    melhor, melhor_valor = comb, valor
    escolhidos = [itens[k] for k in melhor]
    return ResultadoMochila(
        melhor_valor, sum(i.peso for i in escolhidos), capacidade, list(melhor), escolhidos
    )


def guloso_por_densidade(itens: Sequence[ItemMochila], capacidade: int) -> ResultadoMochila:
    """Heurística gulosa: ordena por valor/peso e leva enquanto couber. Rápida, mas não ótima."""
    _validar(itens, capacidade)

    def densidade(k: int) -> float:
        p = itens[k].peso
        return float("inf") if p == 0 else itens[k].valor / p

    ordem = sorted(range(len(itens)), key=lambda k: (-densidade(k), k))
    indices, resto = [], capacidade
    for k in ordem:
        if itens[k].peso <= resto:
            indices.append(k)
            resto -= itens[k].peso
    indices.sort()
    escolhidos = [itens[k] for k in indices]
    return ResultadoMochila(
        sum(i.valor for i in escolhidos), capacidade - resto, capacidade, indices, escolhidos
    )


def formatar_tabela(res: ResultadoMochila, itens: Sequence[ItemMochila], max_colunas: int = 24) -> str:
    """Tabela T[i][w] em texto (para aula/apresentação). Corta colunas além de ``max_colunas``."""
    if res.tabela is None:
        raise ValueError("resultado sem tabela; use mochila_01(..., guardar_tabela=True)")
    W = res.capacidade
    cols = list(range(W + 1)) if W + 1 <= max_colunas else list(range(max_colunas - 1)) + [W]
    largura = max(4, len(str(res.valor_total)) + 1)
    rotulos = ["∅"] + [f"{it.nome[:12]} (p={it.peso},v={it.valor})" for it in itens]
    esq = max(len(r) for r in rotulos) + 1
    linhas = [" " * esq + "".join(f"{('w=' + str(w)) if w == cols[0] else w:>{largura}}" for w in cols)]
    for i, rot in enumerate(rotulos):
        marca = "*" if i > 0 and (i - 1) in res.indices else " "
        valores = "".join(f"{res.tabela[i][w]:>{largura}}" for w in cols)
        linhas.append(f"{marca}{rot:<{esq - 1}}{valores}")
    if len(cols) < W + 1:
        linhas.append(f"(colunas {cols[-2] + 1}..{W - 1} omitidas)")
    return "\n".join(linhas)
