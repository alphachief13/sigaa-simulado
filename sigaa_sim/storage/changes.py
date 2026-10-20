"""Detecção de mudanças: compara itens coletados com o estado atual persistido.

Comparação é feita contra o estado *atual* (não contra um conjunto histórico de
hashes), então uma sequência A → B → A registra as três alterações.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sigaa_sim.domain import (
    ChangeEvent,
    CollectionStatus,
    EventType,
    ItemKind,
    SectionResult,
)
from sigaa_sim.storage.repo import Repo

DEADLINE_FIELDS = {"due_date", "due_at"}
GRADE_FIELDS = {"grade_value", "grade_max", "grade_label"}
MATERIAL_FILE_FIELDS = {"file_url", "file_name", "file_version_hint"}


@dataclass
class DetectionResult:
    created: list[ChangeEvent] = field(default_factory=list)
    updated: list[ChangeEvent] = field(default_factory=list)
    unchanged: int = 0

    @property
    def events(self) -> list[ChangeEvent]:
        return self.created + self.updated


def classify(kind: ItemKind, changed: list[str]) -> EventType:
    s = set(changed)
    if kind in (ItemKind.ACTIVITY, ItemKind.ASSESSMENT) and s & DEADLINE_FIELDS:
        return EventType.DEADLINE_CHANGED
    if kind == ItemKind.GRADE and s & GRADE_FIELDS:
        return EventType.GRADE_CHANGED
    if kind == ItemKind.MATERIAL and s & MATERIAL_FILE_FIELDS:
        return EventType.MATERIAL_REPLACED
    return EventType.UPDATED


def diff_fields(old: dict, new: dict) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def detect_section(
    repo: Repo, result: SectionResult, now_iso: str, sync_run_id: int, baseline: bool
) -> DetectionResult:
    """Persiste itens novos/alterados e seus eventos. Deve rodar dentro de uma transação."""
    out = DetectionResult()
    if result.status not in (CollectionStatus.COMPLETE, CollectionStatus.PARTIAL, CollectionStatus.EMPTY):
        return out  # falha/login/parser quebrado: não toca no estado

    for item in result.items:
        existing = repo.get_item(item.id)
        if existing is None:
            repo.insert_item(item, now_iso, sync_run_id)
            ev = ChangeEvent(
                item_id=item.id,
                course_id=item.course_id,
                kind=item.kind,
                event_type=EventType.BASELINE if baseline else EventType.CREATED,
                old=None,
                new=item.relevant_fields(),
                changed_fields=[],
                detected_at=now_iso,
                sync_run_id=sync_run_id,
            )
            ev.id = repo.insert_event(ev)
            out.created.append(ev)
            continue

        old_fields = existing.relevant_fields()
        new_fields = item.relevant_fields()
        if existing.content_hash() == item.content_hash():
            repo.touch_item(item.id, now_iso)
            out.unchanged += 1
            continue

        changed = diff_fields(old_fields, new_fields)
        repo.update_item(item, now_iso, sync_run_id)
        ev = ChangeEvent(
            item_id=item.id,
            course_id=item.course_id,
            kind=item.kind,
            event_type=classify(item.kind, changed),
            old=old_fields,
            new=new_fields,
            changed_fields=changed,
            detected_at=now_iso,
            sync_run_id=sync_run_id,
        )
        ev.id = repo.insert_event(ev)
        out.updated.append(ev)
    return out


def track_missing(repo: Repo, result: SectionResult, kind: ItemKind) -> set[str]:
    """Em coleta COMPLETA (ou vazia com evidência), incrementa contador de ausência dos itens
    que não apareceram. Ausência em coleta parcial/falha é ignorada.
    """
    if result.status not in (CollectionStatus.COMPLETE, CollectionStatus.EMPTY):
        return set()
    seen = {i.id for i in result.items}
    missing = repo.active_item_ids(result.course_id, kind) - seen
    repo.increment_missing(missing)
    return missing


def apply_removals(repo: Repo, threshold: int, now_iso: str, sync_run_id: int) -> list[ChangeEvent]:
    """Política conservadora: só marca removido após N coletas completas consecutivas sem o item."""
    events = []
    for item in repo.items_to_remove(threshold):
        repo.mark_removed(item.id, now_iso)
        ev = ChangeEvent(
            item_id=item.id,
            course_id=item.course_id,
            kind=item.kind,
            event_type=EventType.REMOVED,
            old=item.relevant_fields(),
            new=None,
            changed_fields=[],
            detected_at=now_iso,
            sync_run_id=sync_run_id,
        )
        ev.id = repo.insert_event(ev)
        events.append(ev)
    return events


SECTION_KIND: dict[str, ItemKind] = {
    "announcements": ItemKind.ANNOUNCEMENT,
    "activities": ItemKind.ACTIVITY,
    "assessments": ItemKind.ASSESSMENT,
    "grades": ItemKind.GRADE,
    "contents": ItemKind.CONTENT,
    "materials": ItemKind.MATERIAL,
}
