from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobKind(StrEnum):
    SPLIT = "split"
    TRANSLATE = "translate"
    TTS = "tts"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    STOPPED = "stopped"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TextMetadata:
    char_count: int
    paragraph_count: int


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    source_format: str
    original_filename: str
    source_path: str
    filtered_path: str
    cleaned_path: str
    created_at: str = ""


@dataclass(frozen=True)
class Chapter:
    id: int
    book_id: int
    chapter_index: int
    title: str
    text_path: str
    char_count: int
    paragraph_count: int
    translation_path: str | None = None
    translated_title: str | None = None
    summary: str | None = None
    audio_path: str | None = None
    content_revision: int = 0
    created_at: str = ""


@dataclass(frozen=True)
class Job:
    id: int
    book_id: int
    chapter_id: int | None
    kind: JobKind
    status: JobStatus
    total_units: int
    completed_units: int = 0
    failed_units: int = 0
    options: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    pause_requested: bool = False
    stop_requested: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class Segment:
    id: int
    job_id: int
    chapter_id: int
    segment_index: int
    source_text: str
    status: SegmentStatus
    output_path: str | None = None
    result_text: str | None = None
    error_message: str | None = None
    created_at: str = ""
    updated_at: str = ""
