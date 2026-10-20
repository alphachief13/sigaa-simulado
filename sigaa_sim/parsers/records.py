"""Converte registros brutos (dicts vindos do adapter) em ``Item`` tipados.

Formato bruto aceito (todas as chaves opcionais exceto ``title``)::

    {"source_id": "123", "title": "...", "body": "...", "url": "...",
     "published": "25/09/2026 14:30", "deadline": "10/10/2026 23:59",
     "weight": "3.0", "scope": "Cap. 1-3",
     "grade_value": "8,5", "grade_max": "10", "grade_label": "Unidade 1",
     "file_url": "...", "file_name": "...", "file_version_hint": "..."}
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from sigaa_sim.domain import Item, ItemKind, normalize_text
from sigaa_sim.parsers.dates import is_ambiguous, parse_deadline


class ParseError(Exception):
    pass


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def parse_record(raw: dict[str, Any], course_id: str, kind: ItemKind, tz: ZoneInfo) -> Item:
    title = normalize_text(raw.get("title"))
    if not title:
        raise ParseError(f"registro sem título ({kind}): {raw!r}"[:200])

    pub_date, pub_dt = parse_deadline(raw.get("published"), tz)
    due_text = raw.get("deadline")
    due_date, due_dt = parse_deadline(due_text, tz)
    extra: dict[str, Any] = dict(raw.get("extra") or {})
    if due_text and due_date is None:
        # prazo em texto livre que não conseguimos estruturar: pendência de confirmação
        extra["deadline_text"] = normalize_text(str(due_text))
        extra["deadline_ambiguous"] = True
    elif is_ambiguous(raw.get("body")):
        extra["body_mentions_ambiguous_date"] = True

    source_id = raw.get("source_id")
    source_id = str(source_id) if source_id not in (None, "") else None
    published_key = pub_dt.isoformat() if pub_dt else (pub_date.isoformat() if pub_date else None)

    return Item(
        id=Item.make_id(course_id, kind, source_id, title, published_key),
        course_id=course_id,
        kind=kind,
        source_id=source_id,
        title=title,
        body=raw.get("body"),
        url=raw.get("url"),
        published_at=pub_dt,
        published_date=pub_date if pub_dt is None else None,
        due_date=due_date if due_dt is None else None,
        due_at=due_dt,
        weight=_num(raw.get("weight")),
        scope=raw.get("scope"),
        grade_value=_str_or_none(raw.get("grade_value")),
        grade_max=_str_or_none(raw.get("grade_max")),
        grade_label=raw.get("grade_label"),
        file_url=raw.get("file_url"),
        file_name=raw.get("file_name"),
        file_version_hint=_str_or_none(raw.get("file_version_hint")),
        extra=extra,
    )


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return normalize_text(str(v))


def parse_records(
    raws: list[dict[str, Any]], course_id: str, kind: ItemKind, tz: ZoneInfo
) -> tuple[list[Item], list[str]]:
    """Retorna itens válidos e lista de erros de parsing (um registro ruim não derruba a seção)."""
    items, errors = [], []
    for raw in raws:
        try:
            items.append(parse_record(raw, course_id, kind, tz))
        except (ParseError, ValueError) as e:
            errors.append(str(e))
    return items, errors
