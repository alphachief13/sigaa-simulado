# Decisões técnicas e limitações conhecidas

## Decisões

- **Python 3.12** fixado (`requires-python >=3.12,<3.13`): versão suportada por todas as
  dependências (groq 1.7, pydantic 2.13). O sistema tem 3.14, mas evitamos
  correr atrás de compatibilidade de wheels em hardware fraco.
- **Monólito por módulo**, sem framework de agentes, filas externas ou banco vetorial. Pendências
  ficam em tabelas SQLite (`jobs`, `outbox`).
- **Identidade × conteúdo**: `items.id` = `instituição:conta:período:turma:tipo:source_id`.
  Sem `source_id`, hash de (turma, tipo, título normalizado, publicação) — renomear cria outro
  item e o antigo é marcado removido após N coletas completas (limitação documentada).
  `content_hash` = SHA-256 do JSON canônico só dos campos relevantes por tipo.
- **Comparação contra o estado atual, não contra hashes históricos**: por isso A→B→A→B gera três
  eventos. Cada alteração grava uma linha em `item_revisions` e um `change_event` com
  antigo/novo/campos.
- **Remoção conservadora**: só após `removal_after_missing_collections` coletas *completas*
  consecutivas sem o item. Coletas parciais/falhas não contam.
- **Seção vazia sem evidência = falha**: um adapter precisa apontar `empty_marker`/`evidence`
  para que 0 registros seja aceito como "vazio". Protege contra parser quebrado e tela de login.
- **Datas**: sem hora → `date`; com hora → datetime com fuso do usuário; armazenamento em UTC ISO
  `Z`. Nunca inventamos 23:59. Texto ambíguo vira pendência com o trecho original.
- **Notificação factual antes da IA**: a outbox é entregue antes de qualquer job; jobs de IA
  adiam (`Defer`) em vez de falhar quando não há orçamento.
- **Cache da LLM** por `(tarefa, escopo, modelo, versão do prompt, versão do schema, hash do
  prompt)`. Documentos são cacheados pelo hash dos bytes: o mesmo arquivo em duas turmas é
  extraído/resumido uma vez.
- **Groq**: modelo padrão `openai/gpt-oss-20b` (suporta `strict: true`; free tier 30 RPM /
  1K RPD / 8K TPM / 200K TPD conforme docs em 2026-09-29). `max_retries=0` no SDK para não
  multiplicar tentativas; `Retry-After` curto → espera; longo → grava `llm_rate_limited_until`
  e adia. Uma única correção de JSON inválido.
- **Prioridade explicável**: `w_deadline/(1+dias) + w_weight*peso/10 + w_frequency*freq/5 +
  w_progress*pendência`, pesos em `[planner]`. Frequência conta materiais distintos por hash,
  não chunks.
- **Plano validado antes de salvar**: soma ≤ disponível, sessão ≥ mínimo, refs ∈ materiais
  conhecidos, disciplina conhecida. Rejeitado → plano básico determinístico com nota.
- **Telegram** em texto simples (sem parse_mode) para evitar escaping; dedupe por chave; entrega
  confirmada por mensagem em transação própria.
- **Trava de execução** por arquivo com PID; travas órfãs (PID morto ou >3h) são removidas.
- **WAL** habilitado no SQLite (leituras não bloqueiam a coleta).

- **Sem integração com o SIGAA real.** Os adapters de portal (Playwright/CAS), login, sessões
  salvas e credenciais foram removidos. A única fonte de dados é o **SIGAA Simulado**
  (`sigaa_sim/simulador`), um banco SQLite gerado por semente que reproduz as entidades e os
  formatos observados no portal (ver `docs/SIMULADOR.md`). O contrato `SigaaAdapter` foi mantido,
  então parser, detecção de mudanças, notificações e planejador não sabem que os dados são
  sintéticos.
- **Relógio simulado único**: no perfil `simulado`, o relógio do agente (`SimClock`) lê o
  relógio do banco simulado. Prazos, lembretes e o plano do dia usam o mesmo "agora" que o portal
  fictício; avançar o tempo (`simulador avancar`) aplica a linha do tempo de eventos.
- **Eventos com colunas permitidas**: um evento só altera colunas listadas em
  `COLUNAS_ALTERAVEIS`; o relógio nunca volta (eventos aplicados não são desfeitos).
- **Mochila 0/1 no plano básico**: as sessões do dia são escolhidas por programação dinâmica
  (`sigaa_sim/algoritmos/mochila.py`). Pesos em blocos de 5 min reduzem `W` (90 min → 18) e o
  custo Θ(n·W). Sessões repetidas da mesma disciplina têm valor decrescente (×0,6) para evitar
  que um único compromisso consuma o dia todo; uma revisão curta vale 30% da prioridade.
- **`w_deadline` = 6** (era 3): com 3, uma entrega em 2 dias perdia para uma prova de peso alto
  em 16 dias.

## Limitações conhecidas

- **Dados sintéticos**: o simulador cobre notícias, tarefas, plano de curso, notas, frequência,
  tópicos e arquivos. Fóruns, enquetes e chats não são simulados. Arquivos são textuais
  (Markdown), então a extração de PDF/PPTX só é exercitada pelas fixtures do perfil `demo`.
- **Mochila e prioridade**: a mochila é ótima para os valores que recebe; a qualidade do plano
  depende da fórmula de prioridade (heurística explicável, não aprendida).
- **Entrega "pelo menos uma vez"** no Telegram: se a API receber a mensagem e a conexão cair
  antes da confirmação, ela pode ser reenviada.
- **Sem OCR**: PDF escaneado/imagem recebe `no_text` e a razão; nenhum processamento pesado
  é ativado. DOCX e `.ppt` antigo ficam `unsupported`.
- **Orçamento local é estimativa**: não conhece o consumo de outras aplicações na mesma conta
  Groq; os headers `x-ratelimit-*` são lidos mas apenas logados em DEBUG.
- **Fórmulas/figuras**: texto extraído pode estar incompleto; o resumo carrega
  `missing_or_unclear` e a referência à página/slide original.
- **`_topic_frequency`** usa `LIKE` sobre termos do escopo (≥5 letras): é um indício, não uma
  medida de relevância pedagógica.
- **Comandos do Telegram** exigem `mode = "http"` e um processo de long polling separado
  (`telegram-poll`); não há webhook/servidor público.
- **`validate_plan`** não modela dependências entre tarefas nem múltiplos dias; replaneja
  quando prazos/disponibilidade mudam, mas não distribui carga entre dias automaticamente.
