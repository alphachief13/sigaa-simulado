"""Schemas das respostas da LLM. Versionados; compatíveis com strict mode
(todos os campos obrigatórios, ``additionalProperties: false``, opcionais via ``| None``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicRef(Strict):
    topic: str
    refs: list[str] = Field(description="referências de origem, ex.: 'p.3', 'slide 5'")


class DocumentSummary(Strict):
    """Resumo compacto de um material."""

    summary: str = Field(description="3 a 6 frases; só o que está no texto")
    topics: list[TopicRef]
    prerequisites: list[str] = Field(description="pré-requisitos inferidos (rotular como inferência)")
    exercises_refs: list[str] = Field(description="refs onde há exercícios; vazio se não houver")
    missing_or_unclear: list[str] = Field(description="lacunas: fórmulas/figuras/tabelas ilegíveis")
    confidence: Literal["high", "medium", "low"]


class AnnouncementInterpretation(Strict):
    """Interpretação de um aviso do professor."""

    kind: Literal["deadline", "exam", "schedule_change", "material", "info", "other"]
    summary: str
    date_mentions: list[str] = Field(description="trechos literais que mencionam datas")
    structured_date: str | None = Field(description="AAAA-MM-DD se explícito e inequívoco; senão null")
    needs_confirmation: bool = Field(description="true se a data é ambígua ('próxima aula')")
    action: str = Field(description="ação sugerida em uma frase")
    urgency: Literal["high", "medium", "low"]
    facts: list[str] = Field(description="afirmações literais do aviso")
    inferences: list[str] = Field(description="conclusões que não estão escritas literalmente")


class PlanStep(Strict):
    minutes: int
    action: str = Field(description="uma ação prática concreta")


class PlanTask(Strict):
    course_id: str
    objective: str
    minutes: int
    why_now: str
    prerequisites: list[str]
    material_refs: list[str] = Field(
        description="IDs de materiais/itens fornecidos no contexto; nunca inventar"
    )
    steps: list[PlanStep]
    done_when: str
    link_to_assessment: Literal["confirmed", "inferred", "none"]
    is_suggested_exercise: bool = Field(description="true se os exercícios são autorais, não do material")


class PlanSuggestion(Strict):
    tasks: list[PlanTask]
    notes: list[str] = Field(description="limitações, dados ausentes")


class SourceRef(Strict):
    material: str = Field(description="título do material ou item exatamente como fornecido")
    ref: str = Field(description="página/slide/seção, ex.: 'p.3'; vazio se for um prazo/aviso")


class QuestionAnswer(Strict):
    """Resposta a uma pergunta do aluno, baseada apenas no contexto fornecido."""

    answer: str = Field(description="resposta direta em português, curta, em parágrafos/linhas")
    sources: list[SourceRef]
    not_found: list[str] = Field(description="partes da pergunta sem apoio no contexto")
    confidence: Literal["high", "medium", "low"]


SCHEMAS = {
    "question_answer": QuestionAnswer,
    "document_summary": DocumentSummary,
    "announcement_interpretation": AnnouncementInterpretation,
    "plan_suggestion": PlanSuggestion,
}


def strict_json_schema(model: type[BaseModel]) -> dict:
    """JSON Schema com todos os campos required e additionalProperties=false (strict mode)."""
    schema = model.model_json_schema()
    _tighten(schema)
    for d in schema.get("$defs", {}).values():
        _tighten(d)
    return schema


def _tighten(node: dict) -> None:
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())
        for p in node["properties"].values():
            p.pop("default", None)
            _tighten(p)
    if "items" in node and isinstance(node["items"], dict):
        _tighten(node["items"])
    for alt in node.get("anyOf", []):
        _tighten(alt)
