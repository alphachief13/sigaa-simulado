"""Transporte Telegram (demo/http/fake) e entrega a partir da outbox.

O corpo é gerado do modelo (``MessageView``) em HTML restrito (<b>, <i>) com teclado inline.
Rejeição explícita de marcação (400 "can't parse entities") → reenvio único em texto simples.
Erro de rede/timeout/429 segue a política da fila (nova tentativa), sem fallback duplicado.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import time, timedelta
from pathlib import Path
from typing import Protocol

import httpx

from sigaa_sim.clock import Clock, to_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.storage.queues import Outbox
from sigaa_sim.telegram.views import MessageView, keyboard, render_html, render_plain

log = logging.getLogger(__name__)
TELEGRAM_MAX_CHARS = 4096


class SendError(Exception):
    def __init__(self, message: str, retry_after: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class TelegramClient(Protocol):
    def send_view(self, view: MessageView) -> str | None: ...

    def send_text(self, text: str) -> str | None: ...

    def edit_view(self, message_id: str, view: MessageView) -> None: ...

    def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...


class DemoTelegramClient:
    """Grava as mensagens (texto simples) em um arquivo local; nada é enviado."""

    def __init__(self, log_path: Path, echo: bool = False):
        self.log_path = log_path
        self.echo = echo
        self._counter = 0

    def send_view(self, view: MessageView) -> str | None:
        text = render_plain(view)
        if view.buttons:
            text += "\n[" + " · ".join(b.label for b in view.buttons) + "]"
        return self.send_text(text)

    def send_text(self, text: str) -> str | None:
        self._counter += 1
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"--- demo message {self._counter} ---\n{text}\n\n")
        if self.echo:
            print(f"\n[telegram demo]\n{text}\n")
        return f"demo-{self._counter}"

    def edit_view(self, message_id: str, view: MessageView) -> None:
        self.send_text(f"(edição de {message_id})\n" + render_plain(view))

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        pass


class HttpTelegramClient:
    """Bot API via httpx. Requer TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."""

    def __init__(self, token: str, chat_id: str, timeout: float = 20.0):
        self._base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=timeout)

    def send_view(self, view: MessageView) -> str | None:
        payload = {"text": render_html(view), "parse_mode": "HTML"}
        kb = keyboard(view)
        if kb:
            payload["reply_markup"] = kb
        try:
            return self._call("sendMessage", payload)
        except SendError as e:
            if e.permanent and "parse" in str(e).lower():
                log.warning("telegram recusou a marcação HTML; reenviando texto simples")
                payload = {"text": render_plain(view)}
                if kb:
                    payload["reply_markup"] = kb
                return self._call("sendMessage", payload)
            raise

    def send_text(self, text: str) -> str | None:
        return self._call("sendMessage", {"text": text})

    def edit_view(self, message_id: str, view: MessageView) -> None:
        payload = {"message_id": int(message_id), "text": render_html(view), "parse_mode": "HTML"}
        kb = keyboard(view)
        payload["reply_markup"] = kb or {"inline_keyboard": []}
        try:
            self._call("editMessageText", payload)
        except SendError as e:
            if "not modified" in str(e).lower():
                return
            raise

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        body = {"callback_query_id": callback_id}
        if text:
            body["text"] = text[:200]
        try:
            self._client.post(f"{self._base}/answerCallbackQuery", json=body)
        except httpx.HTTPError:
            pass

    def _call(self, method: str, payload: dict) -> str | None:
        body = {"chat_id": self.chat_id, "disable_web_page_preview": True, **payload}
        try:
            r = self._client.post(f"{self._base}/{method}", json=body)
        except httpx.HTTPError as e:
            raise SendError(f"erro de rede: {type(e).__name__}") from e
        if r.status_code == 429:
            retry = None
            try:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0)) or None
            except ValueError:
                pass
            raise SendError("rate limit do Telegram", retry_after=retry)
        if r.status_code in (400, 401, 403):
            raise SendError(f"erro permanente {r.status_code}: {_safe_desc(r)}", permanent=True)
        if r.status_code >= 400:
            raise SendError(f"HTTP {r.status_code}")
        data = r.json()
        if not data.get("ok"):
            raise SendError(f"resposta não-ok: {_safe_desc(r)}")
        result = data.get("result")
        return str(result["message_id"]) if isinstance(result, dict) and "message_id" in result else None

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        r = self._client.get(
            f"{self._base}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": '["message","callback_query"]'},
            timeout=timeout + 10,
        )
        return r.json().get("result", [])

    def close(self) -> None:
        self._client.close()


def _safe_desc(r: httpx.Response) -> str:
    try:
        return str(r.json().get("description", ""))[:200]
    except ValueError:
        return r.text[:200]


class FakeTelegramClient:
    """Para testes: registra envios/edições e pode falhar sob comando."""

    def __init__(self):
        self.sent: list[str] = []
        self.views: list[MessageView] = []
        self.edits: list[tuple[str, MessageView]] = []
        self.fail_next = 0
        self._n = 0

    def send_view(self, view: MessageView) -> str | None:
        self.views.append(view)
        return self.send_text(render_plain(view))

    def send_text(self, text: str) -> str | None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise SendError("falha simulada")
        self._n += 1
        self.sent.append(text)
        return f"fake-{self._n}"

    def edit_view(self, message_id: str, view: MessageView) -> None:
        self.edits.append((message_id, view))

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        pass


def make_client(settings: Settings, echo: bool = False) -> TelegramClient:
    if settings.telegram.mode == "http":
        settings.require_telegram()
        return HttpTelegramClient(settings.secrets.telegram_bot_token, settings.secrets.telegram_chat_id)
    return DemoTelegramClient(settings.data_dir / "telegram_demo.log", echo=echo)


def in_quiet_hours(now_local: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end  # atravessa a meia-noite


def _view_of(row: sqlite3.Row) -> MessageView:
    if row["view_json"]:
        return MessageView.from_dict(json.loads(row["view_json"]))
    lines = row["text"].split("\n")
    from sigaa_sim.telegram.views import Block

    return MessageView(title=lines[0], blocks=[Block(lines[1:])] if len(lines) > 1 else [])


def deliver_outbox(
    conn: sqlite3.Connection, client: TelegramClient, clock: Clock, settings: Settings, limit: int = 50
) -> dict[str, int]:
    """Envia mensagens pendentes; cada uma é confirmada em transação própria.

    Se o Telegram receber a mensagem e a conexão cair antes da confirmação, pode haver reenvio
    (não há garantia de entrega exatamente uma vez).
    """
    outbox = Outbox(conn)
    now = clock.now()
    now_iso = to_utc_iso(now)
    stats = {"sent": 0, "failed": 0, "postponed": 0}
    quiet = in_quiet_hours(now.time(), settings.telegram.quiet_hours_start, settings.telegram.quiet_hours_end)
    for row in outbox.due(now_iso, limit=limit):
        if quiet and not row["urgent"]:
            until = _quiet_end(now, settings.telegram.quiet_hours_end)
            conn.execute("BEGIN IMMEDIATE")
            outbox.postpone(row["id"], to_utc_iso(until))
            conn.execute("COMMIT")
            stats["postponed"] += 1
            continue
        try:
            msg_id = client.send_view(_view_of(row))
            conn.execute("BEGIN IMMEDIATE")
            outbox.mark_sent(row["id"], msg_id, now_iso)
            conn.execute("COMMIT")
            stats["sent"] += 1
        except SendError as e:
            conn.execute("BEGIN IMMEDIATE")
            if e.permanent:
                outbox.mark_failed_attempt(row["id"], str(e), now_iso, max_attempts=1)
            else:
                outbox.mark_failed_attempt(
                    row["id"], str(e), now_iso, max_attempts=settings.telegram.max_attempts
                )
                if e.retry_after:
                    outbox.postpone(row["id"], to_utc_iso(now + timedelta(seconds=e.retry_after)))
            conn.execute("COMMIT")
            stats["failed"] += 1
            log.warning("telegram: falha ao enviar outbox#%s: %s", row["id"], e)
    return stats


def _quiet_end(now, end: time | None):
    if end is None:
        return now
    candidate = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


__all__ = [
    "TELEGRAM_MAX_CHARS",
    "DemoTelegramClient",
    "FakeTelegramClient",
    "HttpTelegramClient",
    "SendError",
    "TelegramClient",
    "deliver_outbox",
    "in_quiet_hours",
    "make_client",
]
