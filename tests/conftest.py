from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

# Handlers precisam estar registrados para o pipeline.
import sigaa_sim.knowledge.jobs  # noqa: E402,F401
import sigaa_sim.llm.jobs  # noqa: E402,F401
from sigaa_sim.clock import FixedClock
from sigaa_sim.config import Settings
from sigaa_sim.sigaa.demo import FIXTURES_DIR, DemoAdapter
from sigaa_sim.storage import connect, migrate


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    from sigaa_sim.config import Secrets

    s = Settings(secrets=Secrets(_env_file=None))  # nunca usa o .env real nos testes
    s.general.data_dir = tmp_path / "data"
    s.sigaa.profile = "demo"  # fixtures JSON; o SIGAA Simulado tem testes próprios
    s.telegram.enabled = True
    s.telegram.mode = "demo"
    s.telegram.quiet_hours_start = None
    s.telegram.quiet_hours_end = None
    s.groq.enabled = False
    s.ensure_dirs()
    return s


@pytest.fixture
def conn(settings: Settings):
    c = connect(settings.db_path)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def clock(settings: Settings) -> FixedClock:
    return FixedClock(settings.tz, datetime(2026, 9, 29, 9, 0, tzinfo=settings.tz))


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    """Cópia editável das fixtures para simular cenários sob demanda."""
    dst = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, dst)
    return dst


def adapter(scenario: str = "baseline", fixtures_dir: Path | None = None) -> DemoAdapter:
    return DemoAdapter(scenario, fixtures_dir=fixtures_dir)


def write_scenario(fixtures_dir: Path, name: str, course_file: str, data: dict) -> None:
    d = fixtures_dir / name
    d.mkdir(exist_ok=True)
    (d / f"{course_file}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_fixture(fixtures_dir: Path, scenario: str, course_file: str) -> dict:
    return json.loads((fixtures_dir / scenario / f"{course_file}.json").read_text(encoding="utf-8"))
