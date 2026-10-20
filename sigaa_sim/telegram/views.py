"""Modelo de mensagem e renderização determinística (HTML e texto simples a partir da mesma view).

Padrão editorial: informação principal → contexto necessário → próxima ação → fonte breve.
Nenhum identificador interno vai para o corpo. Valores dinâmicos são escapados exatamente uma vez.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sigaa_sim.clock import from_utc_iso

MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
WEEKDAYS = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]

# Nome curto por nome oficial (em maiúsculas). Vazio: as disciplinas do SIGAA Simulado já têm
# nomes de exibição. Complementado por [display_names] no config.toml.
DEFAULT_DISPLAY_NAMES: dict[str, str] = {}


def display_name(official: str, overrides: dict[str, str] | None = None) -> str:
    """Nome de exibição: config → mapa padrão → nome oficial inalterado (sem title())."""
    key = official.strip()
    if overrides:
        for k, v in overrides.items():
            if k.strip().upper() == key.upper():
                return v
    return DEFAULT_DISPLAY_NAMES.get(key.upper(), key)


# ------------------------------------------------------------------ datas
def _to_local(value, tz: ZoneInfo) -> date | datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(tz)
    if isinstance(value, date):
        return value
    s = str(value)
    if "T" in s:
        return from_utc_iso(s).astimezone(tz)
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def fmt_day(value, tz: ZoneInfo, today: date | None = None, weekday: bool = False) -> str:
    """`06 out` / `06 out · 23h59`; ano só se diferir do atual. Não inventa horário."""
    v = _to_local(value, tz)
    if v is None:
        return "data não informada"
    d = v.date() if isinstance(v, datetime) else v
    ref_year = (today or date.today()).year
    out = f"{d.day:02d} {MONTHS[d.month - 1]}" + (f" {d.year}" if d.year != ref_year else "")
    if isinstance(v, datetime):
        out += f" · {v.hour:02d}h{v.minute:02d}"
    if weekday:
        out += f", {WEEKDAYS[d.weekday()]}"
    return out


def fmt_full(value, tz: ZoneInfo) -> str:
    v = _to_local(value, tz)
    if v is None:
        return "data não informada"
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M")
    return v.strftime("%d/%m/%Y")


def relative_days(target: date, today: date) -> str:
    """Diferença de datas locais: 'hoje', 'amanhã', 'em N dias', 'há N dias'."""
    n = (target - today).days
    if n == 0:
        return "hoje"
    if n == 1:
        return "amanhã"
    if n == -1:
        return "ontem"
    return f"em {n} dias" if n > 0 else f"há {-n} dias"


# ------------------------------------------------------------------ view
@dataclass
class Button:
    label: str
    callback: str  # ≤ 64 bytes; resolvido no servidor


@dataclass
class Block:
    """Grupo de linhas separado dos demais por uma linha em branco.

    ``lines`` são strings já em texto simples; trechos a destacar vão em ``bold`` (substrings
    exatas). O renderer escapa tudo e aplica <b> apenas nesses trechos.
    """

    lines: list[str]
    bold: list[str] = field(default_factory=list)
    italic: bool = False


@dataclass
class MessageView:
    title: str  # em negrito; até ~40 caracteres
    subtitle: str | None = None  # contexto de uma linha, peso normal
    blocks: list[Block] = field(default_factory=list)
    footer: str | None = None  # fonte breve / paginação, peso normal
    buttons: list[Button] = field(default_factory=list)  # máx. 3; até 2 por linha
    emoji: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "blocks": [{"lines": b.lines, "bold": b.bold, "italic": b.italic} for b in self.blocks],
            "footer": self.footer,
            "buttons": [{"label": b.label, "callback": b.callback} for b in self.buttons],
            "emoji": self.emoji,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MessageView:
        return cls(
            title=d["title"],
            subtitle=d.get("subtitle"),
            blocks=[
                Block(b["lines"], b.get("bold", []), b.get("italic", False)) for b in d.get("blocks", [])
            ],
            footer=d.get("footer"),
            buttons=[Button(b["label"], b["callback"]) for b in d.get("buttons", [])],
            emoji=d.get("emoji"),
        )


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _apply_bold(line: str, bolds: list[str]) -> str:
    """Escapa a linha e envolve em <b> as substrings indicadas (uma passagem, sem regex livre)."""
    if not bolds:
        return _esc(line)
    spans: list[tuple[int, int]] = []
    for b in bolds:
        if not b:
            continue
        start = 0
        while True:
            i = line.find(b, start)
            if i == -1:
                break
            if not any(s <= i < e for s, e in spans):
                spans.append((i, i + len(b)))
            start = i + len(b)
    spans.sort()
    out, pos = [], 0
    for s, e in spans:
        out.append(_esc(line[pos:s]))
        out.append(f"<b>{_esc(line[s:e])}</b>")
        pos = e
    out.append(_esc(line[pos:]))
    return "".join(out)


def render_html(view: MessageView) -> str:
    head = f"{view.emoji} {view.title}" if view.emoji else view.title
    parts = [f"<b>{_esc(head)}</b>"]
    if view.subtitle:
        parts[0] += "\n" + _esc(view.subtitle)
    for b in view.blocks:
        body = "\n".join(_apply_bold(ln, b.bold) for ln in b.lines)
        parts.append(f"<i>{body}</i>" if b.italic else body)
    if view.footer:
        parts.append(_esc(view.footer))
    return "\n\n".join(p for p in parts if p.strip())


def render_plain(view: MessageView) -> str:
    head = f"{view.emoji} {view.title}" if view.emoji else view.title
    parts = [head + (f"\n{view.subtitle}" if view.subtitle else "")]
    for b in view.blocks:
        parts.append("\n".join(b.lines))
    if view.footer:
        parts.append(view.footer)
    return "\n\n".join(p for p in parts if p.strip())


def keyboard(view: MessageView) -> dict | None:
    if not view.buttons:
        return None
    btns = [{"text": b.label, "callback_data": b.callback[:64]} for b in view.buttons[:3]]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    return {"inline_keyboard": rows}


def sentence(text: str | None, max_chars: int = 220) -> str:
    """Corta em fim de frase, nunca no meio de uma palavra/raciocínio."""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= max_chars:
        return t
    cut = t[: max_chars + 1]
    for sep in (". ", "! ", "? ", "; "):
        i = cut.rfind(sep)
        if i >= max_chars // 2:
            return cut[: i + 1].strip()
    i = cut.rfind(" ")
    return cut[:i].rstrip(",;:") + "…"
