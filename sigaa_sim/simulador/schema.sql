-- SIGAA Simulado: esquema relacional que espelha as entidades observadas no SIGAA (UFRN).
-- Todos os dados são FICTÍCIOS e gerados por semente (sigaa_sim.simulador.gerador).
--
-- Visibilidade temporal: cada registro tem um instante a partir do qual "existe" no portal
-- (publicado_em / lancada_em). O adapter só enxerga o que já foi publicado segundo o relógio
-- simulado (tabela sim_meta, chave 'relogio'). Alterações posteriores (prova adiada, arquivo
-- substituído...) ficam na tabela evento e são aplicadas quando o relógio avança.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sim_meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curso (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,                 -- ex.: "Ciências e Tecnologia"
    nivel TEXT NOT NULL DEFAULT 'G'     -- G = graduação
);

CREATE TABLE IF NOT EXISTS discente (
    matricula TEXT PRIMARY KEY,         -- 11 dígitos: ano(4) + período(1) + sequencial
    nome TEXT NOT NULL,
    curso_id INTEGER NOT NULL REFERENCES curso(id),
    ingresso TEXT NOT NULL,             -- período de ingresso, ex.: "2025.1"
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS docente (
    siape TEXT PRIMARY KEY,             -- 7 dígitos
    nome TEXT NOT NULL,
    departamento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS componente (
    codigo TEXT PRIMARY KEY,            -- ex.: "SIM0301"
    nome TEXT NOT NULL,
    carga_horaria INTEGER NOT NULL,     -- horas
    departamento TEXT NOT NULL
);

-- Turma = oferta de um componente em um período. id_turma é o ID numérico estável do SIGAA.
CREATE TABLE IF NOT EXISTS turma (
    id_turma INTEGER PRIMARY KEY,
    componente TEXT NOT NULL REFERENCES componente(codigo),
    codigo_turma TEXT NOT NULL,         -- "01", "02"...
    periodo TEXT NOT NULL,              -- "2026.2"
    siape TEXT NOT NULL REFERENCES docente(siape),
    horario TEXT NOT NULL,              -- notação SIGAA: dias(2=seg..7=sáb) + turno(M/T/N) + aulas, ex.: "24T12"
    local TEXT NOT NULL,
    capacidade INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS matricula_turma (
    matricula TEXT NOT NULL REFERENCES discente(matricula),
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    situacao TEXT NOT NULL DEFAULT 'MATRICULADO',
    PRIMARY KEY (matricula, id_turma)
);

-- Notícias da turma virtual (menu "Notícias", /ava/NoticiaTurma/listar.jsf).
CREATE TABLE IF NOT EXISTS noticia (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    titulo TEXT NOT NULL,
    conteudo TEXT NOT NULL,
    publicado_em TEXT NOT NULL          -- ISO 8601 com fuso
);

-- Tópicos de aula da página principal da turma (div.topico-aula).
CREATE TABLE IF NOT EXISTS topico_aula (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    titulo TEXT NOT NULL,
    conteudo TEXT NOT NULL,
    data_inicio TEXT NOT NULL,          -- AAAA-MM-DD
    data_fim TEXT NOT NULL,
    publicado_em TEXT NOT NULL
);

-- Avaliações do Plano de Curso (#listaAvaliacoes): data + descrição. O SIGAA não exibe peso aqui.
CREATE TABLE IF NOT EXISTS avaliacao_plano (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    descricao TEXT NOT NULL,
    data TEXT NOT NULL,                 -- AAAA-MM-DD
    unidade INTEGER NOT NULL,
    conteudo TEXT,                      -- escopo (o agente usa para priorizar)
    peso REAL                           -- conhecido pelo simulador; exposto como no relatório de notas
);

-- Tarefas (painel "Minhas atividades" do portal).
CREATE TABLE IF NOT EXISTS tarefa (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    titulo TEXT NOT NULL,
    descricao TEXT NOT NULL,
    inicio TEXT NOT NULL,               -- ISO com fuso
    fim TEXT NOT NULL,                  -- ISO com fuso (prazo de envio)
    nota_maxima REAL NOT NULL DEFAULT 10,
    peso REAL NOT NULL DEFAULT 1,
    publicado_em TEXT NOT NULL
);

-- Colunas do relatório de notas (th#aval_<id> com hidden denAval/notaAval/pesoAval).
CREATE TABLE IF NOT EXISTS avaliacao_nota (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    unidade INTEGER NOT NULL,
    abreviacao TEXT NOT NULL,           -- rótulo da coluna, ex.: "P1"
    denominacao TEXT NOT NULL,          -- denAval
    nota_maxima REAL NOT NULL,          -- notaAval
    peso REAL NOT NULL                  -- pesoAval
);

CREATE TABLE IF NOT EXISTS nota (
    id_avaliacao INTEGER NOT NULL REFERENCES avaliacao_nota(id),
    matricula TEXT NOT NULL REFERENCES discente(matricula),
    valor REAL NOT NULL,
    lancada_em TEXT NOT NULL,
    PRIMARY KEY (id_avaliacao, matricula)
);

CREATE TABLE IF NOT EXISTS frequencia (
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    matricula TEXT NOT NULL REFERENCES discente(matricula),
    data TEXT NOT NULL,
    faltas INTEGER NOT NULL,            -- aulas perdidas no dia (0..2)
    PRIMARY KEY (id_turma, matricula, data)
);

-- Arquivos da turma (menu "Arquivos"). O conteúdo fica no próprio banco.
CREATE TABLE IF NOT EXISTS arquivo (
    id INTEGER PRIMARY KEY,
    id_turma INTEGER NOT NULL REFERENCES turma(id_turma),
    id_topico INTEGER REFERENCES topico_aula(id),
    nome TEXT NOT NULL,
    descricao TEXT,
    content_type TEXT NOT NULL,
    conteudo TEXT NOT NULL,             -- texto (markdown/plain); arquivos gerados são textuais
    versao INTEGER NOT NULL DEFAULT 1,
    publicado_em TEXT NOT NULL
);

-- Linha do tempo de alterações: aplicadas em ordem quando o relógio passa de aplica_em.
CREATE TABLE IF NOT EXISTS evento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aplica_em TEXT NOT NULL,
    tipo TEXT NOT NULL,                 -- prova_adiada | prazo_prorrogado | arquivo_substituido | ...
    tabela TEXT NOT NULL,
    registro_id INTEGER NOT NULL,
    alteracao_json TEXT NOT NULL,       -- {"coluna": novo_valor, ...}
    descricao TEXT NOT NULL,
    aplicado_em TEXT
);
CREATE INDEX IF NOT EXISTS idx_evento_pendente ON evento(aplicado_em, aplica_em);
