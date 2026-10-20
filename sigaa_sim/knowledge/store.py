"""Knowledge Base: materiais em disco por período/disciplina, documentos por hash, busca."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from sigaa_sim.clock import from_utc_iso
from sigaa_sim.config import Settings
from sigaa_sim.domain import Course, Item, slugify
from sigaa_sim.knowledge.chunks import CHUNKER_VERSION, chunk_segments
from sigaa_sim.knowledge.extract import EXTRACTOR_VERSION, extract, is_login_page, sniff_kind
from sigaa_sim.sigaa.base import DownloadResult, SigaaAdapter
from sigaa_sim.storage import has_fts

log = logging.getLogger(__name__)

SAFE_EXT = {"pdf", "pptx", "txt", "md", "html", "docx", "bin"}


@dataclass
class MaterialOutcome:
    status: str  # downloaded | unchanged | invalid | too_large | failed | blocked | unsupported | no_text
    message: str = ""
    file_hash: str | None = None
    new_version: bool = False
    doc_status: str | None = None


def course_dir(settings: Settings, course: Course) -> Path:
    # slug legível acompanha o ID estável, não o substitui
    return settings.materials_dir / slugify(course.term) / f"{slugify(course.source_id)}__{course.slug}"


def safe_filename(name: str | None, url: str, file_hash: str, kind: str) -> str:
    base = name or Path(urlparse(url).path).name or "material"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:80] or "material"
    ext = kind if kind in SAFE_EXT else "bin"
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem}.{file_hash[:10]}.{ext}"


def host_allowed(url: str, settings: Settings) -> bool:
    """Só URLs da fonte simulada ativa são baixadas; não existe download pela rede."""
    scheme = urlparse(url).scheme
    return (scheme, settings.sigaa.profile) in (("sim", "simulado"), ("demo", "demo"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Knowledge:
    def __init__(self, conn: sqlite3.Connection, settings: Settings):
        self.conn = conn
        self.settings = settings
        self._fts = has_fts(conn)

    # -------------------------------------------------------------- materiais
    def ensure_material(self, item: Item, now_iso: str) -> int:
        assert item.file_url
        self.conn.execute(
            """INSERT INTO materials(item_id, course_id, url) VALUES(?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET url=excluded.url,
                 status=CASE WHEN materials.url<>excluded.url THEN 'pending' ELSE materials.status END""",
            (item.id, item.course_id, item.file_url),
        )
        row = self.conn.execute("SELECT id FROM materials WHERE item_id=?", (item.id,)).fetchone()
        return int(row["id"])

    def needs_fetch(self, item_id: str, now_iso: str) -> tuple[bool, str]:
        row = self.conn.execute("SELECT * FROM materials WHERE item_id=?", (item_id,)).fetchone()
        if row is None or row["status"] != "downloaded" or not row["last_checked_at"]:
            return True, "nunca baixado"
        days = self.settings.knowledge.revalidate_every_days
        if days <= 0:
            return False, "revalidação desligada"
        age = from_utc_iso(now_iso) - from_utc_iso(row["last_checked_at"])
        if age.days >= days:
            return True, f"revalidação periódica ({age.days}d)"
        return False, "recente"

    def fetch_material(
        self, adapter: SigaaAdapter, item: Item, course: Course, now_iso: str
    ) -> MaterialOutcome:
        """Baixa/revalida um material. Persiste versão, documento e chunks (cache por hash)."""
        assert item.file_url
        mat_id = self.ensure_material(item, now_iso)
        row = self.conn.execute("SELECT * FROM materials WHERE id=?", (mat_id,)).fetchone()

        if not host_allowed(item.file_url, self.settings):
            self._set_status(
                mat_id,
                "blocked",
                now_iso,
                "host fora da lista permitida; sessão não é enviada a destinos externos",
            )
            return MaterialOutcome("blocked", "link externo não baixado (host não permitido)")

        max_bytes = self.settings.knowledge.max_file_mb * 1024 * 1024
        res: DownloadResult = adapter.download(
            item.file_url, max_bytes=max_bytes, etag=row["etag"], last_modified=row["last_modified"]
        )
        if res.not_modified:
            self.conn.execute("UPDATE materials SET last_checked_at=? WHERE id=?", (now_iso, mat_id))
            return MaterialOutcome(
                "unchanged", "não modificado (validador HTTP)", file_hash=row["current_file_hash"]
            )
        if not res.ok or res.data is None:
            status = "too_large" if res.error and "excede" in res.error else "failed"
            self._set_status(mat_id, status, now_iso, res.error or "falha no download")
            return MaterialOutcome(status, res.error or "falha no download")

        data = res.data
        if len(data) > max_bytes:
            self._set_status(mat_id, "too_large", now_iso, f"{len(data)} bytes")
            return MaterialOutcome("too_large", f"arquivo com {len(data)} bytes excede o limite")

        kind = sniff_kind(data, item.file_name, res.content_type)
        if kind == "html" and is_login_page(data):
            self._set_status(mat_id, "invalid", now_iso, "página de login recebida no lugar do arquivo")
            return MaterialOutcome("invalid", "página de login recebida (sessão expirada?)")
        declared_ext = (item.file_name or "").rsplit(".", 1)[-1].lower() if item.file_name else ""
        if declared_ext in ("pdf", "pptx") and kind != declared_ext:
            self._set_status(mat_id, "invalid", now_iso, f"esperado {declared_ext}, recebido {kind}")
            return MaterialOutcome("invalid", f"tipo real ({kind}) difere do declarado ({declared_ext})")

        file_hash = hashlib.sha256(data).hexdigest()
        new_version = file_hash != row["current_file_hash"]
        if new_version:
            path = course_dir(self.settings, course) / safe_filename(
                item.file_name, item.file_url, file_hash, kind
            )
            if not path.exists():
                atomic_write(path, data)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "UPDATE material_versions SET superseded_at=? "
                    "WHERE material_id=? AND superseded_at IS NULL",
                    (now_iso, mat_id),
                )
                self.conn.execute(
                    """INSERT OR IGNORE INTO material_versions(material_id, file_hash, local_path,
                           content_type, size_bytes, downloaded_at) VALUES(?,?,?,?,?,?)""",
                    (mat_id, file_hash, str(path), res.content_type, len(data), now_iso),
                )
                self.conn.execute(
                    """UPDATE materials SET status='downloaded', current_file_hash=?, etag=?,
                           last_modified=?, last_checked_at=?, error=NULL WHERE id=?""",
                    (file_hash, res.etag, res.last_modified, now_iso, mat_id),
                )
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self._prune_versions(mat_id)
        else:
            self.conn.execute(
                "UPDATE materials SET last_checked_at=?, etag=?, last_modified=? WHERE id=?",
                (now_iso, res.etag, res.last_modified, mat_id),
            )

        doc_status = self.ensure_document(file_hash, data, kind, now_iso)
        return MaterialOutcome(
            "downloaded" if new_version else "unchanged",
            "nova versão" if new_version else "mesmo conteúdo (cache)",
            file_hash=file_hash,
            new_version=new_version,
            doc_status=doc_status,
        )

    def _set_status(self, mat_id: int, status: str, now_iso: str, error: str) -> None:
        self.conn.execute(
            "UPDATE materials SET status=?, error=?, last_checked_at=? WHERE id=?",
            (status, error[:300], now_iso, mat_id),
        )

    def _prune_versions(self, mat_id: int) -> None:
        keep = self.settings.knowledge.keep_old_versions
        rows = self.conn.execute(
            """SELECT id, local_path FROM material_versions WHERE material_id=? AND superseded_at IS NOT NULL
               ORDER BY downloaded_at DESC""",
            (mat_id,),
        ).fetchall()
        for r in rows[keep:]:
            # remove só o arquivo; a linha fica para auditoria (fonte antiga referenciável)
            Path(r["local_path"]).unlink(missing_ok=True)

    # -------------------------------------------------------------- documentos
    def ensure_document(self, file_hash: str, data: bytes, kind: str, now_iso: str) -> str:
        """Extrai e chunka uma vez por (hash, versão do extrator/chunker). Retorna status."""
        row = self.conn.execute("SELECT * FROM documents WHERE file_hash=?", (file_hash,)).fetchone()
        if row and row["extractor_version"] == EXTRACTOR_VERSION:
            chunk_ver = self.conn.execute(
                "SELECT chunker_version FROM chunks WHERE file_hash=? LIMIT 1", (file_hash,)
            ).fetchone()
            if row["status"] != "extracted" or (chunk_ver and chunk_ver[0] == CHUNKER_VERSION):
                return row["status"]
        ex = extract(data, kind)
        chunks = (
            chunk_segments(
                ex.segments, self.settings.knowledge.chunk_chars, self.settings.knowledge.chunk_overlap_chars
            )
            if ex.segments
            else []
        )
        error = ex.error
        if ex.warnings:
            error = ("; ".join(ex.warnings) + (f" | {ex.error}" if ex.error else ""))[:300]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("DELETE FROM chunks WHERE file_hash=?", (file_hash,))
            self.conn.execute(
                """INSERT INTO documents(file_hash, extractor_version, status, kind, page_count,
                       char_count, error, created_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(file_hash) DO UPDATE SET extractor_version=excluded.extractor_version,
                       status=excluded.status, kind=excluded.kind, page_count=excluded.page_count,
                       char_count=excluded.char_count, error=excluded.error""",
                (
                    file_hash,
                    EXTRACTOR_VERSION,
                    ex.status,
                    ex.kind,
                    ex.page_count,
                    ex.char_count,
                    error,
                    now_iso,
                ),
            )
            for c in chunks:
                self.conn.execute(
                    "INSERT INTO chunks(file_hash, seq, ref, text, chunker_version) VALUES(?,?,?,?,?)",
                    (file_hash, c.seq, c.ref, c.text, CHUNKER_VERSION),
                )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return ex.status

    def chunks_for(self, file_hash: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM chunks WHERE file_hash=? ORDER BY seq", (file_hash,)
        ).fetchall()

    def current_materials(self, course_id: str | None = None) -> list[sqlite3.Row]:
        sql = """SELECT m.*, i.title, i.current_json, d.status doc_status, d.error doc_error, d.kind doc_kind,
                        d.page_count
                 FROM materials m JOIN items i ON i.id=m.item_id
                 LEFT JOIN documents d ON d.file_hash=m.current_file_hash
                 WHERE i.removed_at IS NULL"""
        args: list = []
        if course_id:
            sql += " AND m.course_id=?"
            args.append(course_id)
        return self.conn.execute(sql + " ORDER BY i.title", args).fetchall()

    # ------------------------------------------------------------------ busca
    def search(self, query: str, course_id: str | None = None, limit: int = 8) -> list[dict]:
        """Busca textual em chunks (FTS5 se disponível; fallback LIKE). Retorna chunks com origem."""
        terms = [t for t in re.findall(r"\w{3,}", query.lower()) if t not in _STOP][:8]
        if not terms:
            return []
        base_join = """JOIN materials m ON m.current_file_hash=c.file_hash
                       JOIN items i ON i.id=m.item_id"""
        args: list = []
        course_filter = ""
        if course_id:
            course_filter = " AND m.course_id=?"
        if self._fts:
            match = " OR ".join(f'"{t}"' for t in terms)
            sql = f"""SELECT c.id, c.file_hash, c.ref, c.text, m.item_id, m.course_id, i.title,
                             bm25(chunks_fts) score
                      FROM chunks_fts f JOIN chunks c ON c.id=f.rowid {base_join}
                      WHERE chunks_fts MATCH ? {course_filter}
                      ORDER BY score LIMIT ?"""
            args = [match] + ([course_id] if course_id else []) + [limit]
        else:
            likes = " OR ".join("lower(c.text) LIKE ?" for _ in terms)
            sql = f"""SELECT c.id, c.file_hash, c.ref, c.text, m.item_id, m.course_id, i.title, 0 score
                      FROM chunks c {base_join} WHERE ({likes}) {course_filter} LIMIT ?"""
            args = [f"%{t}%" for t in terms] + ([course_id] if course_id else []) + [limit]
        rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


_STOP = {"para", "com", "uma", "que", "dos", "das", "por", "sobre", "the", "and", "aula", "prova"}
