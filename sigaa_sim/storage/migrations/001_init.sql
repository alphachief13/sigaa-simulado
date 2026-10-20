-- 001: esquema inicial
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    term TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    professor TEXT,
    url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id),
    kind TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    current_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    -- data de publicação na fonte (ISO), data da 1a coleta, última coleta, última atualização local
    source_published TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    missing_count INTEGER NOT NULL DEFAULT 0,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_course_kind ON items(course_id, kind);
CREATE INDEX IF NOT EXISTS idx_items_removed ON items(removed_at);

CREATE TABLE IF NOT EXISTS item_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    sync_run_id INTEGER,
    UNIQUE(item_id, revision)
);

CREATE TABLE IF NOT EXISTS change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    kind TEXT NOT NULL,
    event_type TEXT NOT NULL,
    old_json TEXT,
    new_json TEXT,
    changed_fields TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'sigaa',
    detected_at TEXT NOT NULL,
    sync_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_item ON change_events(item_id);
CREATE INDEX IF NOT EXISTS idx_events_detected ON change_events(detected_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,           -- running | success | partial | failed
    adapter TEXT NOT NULL,
    baseline INTEGER NOT NULL DEFAULT 0,
    stats_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS section_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    section TEXT NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    evidence TEXT,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sections_run ON section_collections(sync_run_id);

-- Outbox de notificações (Telegram). dedupe_key evita duplicar lembretes/alertas.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed
    urgent INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT NOT NULL,
    message_id TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_attempt_at);

-- Trabalhos pendentes (download, extração, resumo, interpretação...).
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,     -- menor = mais urgente
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | deferred
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, next_attempt_at, priority);

-- Materiais (arquivos) e versões.
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | downloaded | invalid | too_large | failed | unsupported
    current_file_hash TEXT,
    etag TEXT,
    last_modified TEXT,
    last_checked_at TEXT,
    error TEXT,
    UNIQUE(item_id)
);

CREATE TABLE IF NOT EXISTS material_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    file_hash TEXT NOT NULL,
    local_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    downloaded_at TEXT NOT NULL,
    superseded_at TEXT,
    UNIQUE(material_id, file_hash)
);

-- Documentos derivados por hash de bytes (cache compartilhado entre versões/materiais).
CREATE TABLE IF NOT EXISTS documents (
    file_hash TEXT PRIMARY KEY,
    extractor_version TEXT NOT NULL,
    status TEXT NOT NULL,          -- extracted | no_text | unsupported | failed
    kind TEXT,                     -- pdf | pptx | txt | md | html
    page_count INTEGER,
    char_count INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL REFERENCES documents(file_hash),
    seq INTEGER NOT NULL,
    ref TEXT,                      -- "p.3" | "slide 5" | "seção X"
    text TEXT NOT NULL,
    chunker_version TEXT NOT NULL,
    UNIQUE(file_hash, seq)
);

-- Resumos/interpretações da LLM com chave de cache.
CREATE TABLE IF NOT EXISTS llm_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,
    task TEXT NOT NULL,
    scope_id TEXT NOT NULL,        -- file_hash | item_id | course_id | plan date
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_outputs_scope ON llm_outputs(task, scope_id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,          -- ok | error | rate_limited | invalid_output
    error TEXT,
    sync_run_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);

-- Planejamento e progresso.
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    version INTEGER NOT NULL,
    source TEXT NOT NULL,          -- llm | fallback
    available_minutes INTEGER NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_date, version)
);

CREATE TABLE IF NOT EXISTS study_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    seq INTEGER NOT NULL,
    objective TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    priority REAL NOT NULL,
    json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | in_progress | done | skipped
    difficulty TEXT,                          -- easy | ok | hard
    note TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON study_tasks(plan_id);

CREATE TABLE IF NOT EXISTS progress_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES study_tasks(id),
    course_id TEXT REFERENCES courses(id),
    status TEXT NOT NULL,
    minutes INTEGER,
    difficulty TEXT,
    note TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id INTEGER PRIMARY KEY,
    from_user_id TEXT,
    chat_id TEXT,
    command TEXT,
    authorized INTEGER NOT NULL,
    processed_at TEXT NOT NULL
);
