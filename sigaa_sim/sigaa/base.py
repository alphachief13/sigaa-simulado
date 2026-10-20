"""Contrato do adapter SIGAA.

Um adapter autentica, lista disciplinas, coleta seções (retornando registros brutos
com status de completude) e baixa materiais usando a sessão autorizada.
Ele **não** interpreta nada: parsing fica em ``sigaa_sim.parsers``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sigaa_sim.domain import AuthResult, CollectionStatus, Course

SECTIONS = ("announcements", "activities", "assessments", "grades", "contents", "materials")


@dataclass
class RawSection:
    """Registros brutos de uma seção + status de completude e evidência."""

    status: CollectionStatus
    records: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    evidence: str | None = None


@dataclass
class DownloadResult:
    ok: bool
    data: bytes | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    error: str | None = None
    not_modified: bool = False


class SigaaAdapter(Protocol):
    name: str
    supported_sections: tuple[str, ...]

    def authenticate(self) -> AuthResult: ...

    def list_courses(self) -> list[Course]: ...

    def collect(self, course: Course, section: str) -> RawSection: ...

    def download(
        self, url: str, *, max_bytes: int, etag: str | None = None, last_modified: str | None = None
    ) -> DownloadResult: ...

    def close(self) -> None: ...
