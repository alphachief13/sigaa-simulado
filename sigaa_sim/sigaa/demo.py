"""Adapter de demonstração: lê fixtures locais, sem rede e sem credenciais.

Cenários (``[sigaa].demo_fixture``): ``baseline``, ``changed`` (overlay sobre baseline),
``expired`` (autenticação exige interação). Downloads atendem URLs ``demo://files/<nome>``
a partir de ``fixtures/files``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sigaa_sim.domain import AuthResult, AuthStatus, CollectionStatus, Course
from sigaa_sim.sigaa.base import SECTIONS, DownloadResult, RawSection

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class DemoAdapter:
    name = "demo"
    supported_sections = SECTIONS

    def __init__(
        self,
        scenario: str = "baseline",
        institution: str = "demo",
        account: str = "default",
        fixtures_dir: Path | None = None,
    ):
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        self.scenario = scenario
        self.institution = institution
        self.account = account
        self._scenario_meta = self._load_json(self.fixtures_dir / scenario / "scenario.json") or {}
        base_dir = self.fixtures_dir / scenario
        if not base_dir.exists():
            raise FileNotFoundError(f"cenário demo inexistente: {scenario}")
        self._base = self.fixtures_dir / "baseline"
        self._overlay = base_dir
        self._authed = False

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _course_data(self, source_id: str) -> dict:
        data = self._load_json(self._base / f"{source_id}.json") or {}
        overlay = (
            self._load_json(self._overlay / f"{source_id}.json") if self._overlay != self._base else None
        )
        if overlay:
            data = {**data, **{k: v for k, v in overlay.items() if not k.startswith("_")}}
        return data

    # ------------------------------------------------------------------ contrato
    def authenticate(self) -> AuthResult:
        auth = self._scenario_meta.get("auth")
        if auth:
            return AuthResult(status=AuthStatus(auth["status"]), message=auth.get("message"))
        self._authed = True
        return AuthResult(status=AuthStatus.OK, message="sessão demo")

    def list_courses(self) -> list[Course]:
        data = self._load_json(self._overlay / "courses.json") or self._load_json(self._base / "courses.json")
        term = data["term"]
        return [
            Course(
                id=Course.make_id(self.institution, self.account, term, c["source_id"]),
                source_id=c["source_id"],
                term=term,
                code=c["code"],
                name=c["name"],
                professor=c.get("professor"),
                url=c.get("url"),
            )
            for c in data["courses"]
        ]

    def collect(self, course: Course, section: str) -> RawSection:
        if not self._authed:
            return RawSection(status=CollectionStatus.FAILED, error="não autenticado")
        if section not in self.supported_sections:
            return RawSection(status=CollectionStatus.UNSUPPORTED)
        sec = self._course_data(course.source_id).get(section)
        if sec is None:
            return RawSection(status=CollectionStatus.FAILED, error=f"fixture sem seção '{section}'")
        return RawSection(
            status=CollectionStatus(sec.get("status", "complete")),
            records=list(sec.get("records", [])),
            error=sec.get("error"),
            evidence=sec.get("evidence"),
        )

    def download(
        self, url: str, *, max_bytes: int, etag: str | None = None, last_modified: str | None = None
    ) -> DownloadResult:
        prefix = "demo://files/"
        if not url.startswith(prefix):
            return DownloadResult(ok=False, error=f"URL fora do domínio demo: {url}")
        name = url[len(prefix) :]
        path = self.fixtures_dir / "files" / Path(name).name
        if not path.exists():
            return DownloadResult(ok=False, status_code=404, error="arquivo não encontrado")
        data = path.read_bytes()
        if len(data) > max_bytes:
            return DownloadResult(ok=False, error=f"arquivo excede limite ({len(data)} bytes)")
        pseudo_etag = f'"{len(data)}-{path.stem}"'
        if etag and etag == pseudo_etag:
            return DownloadResult(ok=True, not_modified=True, etag=pseudo_etag, status_code=304)
        ctype = {
            ".pdf": "application/pdf",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".html": "text/html",
        }.get(path.suffix, "application/octet-stream")
        return DownloadResult(ok=True, data=data, content_type=ctype, etag=pseudo_etag, status_code=200)

    def close(self) -> None:
        self._authed = False
