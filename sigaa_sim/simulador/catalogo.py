"""Catálogo estático usado pelo gerador: componentes, tópicos, nomes e textos-modelo.

Tudo aqui é fictício. Códigos usam o prefixo ``SIM`` para não se confundirem com componentes
reais; nomes de pessoas são combinações aleatórias de prenomes e sobrenomes comuns.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topico:
    titulo: str
    conteudo: str
    palavras: tuple[str, ...] = ()  # termos usados nos materiais e no escopo das avaliações


@dataclass(frozen=True)
class Componente:
    codigo: str
    nome: str
    carga_horaria: int
    departamento: str
    topicos: tuple[Topico, ...]
    # avaliações por unidade: (abreviação, denominação, nota máxima, peso)
    avaliacoes: tuple[tuple[int, str, str, float, float], ...] = field(default=())
    tarefas: tuple[tuple[int, str, str], ...] = field(default=())  # (unidade, título, descrição)


CURSOS = (
    (1, "Ciências e Tecnologia"),
    (2, "Engenharia de Computação"),
    (3, "Tecnologia da Informação"),
)

DEPARTAMENTOS = ("DSIM", "DMAT-SIM", "DFIS-SIM")

PRENOMES = (
    "Ana",
    "Bruno",
    "Camila",
    "Daniel",
    "Eduarda",
    "Felipe",
    "Gabriela",
    "Heitor",
    "Isabela",
    "João",
    "Larissa",
    "Marcos",
    "Natália",
    "Otávio",
    "Paula",
    "Rafael",
    "Sofia",
    "Tiago",
    "Valéria",
    "Yuri",
    "Beatriz",
    "Caio",
    "Lívia",
    "Mateus",
    "Renata",
    "Samuel",
    "Taís",
    "Vinícius",
    "Alice",
    "Igor",
)
SOBRENOMES = (
    "Almeida",
    "Barbosa",
    "Cavalcanti",
    "Dantas",
    "Esteves",
    "Fagundes",
    "Gurgel",
    "Holanda",
    "Lima",
    "Medeiros",
    "Nóbrega",
    "Oliveira",
    "Pereira",
    "Queiroz",
    "Rocha",
    "Saldanha",
    "Tavares",
    "Uchôa",
    "Vasconcelos",
    "Xavier",
    "Bezerra",
    "Câmara",
    "Freire",
    "Galvão",
    "Macedo",
    "Pinheiro",
    "Souza",
)

SALAS = ("Setor IV - Sala A1", "Setor IV - Sala B3", "Lab. SIM 2", "Auditório SIM", "Setor II - Sala 12")

# Avaliações padrão por unidade (abreviação, denominação, máx., peso)
_PADRAO = (
    (1, "P1", "Prova 1", 10.0, 6.0),
    (1, "T1", "Trabalho 1", 10.0, 4.0),
    (2, "P2", "Prova 2", 10.0, 6.0),
    (2, "T2", "Trabalho 2", 10.0, 4.0),
    (3, "P3", "Prova 3", 10.0, 7.0),
    (3, "T3", "Projeto final", 10.0, 3.0),
)


def _t(titulo: str, conteudo: str, *palavras: str) -> Topico:
    return Topico(titulo, conteudo, palavras)


COMPONENTES: tuple[Componente, ...] = (
    Componente(
        "SIM0301",
        "Programação Avançada",
        60,
        "DSIM",
        (
            _t(
                "Revisão de C++ e ferramentas",
                "Compilação separada, make, depuração com gdb, uso de git.",
                "compilação",
                "depuração",
                "git",
            ),
            _t(
                "Ponteiros e gerenciamento de memória",
                "Alocação dinâmica, RAII, smart pointers, valgrind.",
                "ponteiros",
                "memória",
                "RAII",
            ),
            _t(
                "Programação modular",
                "Separação em módulos, interfaces, acoplamento e coesão.",
                "módulos",
                "interfaces",
                "coesão",
            ),
            _t(
                "Templates e STL",
                "Programação genérica, containers, iteradores e algoritmos da STL.",
                "templates",
                "STL",
                "iteradores",
            ),
            _t(
                "Análise de complexidade",
                "Notação O, melhor/pior caso, análise amortizada.",
                "complexidade",
                "assintótica",
                "amortizada",
            ),
            _t(
                "Programação dinâmica",
                "Subestrutura ótima, memoização, tabelas bottom-up, mochila 0/1.",
                "programação dinâmica",
                "mochila",
                "memoização",
            ),
            _t(
                "Algoritmos em grafos",
                "BFS, DFS, Dijkstra, árvores geradoras mínimas.",
                "grafos",
                "Dijkstra",
                "BFS",
            ),
            _t(
                "Engenharia de requisitos",
                "Painel MVP, estórias de usuário, casos de uso e protótipos.",
                "requisitos",
                "MVP",
                "casos de uso",
            ),
        ),
        _PADRAO,
        (
            (
                1,
                "Lista de ponteiros",
                "Implementar uma lista encadeada genérica sem vazamentos (valgrind limpo).",
            ),
            (2, "Implementação do algoritmo", "Implementar e validar o algoritmo sorteado, com testes."),
            (2, "Documento de requisitos", "Painel MVP, estórias, protótipos e diagrama de casos de uso."),
            (3, "Entrega do projeto", "Sistema funcional com relatório e apresentação."),
        ),
    ),
    Componente(
        "SIM0205",
        "Cálculo III",
        90,
        "DMAT-SIM",
        (
            _t(
                "Funções de várias variáveis",
                "Domínio, curvas de nível, limites e continuidade.",
                "várias variáveis",
                "curvas de nível",
                "limites",
            ),
            _t(
                "Derivadas parciais",
                "Derivadas parciais, plano tangente, diferenciabilidade.",
                "derivadas parciais",
                "plano tangente",
            ),
            _t(
                "Regra da cadeia e gradiente",
                "Regra da cadeia, derivada direcional e vetor gradiente.",
                "regra da cadeia",
                "gradiente",
                "direcional",
            ),
            _t(
                "Máximos e mínimos",
                "Pontos críticos, teste da segunda derivada, multiplicadores de Lagrange.",
                "máximos",
                "Lagrange",
                "pontos críticos",
            ),
            _t(
                "Integrais duplas",
                "Integrais iteradas, regiões gerais, coordenadas polares.",
                "integrais duplas",
                "polares",
                "iteradas",
            ),
            _t(
                "Integrais triplas",
                "Coordenadas cilíndricas e esféricas, mudança de variáveis e jacobiano.",
                "integrais triplas",
                "jacobiano",
                "esféricas",
            ),
            _t(
                "Campos vetoriais",
                "Integrais de linha, campos conservativos, Teorema de Green.",
                "integrais de linha",
                "Green",
                "conservativos",
            ),
        ),
        _PADRAO[:4] + ((3, "P3", "Prova 3", 10.0, 10.0),),
        (
            (1, "Lista 1", "Exercícios de derivadas parciais e plano tangente."),
            (2, "Lista 2", "Integrais duplas e triplas com mudança de variáveis."),
        ),
    ),
    Componente(
        "SIM0207",
        "Física II",
        90,
        "DFIS-SIM",
        (
            _t(
                "Carga elétrica e Lei de Coulomb",
                "Quantização e conservação da carga; superposição.",
                "carga elétrica",
                "Coulomb",
                "superposição",
            ),
            _t(
                "Campo elétrico",
                "Campo de cargas pontuais e distribuições contínuas; dipolo.",
                "campo elétrico",
                "dipolo",
            ),
            _t(
                "Lei de Gauss",
                "Fluxo elétrico; simetrias plana, cilíndrica e esférica.",
                "Gauss",
                "fluxo elétrico",
                "simetria",
            ),
            _t(
                "Potencial elétrico",
                "Energia potencial; superfícies equipotenciais; V a partir de E.",
                "potencial elétrico",
                "equipotenciais",
            ),
            _t(
                "Capacitância",
                "Capacitores, associação, energia armazenada e dielétricos.",
                "capacitância",
                "capacitores",
                "dielétricos",
            ),
            _t(
                "Corrente e resistência",
                "Lei de Ohm, resistividade, potência e circuitos de corrente contínua.",
                "corrente",
                "Ohm",
                "resistência",
            ),
            _t(
                "Campo magnético",
                "Força de Lorentz, Lei de Biot-Savart e Lei de Ampère.",
                "campo magnético",
                "Ampère",
                "Lorentz",
            ),
        ),
        _PADRAO[:4] + ((3, "P3", "Prova 3", 10.0, 10.0),),
        (
            (1, "Lista 1", "Exercícios de Coulomb, campo e Gauss."),
            (2, "Relatório de laboratório", "Relatório do experimento de circuitos RC."),
        ),
    ),
    Componente(
        "SIM0110",
        "Álgebra Linear",
        60,
        "DMAT-SIM",
        (
            _t(
                "Sistemas lineares",
                "Eliminação de Gauss, escalonamento, posto.",
                "sistemas lineares",
                "escalonamento",
                "posto",
            ),
            _t(
                "Espaços vetoriais",
                "Subespaços, combinação linear, base e dimensão.",
                "espaços vetoriais",
                "base",
                "dimensão",
            ),
            _t(
                "Transformações lineares",
                "Núcleo, imagem, matriz de uma transformação.",
                "transformações lineares",
                "núcleo",
                "imagem",
            ),
            _t(
                "Autovalores e autovetores",
                "Polinômio característico, diagonalização.",
                "autovalores",
                "autovetores",
                "diagonalização",
            ),
            _t(
                "Produto interno",
                "Ortogonalidade, Gram-Schmidt, projeções e mínimos quadrados.",
                "produto interno",
                "Gram-Schmidt",
                "mínimos quadrados",
            ),
        ),
        (
            (1, "P1", "Prova 1", 10.0, 10.0),
            (2, "P2", "Prova 2", 10.0, 10.0),
            (3, "P3", "Prova 3", 10.0, 10.0),
        ),
        ((1, "Lista de sistemas", "Resolver os sistemas da Lista 1 por escalonamento."),),
    ),
    Componente(
        "SIM0220",
        "Estruturas de Dados",
        60,
        "DSIM",
        (
            _t(
                "Listas, pilhas e filas",
                "Implementações sequenciais e encadeadas; análise de custo.",
                "listas",
                "pilhas",
                "filas",
            ),
            _t(
                "Tabelas hash",
                "Funções de dispersão, encadeamento e endereçamento aberto.",
                "hash",
                "encadeamento",
                "dispersão",
            ),
            _t(
                "Árvores binárias de busca",
                "Inserção, remoção, percursos.",
                "árvores",
                "busca binária",
                "percursos",
            ),
            _t(
                "Árvores balanceadas",
                "AVL e rubro-negra: rotações e invariantes.",
                "AVL",
                "rubro-negra",
                "rotações",
            ),
            _t(
                "Heaps e filas de prioridade",
                "Heap binário, heapsort.",
                "heap",
                "fila de prioridade",
                "heapsort",
            ),
            _t(
                "Ordenação",
                "Mergesort, quicksort, limites inferiores.",
                "ordenação",
                "quicksort",
                "mergesort",
            ),
        ),
        _PADRAO,
        (
            (1, "TAD Lista", "Implementar TAD lista com testes unitários."),
            (2, "Tabela hash", "Implementar tabela hash com redimensionamento."),
            (3, "Árvore AVL", "Implementar AVL com verificação de invariantes."),
        ),
    ),
    Componente(
        "SIM0140",
        "Probabilidade e Estatística",
        60,
        "DMAT-SIM",
        (
            _t(
                "Estatística descritiva",
                "Medidas de tendência central e dispersão; gráficos.",
                "média",
                "variância",
                "descritiva",
            ),
            _t(
                "Probabilidade",
                "Axiomas, probabilidade condicional, Teorema de Bayes.",
                "probabilidade",
                "Bayes",
                "condicional",
            ),
            _t(
                "Variáveis aleatórias discretas",
                "Binomial, Poisson, esperança e variância.",
                "binomial",
                "Poisson",
                "esperança",
            ),
            _t(
                "Variáveis aleatórias contínuas",
                "Normal, exponencial, padronização.",
                "normal",
                "exponencial",
                "contínuas",
            ),
            _t(
                "Inferência",
                "Intervalos de confiança e testes de hipótese.",
                "intervalo de confiança",
                "hipótese",
                "inferência",
            ),
        ),
        (
            (1, "P1", "Prova 1", 10.0, 10.0),
            (2, "P2", "Prova 2", 10.0, 10.0),
            (3, "P3", "Prova 3", 10.0, 10.0),
        ),
        ((2, "Trabalho de dados", "Análise descritiva de um conjunto de dados fornecido."),),
    ),
)

NOTICIAS_MODELO = (
    (
        "Boas-vindas e plano de curso",
        "Sejam bem-vindos(as) a {nome}. O plano de curso e o cronograma estão disponíveis na turma virtual. "
        "Leiam com atenção as datas das avaliações.",
    ),
    (
        "Material da semana disponível",
        "Os slides e as notas de aula sobre {topico} já estão em Arquivos. "
        "Recomendo estudar antes da próxima aula.",
    ),
    (
        "Lembrete: {avaliacao}",
        "Lembrem que a {avaliacao} acontece em {data}. Conteúdo: {escopo}. Tragam documento com foto.",
    ),
    ("Atendimento extra", "Haverá atendimento de dúvidas na sala do professor nesta semana, das 14h às 16h."),
)
