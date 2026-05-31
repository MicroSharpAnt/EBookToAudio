from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import Book, Chapter, Job, JobKind, JobStatus, Segment, SegmentStatus


TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_ERRORS,
    JobStatus.FAILED,
    JobStatus.STOPPED,
}


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """
                UPDATE segments
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE status = ?
                """,
                (SegmentStatus.PENDING, SegmentStatus.RUNNING),
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE status = ?
                """,
                (JobStatus.PENDING, JobStatus.RUNNING),
            )

    def create_book(
        self,
        title: str,
        source_format: str,
        original_filename: str,
        source_path: str,
        filtered_path: str,
        cleaned_path: str,
    ) -> Book:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO books(title, source_format, original_filename, source_path, filtered_path, cleaned_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, source_format, original_filename, source_path, filtered_path, cleaned_path),
            )
            return self.get_book(int(cursor.lastrowid), conn=conn)

    def get_book(self, book_id: int, conn: sqlite3.Connection | None = None) -> Book:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        finally:
            if close_conn:
                conn.close()
        if row is None:
            raise KeyError(f"book not found: {book_id}")
        return self._book_from_row(row)

    def create_chapter(
        self,
        book_id: int,
        chapter_index: int,
        title: str,
        text_path: str,
        char_count: int,
        paragraph_count: int,
    ) -> Chapter:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chapters(book_id, chapter_index, title, text_path, char_count, paragraph_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (book_id, chapter_index, title, text_path, char_count, paragraph_count),
            )
            return self._get_chapter(int(cursor.lastrowid), conn)

    def list_chapters(self, book_id: int) -> list[Chapter]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapters WHERE book_id = ? ORDER BY chapter_index",
                (book_id,),
            ).fetchall()
        return [self._chapter_from_row(row) for row in rows]

    def create_job(
        self,
        book_id: int,
        chapter_id: int | None,
        kind: JobKind,
        total_units: int,
        options: dict[str, Any],
    ) -> Job:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs(book_id, chapter_id, kind, status, total_units, options)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (book_id, chapter_id, kind, JobStatus.PENDING, total_units, json.dumps(options, ensure_ascii=False)),
            )
            return self.get_job(int(cursor.lastrowid), conn=conn)

    def get_job(self, job_id: int, conn: sqlite3.Connection | None = None) -> Job:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            if close_conn:
                conn.close()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return self._job_from_row(row)

    def list_jobs(self, book_id: int | None = None) -> list[Job]:
        with self._connection() as conn:
            if book_id is None:
                rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs WHERE book_id = ? ORDER BY id DESC", (book_id,)).fetchall()
        return [self._job_from_row(row) for row in rows]

    def update_job_status(self, job_id: int, status: JobStatus) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, job_id),
            )

    def request_pause(self, job_id: int) -> None:
        with self._connection() as conn:
            job = self.get_job(job_id, conn=conn)
            if job.status in TERMINAL_JOB_STATUSES or job.stop_requested:
                return
            conn.execute(
                """
                UPDATE jobs
                SET pause_requested = 1,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JobStatus.PAUSED, job_id),
            )

    def resume_job(self, job_id: int) -> None:
        with self._connection() as conn:
            job = self.get_job(job_id, conn=conn)
            if job.status in TERMINAL_JOB_STATUSES or job.stop_requested:
                return
            if job.status != JobStatus.PAUSED and not job.pause_requested:
                return
            conn.execute(
                """
                UPDATE jobs
                SET pause_requested = 0,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JobStatus.RUNNING, job_id),
            )
            self.refresh_job_progress(job_id, conn=conn)

    def request_stop(self, job_id: int) -> None:
        with self._connection() as conn:
            job = self.get_job(job_id, conn=conn)
            if job.status in TERMINAL_JOB_STATUSES:
                return
            conn.execute(
                """
                UPDATE jobs
                SET stop_requested = 1,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JobStatus.STOPPED, job_id),
            )

    def create_segments(self, job_id: int, chapter_id: int, source_texts: list[str]) -> list[Segment]:
        with self._connection() as conn:
            segments: list[Segment] = []
            for segment_index, source_text in enumerate(source_texts):
                cursor = conn.execute(
                    """
                    INSERT INTO segments(job_id, chapter_id, segment_index, source_text, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, chapter_id, segment_index, source_text, SegmentStatus.PENDING),
                )
                segments.append(self._get_segment(int(cursor.lastrowid), conn))
            self.refresh_job_progress(job_id, conn=conn)
            return segments

    def list_segments(self, job_id: int) -> list[Segment]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM segments WHERE job_id = ? ORDER BY segment_index",
                (job_id,),
            ).fetchall()
        return [self._segment_from_row(row) for row in rows]

    def acquire_next_pending_segment(self, job_id: int) -> Segment | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                raise KeyError(f"job not found: {job_id}")

            status = JobStatus(str(job_row["status"]))
            if bool(job_row["pause_requested"]) or bool(job_row["stop_requested"]) or status == JobStatus.PAUSED:
                conn.commit()
                return None
            if status in TERMINAL_JOB_STATUSES:
                conn.commit()
                return None

            row = conn.execute(
                """
                SELECT * FROM segments
                WHERE job_id = ? AND status = ?
                ORDER BY segment_index
                LIMIT 1
                """,
                (job_id, SegmentStatus.PENDING),
            ).fetchone()
            if row is None:
                self.refresh_job_progress(job_id, conn=conn)
                conn.commit()
                return None

            conn.execute(
                """
                UPDATE segments
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SegmentStatus.RUNNING, int(row["id"])),
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (JobStatus.RUNNING, job_id, JobStatus.PENDING),
            )
            conn.commit()
            return self._get_segment(int(row["id"]))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_segment(self, segment_id: int, result_text: str | None = None, output_path: str | None = None) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE segments
                SET status = ?,
                    result_text = ?,
                    output_path = ?,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SegmentStatus.COMPLETED, result_text, output_path, segment_id),
            )
            job_id = self._job_id_for_segment(segment_id, conn)
            self.refresh_job_progress(job_id, conn=conn)

    def fail_segment(self, segment_id: int, error_message: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE segments
                SET status = ?,
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SegmentStatus.FAILED, error_message[:1000], segment_id),
            )
            job_id = self._job_id_for_segment(segment_id, conn)
            self.refresh_job_progress(job_id, conn=conn)

    def stop_running_segments(self, job_id: int) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE segments
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (SegmentStatus.STOPPED, job_id, SegmentStatus.PENDING, SegmentStatus.RUNNING),
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, stop_requested = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JobStatus.STOPPED, job_id),
            )
            return int(cursor.rowcount)

    def refresh_job_progress(self, job_id: int, conn: sqlite3.Connection | None = None) -> Job:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    jobs.*,
                    COALESCE(SUM(CASE WHEN segments.status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_count,
                    COALESCE(SUM(CASE WHEN segments.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count,
                    COALESCE(SUM(CASE WHEN segments.status = 'running' THEN 1 ELSE 0 END), 0) AS running_count,
                    COALESCE(SUM(CASE WHEN segments.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                    COUNT(segments.id) AS segment_count
                FROM jobs
                LEFT JOIN segments ON segments.job_id = jobs.id
                WHERE jobs.id = ?
                GROUP BY jobs.id
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")

            completed = int(row["completed_count"])
            failed = int(row["failed_count"])
            running = int(row["running_count"])
            pending = int(row["pending_count"])
            segment_count = int(row["segment_count"])
            total_units = segment_count or int(row["total_units"])
            status = JobStatus(str(row["status"]))

            if bool(row["stop_requested"]):
                status = JobStatus.STOPPED
            elif segment_count > 0 and completed + failed == total_units:
                if completed == 0 and failed > 0:
                    status = JobStatus.FAILED
                elif failed > 0:
                    status = JobStatus.COMPLETED_WITH_ERRORS
                else:
                    status = JobStatus.COMPLETED
            elif running:
                status = JobStatus.RUNNING
            elif pending and status not in {JobStatus.PAUSED, JobStatus.PENDING}:
                status = JobStatus.RUNNING

            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    total_units = ?,
                    completed_units = ?,
                    failed_units = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, total_units, completed, failed, job_id),
            )
            return self.get_job(job_id, conn=conn)
        finally:
            if close_conn:
                conn.close()

    def reset_running_to_pending(self) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE segments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE status = ?",
                (SegmentStatus.PENDING, SegmentStatus.RUNNING),
            )
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE status = ?",
                (JobStatus.PENDING, JobStatus.RUNNING),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _get_chapter(self, chapter_id: int, conn: sqlite3.Connection) -> Chapter:
        row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if row is None:
            raise KeyError(f"chapter not found: {chapter_id}")
        return self._chapter_from_row(row)

    def _get_segment(self, segment_id: int, conn: sqlite3.Connection | None = None) -> Segment:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        finally:
            if close_conn:
                conn.close()
        if row is None:
            raise KeyError(f"segment not found: {segment_id}")
        return self._segment_from_row(row)

    def _job_id_for_segment(self, segment_id: int, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT job_id FROM segments WHERE id = ?", (segment_id,)).fetchone()
        if row is None:
            raise KeyError(f"segment not found: {segment_id}")
        return int(row["job_id"])

    def _book_from_row(self, row: sqlite3.Row) -> Book:
        return Book(
            id=int(row["id"]),
            title=str(row["title"]),
            source_format=str(row["source_format"]),
            original_filename=str(row["original_filename"]),
            source_path=str(row["source_path"]),
            filtered_path=str(row["filtered_path"]),
            cleaned_path=str(row["cleaned_path"]),
            created_at=str(row["created_at"]),
        )

    def _chapter_from_row(self, row: sqlite3.Row) -> Chapter:
        return Chapter(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            chapter_index=int(row["chapter_index"]),
            title=str(row["title"]),
            text_path=str(row["text_path"]),
            char_count=int(row["char_count"]),
            paragraph_count=int(row["paragraph_count"]),
            created_at=str(row["created_at"]),
        )

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        return Job(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            chapter_id=row["chapter_id"],
            kind=JobKind(str(row["kind"])),
            status=JobStatus(str(row["status"])),
            total_units=int(row["total_units"]),
            completed_units=int(row["completed_units"]),
            failed_units=int(row["failed_units"]),
            options=json.loads(str(row["options"] or "{}")),
            pause_requested=bool(row["pause_requested"]),
            stop_requested=bool(row["stop_requested"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _segment_from_row(self, row: sqlite3.Row) -> Segment:
        return Segment(
            id=int(row["id"]),
            job_id=int(row["job_id"]),
            chapter_id=int(row["chapter_id"]),
            segment_index=int(row["segment_index"]),
            source_text=str(row["source_text"]),
            status=SegmentStatus(str(row["status"])),
            output_path=row["output_path"],
            result_text=row["result_text"],
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_format TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    filtered_path TEXT NOT NULL,
    cleaned_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text_path TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    paragraph_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, chapter_index)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    total_units INTEGER NOT NULL,
    completed_units INTEGER NOT NULL DEFAULT 0,
    failed_units INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{}',
    pause_requested INTEGER NOT NULL DEFAULT 0,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT,
    result_text TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_jobs_book_id ON jobs(book_id);
CREATE INDEX IF NOT EXISTS idx_segments_job_status ON segments(job_id, status, segment_index);
"""
