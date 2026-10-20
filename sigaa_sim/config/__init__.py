"""Configuração: preferências em TOML, segredos em .env / variáveis de ambiente.

Somente as seções necessárias ao comando executado são validadas
(``require_telegram``, ``require_groq``...). Os perfis simulado/demo nunca exigem segredos.
"""

from __future__ import annotations

import os
import tomllib
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Configuração ausente ou inválida para o comando solicitado."""


class GeneralConfig(BaseModel):
    timezone: str = "America/Fortaleza"
    account_label: str = "default"
    data_dir: Path = Path("data")
    log_level: str = "INFO"
    log_retention_days: int = 30

    @field_validator("timezone")
    @classmethod
    def _tz_valid(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"fuso horário desconhecido: {v}") from e
        return v


class SigaaConfig(BaseModel):
    """Fonte dos dados acadêmicos. Só existem fontes simuladas: nenhum portal real é acessado."""

    institution: str = "sim"
    profile: str = "simulado"  # simulado | demo
    # banco do SIGAA Simulado (relativo ao data_dir quando não for absoluto)
    sim_db: Path = Path("sigaa_simulado.db")
    demo_fixture: str = "baseline"

    @field_validator("profile")
    @classmethod
    def _profile_valid(cls, v: str) -> str:
        if v not in ("simulado", "demo"):
            raise ValueError(f"perfil desconhecido: {v} (use 'simulado' ou 'demo')")
        return v


class CollectionConfig(BaseModel):
    removal_after_missing_collections: int = 3


class TelegramConfig(BaseModel):
    enabled: bool = False
    mode: str = "demo"  # demo | http
    reminder_days: list[int] = Field(default_factory=lambda: [7, 3, 1, 0])
    quiet_hours_start: time | None = time(22, 0)
    quiet_hours_end: time | None = time(7, 0)
    daily_summary_enabled: bool = False
    daily_summary_time: time = time(7, 30)
    max_attempts: int = 5


class GroqConfig(BaseModel):
    enabled: bool = False
    model: str = "openai/gpt-oss-20b"
    structured_mode: str = "strict"  # strict | best_effort | json_object
    temperature: float = 0.2
    # "low" | "medium" | "high" (gpt-oss) — menos raciocínio = menos tokens no TPM de 8K do free tier
    reasoning_effort: str | None = "low"
    max_output_tokens: int = 2000
    max_input_chars: int = 7000
    max_calls_per_run: int = 8
    max_tokens_per_run: int = 40000
    max_calls_per_day: int = 200
    max_tokens_per_day: int = 150000
    request_timeout_seconds: int = 60
    max_wait_seconds: int = 20


class KnowledgeConfig(BaseModel):
    max_file_mb: int = 25
    max_documents_per_run: int = 5
    download_timeout_seconds: int = 60
    revalidate_every_days: int = 7
    chunk_chars: int = 1800
    chunk_overlap_chars: int = 200
    keep_old_versions: int = 2


class PlannerConfig(BaseModel):
    availability_minutes: dict[str, int] = Field(
        default_factory=lambda: {"0": 90, "1": 90, "2": 60, "3": 90, "4": 60, "5": 120, "6": 0}
    )
    preferred_session_minutes: int = 35
    min_session_minutes: int = 15
    w_deadline: float = 6.0
    w_weight: float = 1.5
    w_frequency: float = 0.5
    w_progress: float = 1.0
    horizon_days: int = 21

    def minutes_for_weekday(self, weekday: int) -> int:
        return int(self.availability_minutes.get(str(weekday), 0))


class Secrets(BaseSettings):
    """Segredos vêm apenas do ambiente / .env. Nunca são logados."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_user_id: str = ""
    # Token opcional exigido pela interface web (header X-Web-Token ou cookie).
    web_token: str = ""

    def __repr__(self) -> str:  # evita vazamento acidental em logs
        present = [k for k, v in self.model_dump().items() if v]
        return f"Secrets(present={present})"


class Settings(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    sigaa: SigaaConfig = Field(default_factory=SigaaConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    # nome oficial da disciplina -> nome curto de exibição (complementa o mapa padrão)
    display_names: dict[str, str] = Field(default_factory=dict)
    secrets: Secrets = Field(default_factory=Secrets, exclude=True, repr=False)
    config_path: Path | None = Field(default=None, exclude=True)

    # --- caminhos derivados ---
    @property
    def data_dir(self) -> Path:
        return self.general.data_dir

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sigaa_sim.db"

    @property
    def materials_dir(self) -> Path:
        return self.data_dir / "materials"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def sim_db_path(self) -> Path:
        p = self.sigaa.sim_db
        return p if p.is_absolute() else self.data_dir / p

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.general.timezone)

    # --- validação por comando ---
    def require_telegram(self) -> None:
        if not self.telegram.enabled:
            raise ConfigError("Telegram desativado ([telegram].enabled = false).")
        if self.telegram.mode == "http":
            missing = [k for k in ("telegram_bot_token", "telegram_chat_id") if not getattr(self.secrets, k)]
            if missing:
                raise ConfigError(
                    "Telegram em modo http exige no .env: " + ", ".join(m.upper() for m in missing)
                )

    def require_groq(self) -> None:
        if not self.groq.enabled:
            raise ConfigError("Groq desativado ([groq].enabled = false).")
        if not self.secrets.groq_api_key:
            raise ConfigError("Groq ativado mas GROQ_API_KEY ausente no .env.")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.materials_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings(config_path: Path | None = None, *, env_file: Path | None = None) -> Settings:
    """Carrega config.toml (se existir) e segredos do ambiente.

    Um config.toml ausente resulta nos defaults (perfil simulado, integrações desligadas).
    """
    path = config_path or Path(os.environ.get("SIGAA_AGENT_CONFIG", "config.toml"))
    data: dict = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"config inválido em {path}: {e}") from e
    secrets = Secrets(_env_file=str(env_file)) if env_file else Secrets()
    try:
        settings = Settings(**data, secrets=secrets, config_path=path if path.exists() else None)
    except ValueError as e:
        raise ConfigError(f"config inválido: {e}") from e
    return settings
