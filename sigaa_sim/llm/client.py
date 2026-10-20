"""Cliente LLM isolado: orçamento local, cache por chave, saída estruturada validada.

Implementações: ``GroqClient`` (SDK oficial, sem retries do SDK: controlamos aqui),
``FakeGroqClient`` (testes/demo, em ``fake.py``) e ``DisabledLLM``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.llm.prompts import PROMPT_VERSION, SYSTEM_BASE
from sigaa_sim.llm.schemas import SCHEMA_VERSION, strict_json_schema

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """IA indisponível agora (orçamento, 429 longo, desativada). ``retry_seconds`` sugere o adiamento."""

    def __init__(self, reason: str, retry_seconds: int = 3600):
        super().__init__(reason)
        self.retry_seconds = retry_seconds


class LLMInvalidOutput(Exception):
    """Resposta não validou mesmo após a correção controlada."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False


class LLM(Protocol):
    enabled: bool

    def complete(
        self, task: str, scope_id: str, user_prompt: str, schema: type[T], *, cache_extra: str = ""
    ) -> tuple[T, bool]:
        """Retorna (objeto validado, veio_do_cache). Lança LLMUnavailable/LLMInvalidOutput."""
        ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)  # conservador para pt-BR


class Budget:
    """Orçamento local por execução e por dia, com base em ``llm_calls``."""

    def __init__(self, conn: sqlite3.Connection, settings: Settings, clock: Clock):
        self.conn, self.s, self.clock = conn, settings.groq, clock
        self.run_calls = 0
        self.run_tokens = 0

    def _today_usage(self) -> tuple[int, int]:
        start = to_utc_iso(self.clock.now().replace(hour=0, minute=0, second=0, microsecond=0))
        row = self.conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total_tokens),0) t FROM llm_calls WHERE created_at>=?",
            (start,),
        ).fetchone()
        return int(row["c"]), int(row["t"])

    def check(self, est_tokens: int) -> None:
        if self.run_calls >= self.s.max_calls_per_run:
            raise LLMUnavailable("limite de chamadas por execução atingido", 1800)
        if self.run_tokens + est_tokens > self.s.max_tokens_per_run:
            raise LLMUnavailable("limite de tokens por execução atingido", 1800)
        calls, tokens = self._today_usage()
        if calls >= self.s.max_calls_per_day or tokens + est_tokens > self.s.max_tokens_per_day:
            secs = int((self._tomorrow() - self.clock.now()).total_seconds()) + 60
            raise LLMUnavailable("orçamento diário de IA atingido", max(secs, 600))
        wait_until = self.conn.execute("SELECT value FROM meta WHERE key='llm_rate_limited_until'").fetchone()
        if wait_until and wait_until["value"] > self.clock.now_utc_iso():
            from sigaa_sim.clock import from_utc_iso

            secs = int((from_utc_iso(wait_until["value"]) - self.clock.now()).total_seconds())
            raise LLMUnavailable("aguardando fim do rate limit", max(secs, 60))

    def _tomorrow(self):
        n = self.clock.now()
        return (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def record(self, task: str, model: str, usage: Usage, status: str, error: str | None = None) -> None:
        self.run_calls += 1
        self.run_tokens += usage.total_tokens
        self.conn.execute(
            """INSERT INTO llm_calls(task, model, prompt_tokens, completion_tokens, total_tokens,
                   estimated, status, error, created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                task,
                model,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                int(usage.estimated),
                status,
                (error or "")[:300] or None,
                self.clock.now_utc_iso(),
            ),
        )

    def set_rate_limited(self, seconds: int) -> None:
        until = to_utc_iso(self.clock.now() + timedelta(seconds=seconds))
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES('llm_rate_limited_until',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (until,),
        )


class OutputCache:
    def __init__(self, conn: sqlite3.Connection, clock: Clock):
        self.conn, self.clock = conn, clock

    @staticmethod
    def key(task: str, scope_id: str, model: str, prompt: str, schema_name: str, extra: str) -> str:
        raw = "|".join(
            [
                task,
                scope_id,
                model,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                schema_name,
                extra,
                hashlib.sha256(prompt.encode()).hexdigest(),
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, cache_key: str) -> dict | None:
        row = self.conn.execute("SELECT json FROM llm_outputs WHERE cache_key=?", (cache_key,)).fetchone()
        return json.loads(row["json"]) if row else None

    def put(self, cache_key: str, task: str, scope_id: str, model: str, data: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO llm_outputs(cache_key, task, scope_id, model, prompt_version,
                   schema_version, json, created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                cache_key,
                task,
                scope_id,
                model,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                json.dumps(data, ensure_ascii=False),
                self.clock.now_utc_iso(),
            ),
        )

    def latest(self, task: str, scope_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT json FROM llm_outputs WHERE task=? AND scope_id=? ORDER BY id DESC LIMIT 1",
            (task, scope_id),
        ).fetchone()
        return json.loads(row["json"]) if row else None


class BaseLLM:
    """Lógica comum: cache → orçamento → chamada → validação → 1 correção → registro."""

    enabled = True

    def __init__(self, conn: sqlite3.Connection, settings: Settings, clock: Clock):
        self.conn, self.settings, self.clock = conn, settings, clock
        self.model = settings.groq.model
        self.budget = Budget(conn, settings, clock)
        self.cache = OutputCache(conn, clock)

    def _raw_call(self, messages: list[dict], schema: type[BaseModel]) -> tuple[str, Usage]:
        raise NotImplementedError

    def complete(
        self, task: str, scope_id: str, user_prompt: str, schema: type[T], *, cache_extra: str = ""
    ) -> tuple[T, bool]:
        user_prompt = user_prompt[: self.settings.groq.max_input_chars]
        key = self.cache.key(task, scope_id, self.model, user_prompt, schema.__name__, cache_extra)
        cached = self.cache.get(key)
        if cached is not None:
            return schema.model_validate(cached), True

        est = estimate_tokens(SYSTEM_BASE + user_prompt) + self.settings.groq.max_output_tokens
        self.budget.check(est)
        messages = [{"role": "system", "content": SYSTEM_BASE}, {"role": "user", "content": user_prompt}]
        text, usage = self._call_recording(task, messages, schema)
        try:
            obj = _validate(text, schema)
        except (ValidationError, ValueError) as e:
            # uma única correção controlada
            log.info("saída inválida em %s; tentando 1 correção", task)
            fix_messages = messages + [
                {"role": "assistant", "content": text[:4000]},
                {
                    "role": "user",
                    "content": "A resposta anterior não segue o schema JSON exigido: "
                    f"{str(e)[:500]}. Responda novamente APENAS com o JSON válido.",
                },
            ]
            self.budget.check(est)
            text2, _ = self._call_recording(task, fix_messages, schema)
            try:
                obj = _validate(text2, schema)
            except (ValidationError, ValueError) as e2:
                self.budget.record(task, self.model, Usage(), "invalid_output", str(e2))
                raise LLMInvalidOutput(f"{task}: {str(e2)[:200]}") from e2
        self.cache.put(key, task, scope_id, self.model, obj.model_dump(mode="json"))
        return obj, False

    def _call_recording(self, task: str, messages: list[dict], schema: type[BaseModel]) -> tuple[str, Usage]:
        try:
            text, usage = self._raw_call(messages, schema)
        except LLMUnavailable as e:
            self.budget.record(task, self.model, Usage(), "rate_limited", str(e))
            raise
        except Exception as e:
            self.budget.record(task, self.model, Usage(), "error", f"{type(e).__name__}: {e}")
            raise LLMUnavailable(f"erro na API: {type(e).__name__}", 900) from e
        self.budget.record(task, self.model, usage, "ok")
        return text, usage


def _validate(text: str, schema: type[T]) -> T:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return schema.model_validate_json(text)


class GroqClient(BaseLLM):
    def __init__(self, conn, settings: Settings, clock: Clock):
        super().__init__(conn, settings, clock)
        settings.require_groq()
        from groq import Groq

        # max_retries=0: evitamos retentativas em camadas; backoff/adiamento é nosso.
        self._client = Groq(
            api_key=settings.secrets.groq_api_key,
            max_retries=0,
            timeout=settings.groq.request_timeout_seconds,
        )

    def _response_format(self, schema: type[BaseModel]) -> dict:
        mode = self.settings.groq.structured_mode
        if mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__.lower(),
                "strict": mode == "strict",
                "schema": strict_json_schema(schema),
            },
        }

    def _create(self, messages: list[dict], schema: type[BaseModel], extra: dict):
        g = self.settings.groq
        return self._client.chat.completions.with_raw_response.create(
            model=self.model,
            messages=messages,
            temperature=g.temperature,
            max_completion_tokens=g.max_output_tokens,
            response_format=self._response_format(schema),
            **extra,
        )

    def _raw_call(self, messages: list[dict], schema: type[BaseModel]) -> tuple[str, Usage]:
        import groq

        g = self.settings.groq
        extra: dict = {}
        if g.reasoning_effort and self.model.startswith("openai/gpt-oss"):
            extra["reasoning_effort"] = g.reasoning_effort
        if g.structured_mode == "json_object":
            messages = messages[:-1] + [
                {
                    "role": "user",
                    "content": messages[-1]["content"]
                    + "\n\nResponda com JSON seguindo este schema:\n"
                    + json.dumps(strict_json_schema(schema), ensure_ascii=False),
                }
            ]
        try:
            raw = self._create(messages, schema, extra)
        except groq.RateLimitError as e:
            retry = _retry_after(e.response.headers if e.response is not None else {})
            if retry > g.max_wait_seconds:
                self.budget.set_rate_limited(retry)
                raise LLMUnavailable(f"429; Retry-After={retry}s", retry) from e
            import time

            time.sleep(retry)
            try:
                raw = self._create(messages, schema, extra)
            except groq.RateLimitError as e2:
                retry2 = max(_retry_after(e2.response.headers if e2.response is not None else {}), 60)
                self.budget.set_rate_limited(retry2)
                raise LLMUnavailable(f"429 persistente; aguardar {retry2}s", retry2) from e2
        except groq.APIStatusError as e:
            msg = str(e).lower()
            if e.status_code == 400 and ("json" in msg or "schema" in msg):
                # A geração/validação de JSON falhou no servidor (acontece de forma intermitente,
                # mesmo em strict mode). Uma nova tentativa imediata costuma resolver.
                log.info("groq: falha de JSON no servidor; repetindo uma vez")
                try:
                    raw = self._create(messages, schema, extra)
                except groq.APIStatusError as e2:
                    raise ValueError(f"API rejeitou a saída: {str(e2)[:200]}") from e2
            else:
                raise
        remaining = raw.headers.get("x-ratelimit-remaining-tokens")
        if remaining is not None:
            log.debug("groq tokens restantes (TPM): %s", remaining)
            try:
                if int(float(remaining)) < g.max_output_tokens + 1500:
                    # perto do limite por minuto: espera o reset em vez de provocar um 429
                    reset = raw.headers.get("x-ratelimit-reset-tokens", "10s")
                    secs = _parse_reset(reset)
                    if secs <= max(g.max_wait_seconds, 65):  # janela de TPM é de 1 minuto
                        import time

                        log.info("groq: perto do limite de tokens/min; aguardando %ss", secs)
                        time.sleep(secs)
                    else:
                        self.budget.set_rate_limited(secs)
            except (TypeError, ValueError):
                pass
        completion = raw.parse()
        content = completion.choices[0].message.content or ""
        u = completion.usage
        usage = (
            Usage(u.prompt_tokens, u.completion_tokens, u.total_tokens)
            if u
            else Usage(
                estimate_tokens(json.dumps(messages)),
                estimate_tokens(content),
                estimate_tokens(json.dumps(messages)) + estimate_tokens(content),
                estimated=True,
            )
        )
        return content, usage


def _parse_reset(text: str) -> int:
    """Converte '7.66s', '2m59.56s', '1h2m' em segundos (formato dos headers x-ratelimit-reset-*)."""
    import re

    total = 0.0
    for num, unit in re.findall(r"([\d.]+)([hms])", text or ""):
        total += float(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return int(total) + 1


def _retry_after(headers) -> int:
    try:
        return int(float(headers.get("retry-after", "5")))
    except (TypeError, ValueError):
        return 5


class DisabledLLM:
    enabled = False

    def complete(self, task, scope_id, user_prompt, schema, *, cache_extra=""):
        raise LLMUnavailable("IA desativada na configuração", 24 * 3600)


def make_llm(settings: Settings, conn: sqlite3.Connection, clock: Clock) -> LLM:
    if not settings.groq.enabled:
        return DisabledLLM()
    return GroqClient(conn, settings, clock)
