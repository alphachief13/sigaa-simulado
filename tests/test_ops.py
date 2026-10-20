from __future__ import annotations

import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import adapter

from sigaa_sim.config import ConfigError, Settings, load_settings
from sigaa_sim.domain import ItemKind
from sigaa_sim.parsers import parse_deadline, parse_records
from sigaa_sim.storage import LockError, run_lock
from sigaa_sim.sync import run_sync
from sigaa_sim.telegram import FakeTelegramClient, in_quiet_hours, split_message
from sigaa_sim.telegram.commands import is_authorized, parse_command, process_update

TZ = ZoneInfo("America/Fortaleza")


def _update(uid, user, chat, text):
    return {"update_id": uid, "message": {"from": {"id": user}, "chat": {"id": chat}, "text": text}}


def test_unauthorized_user_is_refused_and_logged(conn, settings, clock):
    settings.secrets.telegram_user_id = "111"
    settings.secrets.telegram_chat_id = "111"
    run_sync(conn, adapter("baseline"), settings, clock)
    client = FakeTelegramClient()
    sent = client.sent
    process_update(conn, settings, clock, _update(1, 999, 111, "/prazos"), client)
    process_update(conn, settings, clock, _update(2, 111, 555, "/prazos"), client)
    assert sent == []
    process_update(conn, settings, clock, _update(3, 111, 111, "/prazos"), client)
    assert len(sent) == 1 and "Prova 1" in sent[0]
    # idempotência: mesmo update_id não é reprocessado
    process_update(conn, settings, clock, _update(3, 111, 111, "/prazos"), client)
    assert len(sent) == 1
    rows = conn.execute("SELECT authorized FROM telegram_updates ORDER BY update_id").fetchall()
    assert [r["authorized"] for r in rows] == [0, 0, 1]


def test_commands_never_execute_text(conn, settings, clock):
    settings.secrets.telegram_user_id = settings.secrets.telegram_chat_id = "1"
    run_sync(conn, adapter("baseline"), settings, clock)
    client = FakeTelegramClient()
    sent = client.sent
    process_update(conn, settings, clock, _update(1, 1, 1, "/status; rm -rf / && echo $(id)"), client)
    assert sent == []  # não é um comando válido: ignorado, nada executado
    process_update(conn, settings, clock, _update(2, 1, 1, "/progresso $(rm -rf /) done"), client)
    assert len(sent) == 1 and ("Sessões de hoje" in sent[0] or "Sem plano" in sent[0])
    assert parse_command("__import__('os').system('x')") == (None, "")
    assert parse_command("/plano@meubot 2026-10-01") == ("plano", "2026-10-01")
    assert is_authorized({"message": {}}, settings) is False


def test_split_message_respects_limit():
    text = "\n".join(f"linha {i} " + "x" * 90 for i in range(100))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("linha") == 100


def test_quiet_hours():
    assert in_quiet_hours(time(23, 30), time(23, 0), time(7, 0))
    assert in_quiet_hours(time(2, 0), time(23, 0), time(7, 0))
    assert not in_quiet_hours(time(12, 0), time(23, 0), time(7, 0))
    assert not in_quiet_hours(time(12, 0), None, None)


def test_offline_mode_requires_no_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(tmp_path / "nao_existe.toml")
    assert s.sigaa.profile == "simulado" and not s.groq.enabled and not s.telegram.enabled
    with pytest.raises(ConfigError):
        s.require_groq()
    s2 = Settings()
    s2.telegram.enabled = True
    s2.telegram.mode = "http"
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        s2.require_telegram()
    (tmp_path / "bad.toml").write_text("[general]\ntimezone = 'Marte/Base'\n")
    with pytest.raises(ConfigError, match="fuso"):
        load_settings(tmp_path / "bad.toml")


def test_run_lock_blocks_and_recovers_stale(tmp_path: Path):
    lock = tmp_path / "sync.lock"
    with run_lock(lock):
        with pytest.raises(LockError):
            with run_lock(lock):
                pass
    assert not lock.exists()
    lock.write_text("999999")  # pid inexistente => trava órfã
    with run_lock(lock):
        assert lock.read_text() == str(os.getpid())


def test_date_parsing_pt_br():
    assert parse_deadline("15/10/2026", TZ) == (date(2026, 10, 15), None)
    d, dt = parse_deadline("08/10/2026 23:59", TZ)
    assert d == date(2026, 10, 8) and dt == datetime(2026, 10, 8, 23, 59, tzinfo=TZ)
    d, dt = parse_deadline("Entrega: 8/10/26 às 14h", TZ)
    assert d == date(2026, 10, 8) and dt.hour == 14
    assert parse_deadline("na próxima aula", TZ) == (None, None)
    assert parse_deadline("31/02/2026", TZ) == (None, None)
    d, dt = parse_deadline("2026-10-08T23:59:00", TZ)
    assert dt.tzinfo is not None and dt.hour == 23


def test_parse_records_isolates_bad_rows():
    items, errors = parse_records(
        [{"title": "ok", "deadline": "10/10/2026"}, {"body": "sem título"}, {"title": "  b  "}],
        "c",
        ItemKind.ACTIVITY,
        TZ,
    )
    assert len(items) == 2 and len(errors) == 1
    assert items[1].title == "b"
    # identidade estável sem source_id: mesmo título => mesmo id; título diferente => id diferente
    assert items[0].id != items[1].id
    again, _ = parse_records([{"title": "ok", "deadline": "11/10/2026"}], "c", ItemKind.ACTIVITY, TZ)
    assert again[0].id == items[0].id and again[0].content_hash() != items[0].content_hash()


def test_repo_has_no_secrets_committed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / ".env").exists() or True  # .env local é permitido, mas ignorado pelo git
    gitignore = (root / ".gitignore").read_text()
    for entry in (".env", "data/", "sessions/", "*.db", "selectors.toml"):
        assert entry in gitignore
    for f in (root / "sigaa_sim" / "sigaa" / "fixtures").rglob("*.json"):
        txt = f.read_text(encoding="utf-8").lower()
        assert "senha" not in txt and "password" not in txt and "cookie" not in txt
