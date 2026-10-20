"""Utilitários de formatação compartilhados (datas em pt-BR, agenda, divisão por limite).

A renderização das mensagens do Telegram fica em ``views.py`` (modelo + HTML/texto) e
``templates.py`` (biblioteca por intenção).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sigaa_sim.clock import from_utc_iso
from sigaa_sim.domain import Item, ItemKind

TELEGRAM_MAX_CHARS = 4096


def fmt_when(value: str | date | datetime | None, tz: ZoneInfo) -> str:
    """dd/mm/aaaa [HH:MM] — usado em prompts e no CLI (não nas mensagens do Telegram)."""
    if value is None:
        return "sem data"
    if isinstance(value, datetime):
        return value.astimezone(tz).strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    s = str(value)
    if "T" in s:
        return from_utc_iso(s).astimezone(tz).strftime("%d/%m/%Y %H:%M")
    try:
        return date.fromisoformat(s).strftime("%d/%m/%Y")
    except ValueError:
        return s


def upcoming(items: list[Item], today: date, horizon_days: int) -> list[tuple[Item, date]]:
    out = []
    for it in items:
        if it.kind not in (ItemKind.ACTIVITY, ItemKind.ASSESSMENT):
            continue
        d = it.due_at.date() if it.due_at else it.due_date
        if d and today <= d <= date.fromordinal(today.toordinal() + horizon_days):
            out.append((it, d))
    out.sort(key=lambda x: x[1])
    return out


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Divide texto simples longo em partes por linha (não fatia HTML)."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip("\n"))
    return parts
