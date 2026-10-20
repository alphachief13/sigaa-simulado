"""Parsing de datas em formatos comuns do SIGAA (pt-BR), sem inventar horário.

Retorna ``(date, datetime|None)``: se a fonte só traz a data, ``datetime`` é None.
Expressões ambíguas ("próxima aula", "semana que vem") retornam ``None`` e devem
ser tratadas como pendência de confirmação com o texto de origem.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2,4})(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:h](\d{2})?(?!\d)")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?")

AMBIGUOUS_MARKERS = (
    "próxima aula",
    "proxima aula",
    "semana que vem",
    "próxima semana",
    "proxima semana",
    "em breve",
    "a definir",
    "a combinar",
    "depois do feriado",
)


def is_ambiguous(text: str | None) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(m in t for m in AMBIGUOUS_MARKERS)


def parse_date_pt(text: str | None) -> date | None:
    if not text:
        return None
    m = _ISO_RE.search(text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_RE.search(text)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_time_pt(text: str | None) -> time | None:
    if not text:
        return None
    # remove a parte da data para não confundir "10/10/2026" com hora
    stripped = _DATE_RE.sub(" ", _ISO_RE.sub(" ", text))
    m = _TIME_RE.search(stripped)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return time(h, mi)
    return None


def parse_deadline(text: str | None, tz: ZoneInfo) -> tuple[date | None, datetime | None]:
    """Extrai prazo. Só produz datetime se houver horário explícito."""
    d = parse_date_pt(text)
    if d is None:
        return None, None
    m = _ISO_RE.search(text or "")
    if m and m.group(4):
        return d, datetime(
            d.year, d.month, d.day, int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=tz
        )
    t = parse_time_pt(text)
    if t is None:
        return d, None
    return d, datetime.combine(d, t, tzinfo=tz)
