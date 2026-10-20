"""Validação da Mochila 0/1: casos conhecidos, bordas, propriedades e comparação com força bruta."""

from __future__ import annotations

import random

import pytest

from sigaa_sim.algoritmos.mochila import (
    ItemMochila,
    forca_bruta,
    formatar_tabela,
    guloso_por_densidade,
    mochila_01,
    valor_maximo,
)


def itens(*pv: tuple[int, int]) -> list[ItemMochila]:
    return [ItemMochila(f"i{k}", p, v) for k, (p, v) in enumerate(pv)]


# ------------------------------------------------------------- casos conhecidos
def test_exemplo_classico():
    # instância de livro-texto: W=50 → leva os itens 2 e 3 (valor 220)
    its = itens((10, 60), (20, 100), (30, 120))
    r = mochila_01(its, 50)
    assert r.valor_total == 220 and r.indices == [1, 2] and r.peso_total == 50


def test_guloso_falha_e_pd_acerta():
    its = itens((1, 1), (3, 4), (4, 5), (5, 7))
    assert mochila_01(its, 7).valor_total == 9  # B + C
    assert guloso_por_densidade(its, 7).valor_total == 8  # D + A (maior densidade primeiro)


def test_tabela_tem_dimensoes_n_mais_1_por_w_mais_1_e_resposta_no_canto():
    its = itens((2, 3), (3, 4), (4, 5), (5, 6))
    r = mochila_01(its, 5, guardar_tabela=True)
    assert len(r.tabela) == 5 and all(len(linha) == 6 for linha in r.tabela)
    assert r.tabela[-1][-1] == r.valor_total == 7
    assert all(v == 0 for v in r.tabela[0])  # linha 0: nenhum item
    texto = formatar_tabela(r, its)
    assert "w=0" in texto and texto.count("*") == len(r.indices)


# ---------------------------------------------------------------------- bordas
@pytest.mark.parametrize(
    "its,cap,valor",
    [
        ([], 10, 0),  # sem itens
        (itens((1, 5)), 0, 0),  # capacidade zero
        (itens((11, 5), (20, 9)), 10, 0),  # nenhum cabe
        (itens((0, 5), (0, 3), (4, 1)), 0, 8),  # peso zero sempre entra
        (itens((5, 0), (5, 10)), 5, 10),  # valor zero nunca é preferido
        (itens((10, 7)), 10, 7),  # cabe exatamente
    ],
)
def test_casos_de_borda(its, cap, valor):
    r = mochila_01(its, cap)
    assert r.valor_total == valor == valor_maximo(its, cap) == forca_bruta(its, cap).valor_total
    assert r.peso_total <= cap


@pytest.mark.parametrize(
    "its,cap",
    [
        (itens((1, 1)), -1),
        (itens((-1, 1)), 5),
        (itens((1, -1)), 5),
        ([ItemMochila("x", 1.5, 1)], 5),
        (itens((1, 1)), 2.0),
        (itens((1, 1)), True),
    ],
)
def test_entradas_invalidas_sao_rejeitadas(its, cap):
    with pytest.raises(ValueError):
        mochila_01(its, cap)


def test_empate_e_deterministico_e_prefere_menores_indices():
    its = itens((2, 5), (2, 5), (2, 5))
    a, b = mochila_01(its, 4), mochila_01(its, 4)
    assert a.indices == b.indices == [0, 1]


def test_cada_item_usado_no_maximo_uma_vez():
    # se a PD permitisse repetição, levaria 3x o item (1, 10) com W=3
    its = itens((1, 10), (3, 12))
    r = mochila_01(its, 3)
    assert r.valor_total == 12 and r.indices == [1]
    assert valor_maximo(its, 3) == 12


# ---------------------------------------------- propriedades (aleatório × bruta)
@pytest.mark.parametrize("semente", range(300))
def test_pd_igual_a_forca_bruta_em_instancias_aleatorias(semente):
    rnd = random.Random(semente)
    n = rnd.randint(0, 12)
    its = itens(*[(rnd.randint(0, 15), rnd.randint(0, 40)) for _ in range(n)])
    cap = rnd.randint(0, 40)
    r = mochila_01(its, cap)
    b = forca_bruta(its, cap)
    assert r.valor_total == b.valor_total
    # reconstrução consistente: os escolhidos somam o valor ótimo e cabem
    assert sum(it.valor for it in r.escolhidos) == r.valor_total
    assert sum(it.peso for it in r.escolhidos) == r.peso_total <= cap
    assert len(set(r.indices)) == len(r.indices) == len(r.escolhidos)
    # versão com memória Θ(W) chega ao mesmo valor
    assert valor_maximo(its, cap) == r.valor_total
    # o guloso é viável mas nunca melhor que o ótimo
    g = guloso_por_densidade(its, cap)
    assert g.peso_total <= cap and g.valor_total <= r.valor_total


def test_monotonia_em_relacao_a_capacidade():
    rnd = random.Random(7)
    its = itens(*[(rnd.randint(1, 9), rnd.randint(1, 30)) for _ in range(15)])
    valores = [mochila_01(its, w).valor_total for w in range(60)]
    assert valores == sorted(valores)


def test_instancia_grande_roda_rapido():
    rnd = random.Random(1)
    its = itens(*[(rnd.randint(1, 50), rnd.randint(1, 100)) for _ in range(200)])
    r = mochila_01(its, 1000)  # 200 × 1001 ≈ 2·10^5 células
    assert r.peso_total <= 1000 and r.valor_total == valor_maximo(its, 1000)
