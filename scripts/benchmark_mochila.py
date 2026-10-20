"""Benchmark empírico da Mochila 0/1: tempo × n·W (deve crescer linearmente no produto).

Uso: .venv/bin/python scripts/benchmark_mochila.py [--repeticoes 3]
Imprime uma tabela Markdown com o tempo mediano e o custo por célula da tabela T[i][w].
"""

from __future__ import annotations

import argparse
import random
import statistics
import time

from sigaa_sim.algoritmos.mochila import ItemMochila, forca_bruta, mochila_01


def instancia(n: int, semente: int) -> list[ItemMochila]:
    rnd = random.Random(semente)
    return [ItemMochila(f"i{k}", rnd.randint(1, 50), rnd.randint(1, 100)) for k in range(n)]


def medir(fn, *args, repeticoes: int) -> float:
    tempos = []
    for _ in range(repeticoes):
        t0 = time.perf_counter()
        fn(*args)
        tempos.append(time.perf_counter() - t0)
    return statistics.median(tempos)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeticoes", type=int, default=3)
    a = ap.parse_args()

    print("## Programação dinâmica — Θ(n·W)\n")
    print("| n | W | células n·W | tempo (ms) | ns/célula |")
    print("|---:|---:|---:|---:|---:|")
    for n in (25, 50, 100, 200):
        for W in (250, 500, 1000, 2000):
            t = medir(mochila_01, instancia(n, n * W), W, repeticoes=a.repeticoes)
            print(f"| {n} | {W} | {n * W:,} | {t * 1000:.1f} | {t * 1e9 / (n * W):.0f} |")

    print("\n## Força bruta — Θ(2^n·n) (W = 100)\n")
    print("| n | subconjuntos | força bruta (ms) | PD (ms) |")
    print("|---:|---:|---:|---:|")
    for n in (8, 12, 16, 18, 20):
        its = instancia(n, n)
        tb = medir(forca_bruta, its, 100, repeticoes=1)
        tp = medir(mochila_01, its, 100, repeticoes=a.repeticoes)
        print(f"| {n} | {2**n:,} | {tb * 1000:.1f} | {tp * 1000:.2f} |")


if __name__ == "__main__":
    main()
