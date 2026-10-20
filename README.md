# SIGAA Simulado — agente de estudo com Mochila 0/1

Projeto da disciplina **Programação Avançada (UFRN, 2026.2)**, II Unidade.
Aluno: Victor Manuel Valentim Cordeiro · Algoritmo: **nº 5 — Mochila Booleana (Knapsack 0/1)**.

O projeto tem duas partes:

1. **SIGAA Simulado.** Um banco SQLite **fictício** e reprodutível (gerado por semente), que
   imita as estruturas do SIGAA: turmas, notícias, plano de curso, tarefas, notas, faltas,
   tópicos de aula e arquivos. Ele evolui com um **relógio simulado**: provas são adiadas,
   notas lançadas e materiais trocados.
2. **Agente de estudo.** Sincroniza com o SIGAA Simulado, detecta novidades e alterações e
   monta o **plano de estudo do dia** resolvendo uma **Mochila 0/1**: as sessões candidatas
   são os itens e o tempo livre do dia é a capacidade.

> Não há integração com o SIGAA real: nada de login, rede ou dados pessoais. Todos os nomes,
> códigos e notas são gerados.

![Painel local (`sigaa-sim web`) sobre o SIGAA Simulado](docs/img/painel.png)

| Documento | Conteúdo |
| --- | --- |
| [docs/ALGORITMO.md](docs/ALGORITMO.md) | Parte I: características e aplicações, entrada, implementação e validação da Mochila 0/1 |
| [docs/REQUISITOS.md](docs/REQUISITOS.md) | Parte II: Painel MVP, estórias, protótipos, casos de uso e especificações |
| [docs/SIMULADOR.md](docs/SIMULADOR.md) | modelo de dados do SIGAA Simulado, tempo simulado, consultas SQL |
| [docs/DECISIONS.md](docs/DECISIONS.md) | decisões técnicas e limitações |

## Instalação

```bash
git clone https://github.com/alphachief13/sigaa-simulado && cd sigaa-simulado
uv venv --python 3.12 .venv                               # ou: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"      # ou: .venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
```

## Roteiro rápido (5 minutos)

```bash
mkdir -p ~/lab-sigaa && cd ~/lab-sigaa
sigaa-sim init                         # config.toml + banco do agente
sigaa-sim simulador gerar              # SIGAA fictício (semente 2026, relógio em 19/10/2026 09:00)
sigaa-sim simulador info               # turmas do discente logado
sigaa-sim sync                         # 1ª coleta (baseline)
sigaa-sim plan                         # plano do dia (Mochila 0/1)
sigaa-sim mochila --comparar           # itens, escolha ótima, guloso e força bruta

sigaa-sim simulador eventos            # o que vai acontecer
sigaa-sim simulador avancar --dias 10  # o tempo passa: prazos mudam, notas saem
sigaa-sim sync                         # o agente detecta as mudanças
sigaa-sim status -v
sigaa-sim plan                         # replaneja porque os compromissos mudaram
```

A instância didática da mochila, com a tabela de PD completa:

```bash
sigaa-sim mochila --exemplo --tabela --comparar
```

```
Mochila 0/1 (PD): valor 9, peso 7/7 u
  ✔ [1] B  (peso 3, valor 4)
  ✔ [2] C  (peso 4, valor 5)

Tabela T[i][w] (* = item escolhido na reconstrução):
             w=0   1   2   3   4   5   6   7
 ∅             0   0   0   0   0   0   0   0
 A (p=1,v=1)   0   1   1   1   1   1   1   1
*B (p=3,v=4)   0   1   1   4   5   5   5   5
*C (p=4,v=5)   0   1   1   4   5   6   6   9
 D (p=5,v=7)   0   1   1   4   5   7   8   9

Guloso por densidade: valor 8, peso 6/7
Força bruta (2^4 subconjuntos): valor 9 — igual ao da PD
O guloso perde 11.1% do valor ótimo nesta instância.
```

## Comandos

| Comando | O que faz |
| --- | --- |
| `simulador gerar [--semente N] [--discentes N] [--relogio AAAA-MM-DD] [--sobrescrever]` | cria o banco fictício |
| `simulador info` / `simulador eventos [--todos]` | contagens, turmas, linha do tempo |
| `simulador avancar [--dias N] [--horas N] [--ate AAAA-MM-DD[THH:MM]]` | avança o relógio e aplica eventos |
| `simulador sessao [--expirar\|--renovar]` | simula a sessão expirada |
| `sync [--no-process]` | coleta, detecta mudanças, baixa e extrai materiais |
| `plan [--date] [--new] [--no-ai]` | plano do dia |
| `mochila [--date] [--minutos N] [--tabela] [--comparar] [--exemplo]` | mostra o algoritmo sobre os dados |
| `progress --task N --status done\|skipped [--difficulty] [--note]` | registra progresso |
| `search termo…` | busca textual nos materiais |
| `status [-v]` / `doctor` | saúde, pendências e configuração |
| `web` | painel local em http://127.0.0.1:8765 |
| `demo` | fluxo completo com as fixtures JSON mínimas (sem banco simulado) |

Disponibilidade por dia da semana, pesos da prioridade e horizonte ficam em `config.toml`
(`[planner]`; ver `config.example.toml`).

## Estrutura

```
sigaa_sim/
  algoritmos/mochila.py   Mochila 0/1: PD Θ(n·W), variante Θ(W), força bruta, guloso
  simulador/              SIGAA Simulado: esquema SQL, catálogo, gerador, relógio e eventos
  sigaa/simulado.py       adapter que lê o banco simulado no formato do portal
  sigaa/demo.py           adapter de fixtures JSON (testes)
  planner/                prioridade, candidatas, seleção pela mochila, plano validado
  sync.py, storage/       coleta, detecção de mudanças, persistência
  knowledge/              download/extração/busca de materiais
  telegram/, llm/, web/   opcionais: notificações, IA, painel local
tests/                    389 testes (pytest), sem rede
scripts/benchmark_mochila.py
```

## Testes e qualidade

```bash
pytest                                      # 389 testes, ~4 s
ruff check . && ruff format --check .
python scripts/benchmark_mochila.py         # tempo × n·W
```

- `tests/test_mochila.py`: 319 casos. A PD é comparada com a força bruta em 300 instâncias
  aleatórias; também cobre bordas, entradas inválidas e o contraexemplo do guloso.
- `tests/test_simulador.py`: determinismo por semente, integridade referencial, visibilidade
  pelo relógio, eventos, sessão expirada e plano dentro do tempo.
- Demais testes: detecção de mudanças, notificações, materiais e planejador (fixtures demo).

## Opcionais

Telegram (notificações e comandos) e Groq (IA para interpretar avisos e sugerir a sequência do
plano) ficam **desligados** por padrão. Para ativar, coloque as chaves no `.env` (modelo em
`.env.example`) e `enabled = true` em `config.toml`. Sem eles, tudo funciona: o plano básico é
o da Mochila 0/1.
