"""Contratos de dados do domínio (Pydantic).

Um único modelo ``Item`` cobre avisos, atividades, avaliações, notas, conteúdos e
materiais, diferenciados por ``kind``. Campos não aplicáveis ficam ``None``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator


class ItemKind(StrEnum):
    ANNOUNCEMENT = "announcement"
    ACTIVITY = "activity"
    ASSESSMENT = "assessment"
    GRADE = "grade"
    CONTENT = "content"
    MATERIAL = "material"


class CollectionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    # Distingue uma seção realmente vazia de erro/login (vazia só com evidência).
    EMPTY = "empty"


class AuthStatus(StrEnum):
    OK = "ok"
    NEEDS_INTERACTION = "needs_interaction"  # CAPTCHA/MFA/login manual
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


class EventType(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DEADLINE_CHANGED = "deadline_changed"
    GRADE_CHANGED = "grade_changed"
    MATERIAL_REPLACED = "material_replaced"
    REMOVED = "removed"
    BASELINE = "baseline"


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    norm = re.sub(r"[^a-zA-Z0-9]+", "_", norm).strip("_").lower()
    return norm or "sem_nome"


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Term(BaseModel):
    code: str  # ex.: "2026.2"
    name: str | None = None


class Course(BaseModel):
    """Turma de uma disciplina em um período letivo."""

    id: str  # ID estável: institution:account:term:source_id
    source_id: str
    term: str
    code: str
    name: str
    professor: str | None = None
    url: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @staticmethod
    def make_id(institution: str, account: str, term: str, source_id: str) -> str:
        return f"{institution}:{account}:{term}:{slugify(source_id)}"


class Item(BaseModel):
    """Registro acadêmico normalizado.

    ``due_date`` é usado quando a fonte só informa a data; ``due_at`` quando há horário
    (datetime consciente de fuso). Nunca inventamos hora para uma data.
    """

    id: str
    course_id: str
    kind: ItemKind
    source_id: str | None = None
    title: str
    body: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    published_date: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    # avaliação/atividade
    weight: float | None = None
    scope: str | None = None
    # nota
    grade_value: str | None = None
    grade_max: str | None = None
    grade_label: str | None = None
    # material
    file_url: str | None = None
    file_name: str | None = None
    file_version_hint: str | None = None  # ex.: data/tamanho exibidos pela fonte
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "body", "scope", "grade_label", "file_name")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        return normalize_text(v)

    @field_validator("due_at", "published_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime deve ter fuso")
        return v

    # Campos que participam do hash de conteúdo (por tipo).
    HASH_FIELDS: ClassVar[dict[ItemKind, tuple[str, ...]]] = {
        ItemKind.ANNOUNCEMENT: ("title", "body", "published_date", "published_at"),
        ItemKind.ACTIVITY: ("title", "body", "due_date", "due_at", "weight", "scope"),
        ItemKind.ASSESSMENT: ("title", "body", "due_date", "due_at", "weight", "scope"),
        ItemKind.GRADE: ("title", "grade_value", "grade_max", "grade_label"),
        ItemKind.CONTENT: ("title", "body", "published_date"),
        ItemKind.MATERIAL: ("title", "body", "file_url", "file_name", "file_version_hint"),
    }

    def relevant_fields(self) -> dict[str, Any]:
        fields = self.HASH_FIELDS[self.kind]
        out: dict[str, Any] = {}
        for f in fields:
            v = getattr(self, f)
            if isinstance(v, datetime):
                from sigaa_sim.clock import to_utc_iso

                v = to_utc_iso(v)
            elif isinstance(v, date):
                v = v.isoformat()
            out[f] = v
        return out

    def content_hash(self) -> str:
        return sha256_text(canonical_json(self.relevant_fields()))

    def deadline_key(self) -> str | None:
        """Representação comparável do prazo (data ou datetime)."""
        if self.due_at:
            from sigaa_sim.clock import to_utc_iso

            return to_utc_iso(self.due_at)
        if self.due_date:
            return self.due_date.isoformat()
        return None

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @staticmethod
    def make_id(
        course_id: str, kind: ItemKind, source_id: str | None, title: str, published: str | None = None
    ) -> str:
        """Identidade estável do item.

        Prefere o ID do SIGAA. Sem ID, usa hash de (curso, tipo, título normalizado,
        data de publicação). Limitação documentada: renomear um item sem ID cria
        outro item; em vez disso o item antigo será eventualmente marcado como removido.
        """
        if source_id:
            return f"{course_id}:{kind}:{slugify(source_id)}"
        base = canonical_json([course_id, kind, (normalize_text(title) or "").lower(), published])
        return f"{course_id}:{kind}:h{sha256_text(base)[:16]}"


class SectionResult(BaseModel):
    """Resultado da coleta de uma seção de uma disciplina."""

    course_id: str
    section: str  # announcements | activities | assessments | grades | contents | materials
    status: CollectionStatus
    items: list[Item] = Field(default_factory=list)
    error: str | None = None
    evidence: str | None = None  # ex.: "mensagem 'Nenhum registro' encontrada"


class AuthResult(BaseModel):
    status: AuthStatus
    message: str | None = None


class ChangeEvent(BaseModel):
    id: int | None = None
    item_id: str
    course_id: str
    kind: ItemKind
    event_type: EventType
    old: dict[str, Any] | None = None
    new: dict[str, Any] | None = None
    changed_fields: list[str] = Field(default_factory=list)
    detected_at: str = ""
    sync_run_id: int | None = None
