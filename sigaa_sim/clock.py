"""Relógio injetável. Datetimes sempre conscientes de fuso.

Convenção de armazenamento: datetimes em UTC ISO 8601 (sufixo ``Z``);
datas sem horário como ``AAAA-MM-DD``. A conversão para o fuso do usuário
acontece só na apresentação.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


class Clock:
    def __init__(self, tz: ZoneInfo):
        self.tz = tz

    def now(self) -> datetime:
        return datetime.now(tz=self.tz)

    def today(self) -> date:
        return self.now().date()

    def now_utc_iso(self) -> str:
        return to_utc_iso(self.now())


class FixedClock(Clock):
    """Relógio controlado para testes."""

    def __init__(self, tz: ZoneInfo, fixed: datetime):
        super().__init__(tz)
        self._fixed = fixed if fixed.tzinfo else fixed.replace(tzinfo=tz)

    def now(self) -> datetime:
        return self._fixed.astimezone(self.tz)

    def set(self, fixed: datetime) -> None:
        self._fixed = fixed if fixed.tzinfo else fixed.replace(tzinfo=self.tz)

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self._fixed = self._fixed + timedelta(**kwargs)


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime sem fuso não pode ser armazenado")
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def from_utc_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
