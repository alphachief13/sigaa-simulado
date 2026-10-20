"""Logging rotativo em arquivo + console. Não registra conteúdo acadêmico nem sessão."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(
    logs_dir: Path, level: str = "INFO", retention_days: int = 30, console: bool = True
) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_sigaa_configured", False):
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = TimedRotatingFileHandler(
        logs_dir / "sigaa_sim.log", when="midnight", backupCount=retention_days, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(ch)
    # bibliotecas HTTP podem logar URLs com token: silenciar
    for noisy in ("httpx", "httpcore", "groq", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._sigaa_configured = True  # type: ignore[attr-defined]
