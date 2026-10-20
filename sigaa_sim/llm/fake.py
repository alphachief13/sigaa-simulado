"""LLM simulada: respostas determinísticas derivadas do prompt (sem rede).

Também suporta scripts de falha para testes: ``fail_with`` (exceção) ou ``raw_responses``
(textos brutos, para exercitar JSON inválido e a correção controlada).
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

from sigaa_sim.llm.client import BaseLLM, Usage, estimate_tokens
from sigaa_sim.llm.schemas import (
    AnnouncementInterpretation,
    DocumentSummary,
    PlanSuggestion,
)

T = TypeVar("T", bound=BaseModel)


class FakeGroqClient(BaseLLM):
    """Sem banco: usa ``complete`` simplificado (sem cache/orçamento) — para demo/planner.
    Quando construído com ``conn`` (via ``FakeGroqClient.with_db``), herda cache e orçamento."""

    enabled = True

    def __init__(self, conn=None, settings=None, clock=None):
        self.calls: list[tuple[str, str]] = []
        self.raw_responses: list[str] = []
        self.fail_with: Exception | None = None
        self.model = "fake-model"
        if conn is not None:
            super().__init__(conn, settings, clock)
        else:
            self.conn = None

    @classmethod
    def with_db(cls, conn, settings, clock) -> FakeGroqClient:
        return cls(conn, settings, clock)

    def complete(
        self, task: str, scope_id: str, user_prompt: str, schema: type[T], *, cache_extra: str = ""
    ) -> tuple[T, bool]:
        if self.conn is not None:
            return super().complete(task, scope_id, user_prompt, schema, cache_extra=cache_extra)
        self.calls.append((task, scope_id))
        text, _ = self._raw_call([{"role": "user", "content": user_prompt}], schema)
        return schema.model_validate_json(text), False

    def _raw_call(self, messages: list[dict], schema: type[BaseModel]) -> tuple[str, Usage]:
        if self.fail_with is not None:
            raise self.fail_with
        prompt = messages[-1]["content"]
        self.calls.append((schema.__name__, prompt[:40]))
        if self.raw_responses:
            text = self.raw_responses.pop(0)
        else:
            text = json.dumps(_fabricate(prompt, schema), ensure_ascii=False)
        n = estimate_tokens(prompt)
        return text, Usage(n, estimate_tokens(text), n + estimate_tokens(text))


def _fabricate(prompt: str, schema: type[BaseModel]) -> dict:
    if schema is DocumentSummary:
        refs = re.findall(r"\[(p\.\d+(?:-\d+)?|slide[s]? [\d-]+|seção: [^\]]+|texto)\]", prompt)
        refs = list(dict.fromkeys(refs))[:4] or ["p.1"]
        topics = re.findall(r"(?:Cap\.\s*\d+:\s*|# +|## +)([^\n\.]{5,60})", prompt)[:4]
        if not topics:
            topics = [w for w in re.findall(r"[A-ZÁ-Ú][a-zá-ú]+ [a-zá-ú]+", prompt)][:3] or [
                "conteúdo do material"
            ]
        return {
            "summary": "Resumo simulado (FakeGroqClient): o material cobre "
            + "; ".join(topics[:3])
            + ". Baseado apenas no texto extraído.",
            "topics": [{"topic": t.strip(), "refs": refs[: 1 + i % len(refs)]} for i, t in enumerate(topics)],
            "prerequisites": ["[inferência] noções básicas do capítulo anterior"],
            "exercises_refs": [r for r in refs if "Lista" in prompt or "exerc" in prompt.lower()][:1],
            "missing_or_unclear": ["fórmulas podem estar incompletas no texto extraído"],
            "confidence": "medium",
        }
    if schema is AnnouncementInterpretation:
        body = prompt.split("=== AVISO ===")[-1]
        dates = re.findall(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", body)
        rel_words = ("próxima aula", "proxima aula", "próxima semana", "proxima semana", "hoje", "amanhã")
        rel_found = [w for w in rel_words if w in body.lower()]
        ambiguous = bool(rel_found)
        kind = (
            "schedule_change"
            if "adiad" in body.lower()
            else (
                "deadline" if "entrega" in body.lower() else ("exam" if "prova" in body.lower() else "info")
            )
        )
        structured = None
        if len(dates) == 1 and dates[0].count("/") == 2 and not ambiguous:
            d, m, y = dates[0].split("/")
            structured = f"{y if len(y) == 4 else '20' + y}-{int(m):02d}-{int(d):02d}"
        return {
            "kind": kind,
            "summary": "Interpretação simulada: " + body.strip()[:120],
            "date_mentions": dates + rel_found,
            "structured_date": structured,
            "needs_confirmation": ambiguous or (len(dates) > 0 and structured is None),
            "action": "Confirmar a data com o professor." if ambiguous else "Anotar e ajustar o plano.",
            "urgency": "high" if (ambiguous or kind in ("deadline", "schedule_change")) else "medium",
            "facts": [s.strip() for s in re.split(r"(?<=[\.!])\s+", body.strip()) if s.strip()][:3],
            "inferences": ["[inferência] o conteúdo cobrado não mudou"] if kind == "schedule_change" else [],
        }
    if schema is PlanSuggestion:
        minutes = int(re.search(r"com (\d+) minutos", prompt).group(1))
        session = int(re.search(r"~(\d+) minutos", prompt).group(1))
        commitments = re.findall(r"^- \[(\S+)\] course=(\S+) .*?prio=([\d\.]+)", prompt, re.M)
        materials = re.findall(r"^- \[(\S+)\] course=(\S+)", prompt.split("=== MATERIAIS")[-1], re.M)
        tasks = []
        left = minutes
        for item_id, course_id, _prio in commitments:
            if left < 15:
                break
            dur = min(session, left)
            mats = [m for m, c in materials if c == course_id][:2]
            tasks.append(
                {
                    "course_id": course_id,
                    "objective": f"Avançar no preparo de {item_id.split(':')[-1]} (simulado)",
                    "minutes": dur,
                    "why_now": "compromisso com maior prioridade calculada",
                    "prerequisites": [],
                    "material_refs": mats + [item_id],
                    "steps": [
                        {"minutes": max(5, dur // 3), "action": "revisar definições no material citado"},
                        {"minutes": dur - max(5, dur // 3) - 5, "action": "resolver 2 exercícios"},
                        {"minutes": 5, "action": "anotar a principal dúvida"},
                    ],
                    "done_when": "exercícios resolvidos e dúvida registrada",
                    "link_to_assessment": "inferred",
                    "is_suggested_exercise": not mats,
                }
            )
            left -= dur
        return {"tasks": tasks, "notes": ["plano simulado pelo FakeGroqClient"]}
    raise ValueError(f"schema não suportado no fake: {schema}")
