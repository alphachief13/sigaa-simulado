"""Repositório central (cursos, itens, revisões, eventos, execuções)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from sigaa_sim.domain import ChangeEvent, Course, EventType, Item, ItemKind, SectionResult


class Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------ courses
    def upsert_course(self, course: Course, now_iso: str) -> None:
        self.conn.execute(
            """INSERT INTO courses(id, source_id, term, code, name, slug, professor, url,
                                   first_seen_at, last_seen_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, code=excluded.code, slug=excluded.slug,
                 professor=COALESCE(excluded.professor, courses.professor),
                 url=COALESCE(excluded.url, courses.url), last_seen_at=excluded.last_seen_at""",
            (
                course.id,
                course.source_id,
                course.term,
                course.code,
                course.name,
                course.slug,
                course.professor,
                course.url,
                now_iso,
                now_iso,
            ),
        )

    def get_course(self, course_id: str) -> Course | None:
        row = self.conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return _row_to_course(row) if row else None

    def list_courses(self) -> list[Course]:
        rows = self.conn.execute("SELECT * FROM courses ORDER BY name").fetchall()
        return [_row_to_course(r) for r in rows]

    # -------------------------------------------------------------------- items
    def get_item(self, item_id: str) -> Item | None:
        row = self.conn.execute("SELECT current_json FROM items WHERE id=?", (item_id,)).fetchone()
        return Item.model_validate_json(row["current_json"]) if row else None

    def get_item_row(self, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def list_items(
        self, course_id: str | None = None, kind: ItemKind | None = None, include_removed: bool = False
    ) -> list[Item]:
        sql = "SELECT current_json FROM items WHERE 1=1"
        args: list[Any] = []
        if course_id:
            sql += " AND course_id=?"
            args.append(course_id)
        if kind:
            sql += " AND kind=?"
            args.append(str(kind))
        if not include_removed:
            sql += " AND removed_at IS NULL"
        sql += " ORDER BY updated_at DESC"
        return [Item.model_validate_json(r["current_json"]) for r in self.conn.execute(sql, args).fetchall()]

    def active_item_ids(self, course_id: str, kind: ItemKind) -> set[str]:
        rows = self.conn.execute(
            "SELECT id FROM items WHERE course_id=? AND kind=? AND removed_at IS NULL",
            (course_id, str(kind)),
        ).fetchall()
        return {r["id"] for r in rows}

    def insert_item(self, item: Item, now_iso: str, sync_run_id: int | None) -> None:
        h = item.content_hash()
        pub = _published_key(item)
        self.conn.execute(
            """INSERT INTO items(id, course_id, kind, source_id, title, content_hash, current_json,
                                 revision, source_published, first_seen_at, last_seen_at, updated_at)
               VALUES(?,?,?,?,?,?,?,1,?,?,?,?)""",
            (
                item.id,
                item.course_id,
                str(item.kind),
                item.source_id,
                item.title,
                h,
                item.to_json(),
                pub,
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        self.conn.execute(
            """INSERT INTO item_revisions(item_id, revision, content_hash, json, collected_at,
                                          sync_run_id) VALUES(?,1,?,?,?,?)""",
            (item.id, h, item.to_json(), now_iso, sync_run_id),
        )

    def update_item(self, item: Item, now_iso: str, sync_run_id: int | None) -> int:
        """Grava nova revisão. Retorna o número da revisão."""
        row = self.conn.execute("SELECT revision FROM items WHERE id=?", (item.id,)).fetchone()
        rev = int(row["revision"]) + 1
        h = item.content_hash()
        self.conn.execute(
            """UPDATE items SET title=?, content_hash=?, current_json=?, revision=?,
                    source_published=?, last_seen_at=?, updated_at=?, missing_count=0,
                    removed_at=NULL WHERE id=?""",
            (item.title, h, item.to_json(), rev, _published_key(item), now_iso, now_iso, item.id),
        )
        self.conn.execute(
            """INSERT INTO item_revisions(item_id, revision, content_hash, json, collected_at,
                                          sync_run_id) VALUES(?,?,?,?,?,?)""",
            (item.id, rev, h, item.to_json(), now_iso, sync_run_id),
        )
        return rev

    def touch_item(self, item_id: str, now_iso: str) -> None:
        self.conn.execute("UPDATE items SET last_seen_at=?, missing_count=0 WHERE id=?", (now_iso, item_id))

    def increment_missing(self, item_ids: set[str]) -> None:
        for iid in item_ids:
            self.conn.execute("UPDATE items SET missing_count=missing_count+1 WHERE id=?", (iid,))

    def items_to_remove(self, threshold: int) -> list[Item]:
        rows = self.conn.execute(
            "SELECT current_json FROM items WHERE removed_at IS NULL AND missing_count>=?",
            (threshold,),
        ).fetchall()
        return [Item.model_validate_json(r["current_json"]) for r in rows]

    def mark_removed(self, item_id: str, now_iso: str) -> None:
        self.conn.execute("UPDATE items SET removed_at=? WHERE id=?", (now_iso, item_id))

    def item_revisions(self, item_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM item_revisions WHERE item_id=? ORDER BY revision", (item_id,)
        ).fetchall()

    # ------------------------------------------------------------------- events
    def insert_event(self, ev: ChangeEvent) -> int:
        cur = self.conn.execute(
            """INSERT INTO change_events(item_id, course_id, kind, event_type, old_json, new_json,
                                         changed_fields, detected_at, sync_run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                ev.item_id,
                ev.course_id,
                str(ev.kind),
                str(ev.event_type),
                json.dumps(ev.old, ensure_ascii=False, default=str) if ev.old is not None else None,
                json.dumps(ev.new, ensure_ascii=False, default=str) if ev.new is not None else None,
                json.dumps(ev.changed_fields),
                ev.detected_at,
                ev.sync_run_id,
            ),
        )
        return int(cur.lastrowid)

    def list_events(
        self, item_id: str | None = None, since: str | None = None, limit: int = 200
    ) -> list[ChangeEvent]:
        sql = "SELECT * FROM change_events WHERE 1=1"
        args: list[Any] = []
        if item_id:
            sql += " AND item_id=?"
            args.append(item_id)
        if since:
            sql += " AND detected_at>=?"
            args.append(since)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            out.append(
                ChangeEvent(
                    id=r["id"],
                    item_id=r["item_id"],
                    course_id=r["course_id"],
                    kind=ItemKind(r["kind"]),
                    event_type=EventType(r["event_type"]),
                    old=json.loads(r["old_json"]) if r["old_json"] else None,
                    new=json.loads(r["new_json"]) if r["new_json"] else None,
                    changed_fields=json.loads(r["changed_fields"]),
                    detected_at=r["detected_at"],
                    sync_run_id=r["sync_run_id"],
                )
            )
        return out

    def count(self, table: str, where: str = "1=1", args: tuple = ()) -> int:
        return int(self.conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {where}", args).fetchone()["c"])

    # --------------------------------------------------------------------- runs
    def start_run(self, adapter: str, now_iso: str, baseline: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_runs(started_at, status, adapter, baseline) VALUES(?, 'running', ?, ?)",
            (now_iso, adapter, int(baseline)),
        )
        return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, status: str, now_iso: str, stats: dict, error: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, stats_json=?, error=? WHERE id=?",
            (now_iso, status, json.dumps(stats, ensure_ascii=False, default=str), error, run_id),
        )

    def last_run(self, status: str | None = None) -> sqlite3.Row | None:
        if status:
            return self.conn.execute(
                "SELECT * FROM sync_runs WHERE status=? ORDER BY id DESC LIMIT 1", (status,)
            ).fetchone()
        return self.conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()

    def has_successful_run(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sync_runs WHERE status IN ('success','partial') LIMIT 1"
        ).fetchone()
        return row is not None

    def record_section(self, run_id: int, result: SectionResult, now_iso: str) -> None:
        self.conn.execute(
            """INSERT INTO section_collections(sync_run_id, course_id, section, status, item_count,
                                               error, evidence, collected_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                run_id,
                result.course_id,
                result.section,
                str(result.status),
                len(result.items),
                result.error,
                result.evidence,
                now_iso,
            ),
        )

    def sections_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM section_collections WHERE sync_run_id=? ORDER BY course_id, section",
            (run_id,),
        ).fetchall()


def _published_key(item: Item) -> str | None:
    if item.published_at:
        from sigaa_sim.clock import to_utc_iso

        return to_utc_iso(item.published_at)
    if item.published_date:
        return item.published_date.isoformat()
    return None


def _row_to_course(row: sqlite3.Row) -> Course:
    return Course(
        id=row["id"],
        source_id=row["source_id"],
        term=row["term"],
        code=row["code"],
        name=row["name"],
        professor=row["professor"],
        url=row["url"],
    )
