from pathlib import Path

from ebook_to_audio.models import JobKind, JobStatus, SegmentStatus
from ebook_to_audio.repository import Repository


def _create_segmented_job(repo: Repository, total_units: int = 2):
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, total_units=total_units, options={})
    segments = repo.create_segments(job.id, chapter.id, ["a", "b"])
    return book, chapter, job, segments


def test_repository_creates_book_chapters_job_and_segments(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()

    book = repo.create_book("Title", "txt", "book.txt", "books/1/source.txt", "books/1/source.txt", "books/1/cleaned.txt")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, total_units=2, options={"parallel": 2})
    repo.create_segments(job.id, chapter.id, ["a", "b"])

    assert repo.get_book(book.id).title == "Title"
    assert repo.list_chapters(book.id)[0].paragraph_count == 2
    assert repo.get_job(job.id).status == JobStatus.PENDING
    assert [segment.status for segment in repo.list_segments(job.id)] == [SegmentStatus.PENDING, SegmentStatus.PENDING]


def test_repository_pause_resume_stop_flags_are_persisted(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    job = repo.create_job(book.id, None, JobKind.TTS, total_units=1, options={})

    repo.request_pause(job.id)
    assert repo.get_job(job.id).pause_requested is True
    assert repo.get_job(job.id).status == JobStatus.PAUSED

    repo.resume_job(job.id)
    assert repo.get_job(job.id).pause_requested is False
    assert repo.get_job(job.id).status == JobStatus.RUNNING

    repo.request_stop(job.id)
    assert repo.get_job(job.id).stop_requested is True


def test_acquire_next_pending_segment_returns_none_for_paused_job(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, _ = _create_segmented_job(repo)

    repo.request_pause(job.id)

    assert repo.acquire_next_pending_segment(job.id) is None
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.PENDING,
        SegmentStatus.PENDING,
    ]


def test_acquire_next_pending_segment_returns_none_for_stopped_job(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, _ = _create_segmented_job(repo)

    repo.stop_running_segments(job.id)

    assert repo.acquire_next_pending_segment(job.id) is None
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.STOPPED,
        SegmentStatus.STOPPED,
    ]


def test_request_stop_immediately_marks_job_stopped(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, _ = _create_segmented_job(repo)

    repo.request_stop(job.id)

    stopped_job = repo.get_job(job.id)
    assert stopped_job.stop_requested is True
    assert stopped_job.status == JobStatus.STOPPED


def test_request_pause_preserves_completed_and_stopped_jobs(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, completed_job, completed_segments = _create_segmented_job(repo)
    for segment in completed_segments:
        repo.complete_segment(segment.id, result_text=segment.source_text)

    repo.request_pause(completed_job.id)
    assert repo.get_job(completed_job.id).status == JobStatus.COMPLETED

    _, _, stopped_job, _ = _create_segmented_job(repo)
    repo.request_stop(stopped_job.id)

    repo.request_pause(stopped_job.id)
    assert repo.get_job(stopped_job.id).status == JobStatus.STOPPED


def test_request_stop_preserves_completed_and_failed_jobs(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, completed_job, completed_segments = _create_segmented_job(repo)
    for segment in completed_segments:
        repo.complete_segment(segment.id, result_text=segment.source_text)

    repo.request_stop(completed_job.id)
    completed = repo.get_job(completed_job.id)
    assert completed.status == JobStatus.COMPLETED
    assert completed.stop_requested is False

    _, _, failed_job, failed_segments = _create_segmented_job(repo)
    for segment in failed_segments:
        repo.fail_segment(segment.id, "failed")

    repo.request_stop(failed_job.id)
    failed = repo.get_job(failed_job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.stop_requested is False


def test_resume_job_does_not_resurrect_completed_or_stopped_jobs(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, completed_job, completed_segments = _create_segmented_job(repo)
    for segment in completed_segments:
        repo.complete_segment(segment.id, result_text=segment.source_text)

    repo.resume_job(completed_job.id)
    assert repo.get_job(completed_job.id).status == JobStatus.COMPLETED

    _, _, stopped_job, _ = _create_segmented_job(repo)
    repo.request_stop(stopped_job.id)

    repo.resume_job(stopped_job.id)
    assert repo.get_job(stopped_job.id).status == JobStatus.STOPPED


def test_resume_job_does_not_start_unpaused_pending_job(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, _ = _create_segmented_job(repo)

    repo.resume_job(job.id)

    assert repo.get_job(job.id).status == JobStatus.PENDING


def test_promote_chapter_audio_path_only_for_latest_tts_job(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    older = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TTS,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )
    newer = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TTS,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )

    assert repo.promote_chapter_audio_path_if_latest_tts_job(older.id, "old.wav") is False
    assert repo.get_chapter(chapter.id).audio_path is None
    assert repo.promote_chapter_audio_path_if_latest_tts_job(newer.id, "new.wav") is True
    assert repo.get_chapter(chapter.id).audio_path == "new.wav"


def test_chapter_update_revision_blocks_stale_artifact_promotion(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    tts_job = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TTS,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )
    translate_job = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TRANSLATE,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )

    updated = repo.update_chapter(chapter.id, "第一章 改", chapter.text_path, 3, 1)

    assert updated.content_revision == chapter.content_revision + 1
    assert repo.promote_chapter_audio_path_if_latest_tts_job(tts_job.id, "old.wav") is False
    assert repo.promote_chapter_translation_path_if_current_job(translate_job.id, "old.txt") is False
    assert repo.get_chapter(chapter.id).audio_path is None
    assert repo.get_chapter(chapter.id).translation_path is None


def test_promote_chapter_translation_metadata_only_for_latest_current_job(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "The Gift", "books/1/chapters/0000.txt", 10, 2)
    older = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TRANSLATE,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )
    newer = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TRANSLATE,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )

    assert repo.promote_chapter_translation_metadata_if_current_job(
        older.id,
        translated_title="旧标题",
        summary="旧简介",
    ) is False
    assert repo.get_chapter(chapter.id).translated_title is None
    assert repo.promote_chapter_translation_metadata_if_current_job(
        newer.id,
        translated_title="麦琪的礼物",
        summary="本章讲述一对夫妻互赠礼物。",
    ) is True

    promoted = repo.get_chapter(chapter.id)
    assert promoted.translated_title == "麦琪的礼物"
    assert promoted.summary == "本章讲述一对夫妻互赠礼物。"


def test_chapter_update_clears_translation_metadata(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "The Gift", "books/1/chapters/0000.txt", 10, 2)
    job = repo.create_job(
        book.id,
        chapter.id,
        JobKind.TRANSLATE,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )
    assert repo.promote_chapter_translation_metadata_if_current_job(job.id, "麦琪的礼物", "一两句简介") is True

    updated = repo.update_chapter(chapter.id, "The Gift Revised", chapter.text_path, 12, 3)

    assert updated.translated_title is None
    assert updated.summary is None


def test_legacy_revision_zero_jobs_can_promote_until_chapter_changes(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    tts_job = repo.create_job(book.id, chapter.id, JobKind.TTS, total_units=1, options={})
    translate_job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, total_units=1, options={})

    assert repo.promote_chapter_audio_path_if_latest_tts_job(tts_job.id, "legacy.wav") is True
    assert repo.promote_chapter_translation_path_if_current_job(translate_job.id, "legacy.txt") is True
    repo.update_chapter(chapter.id, "第一章 改", chapter.text_path, 3, 1)

    assert repo.promote_chapter_audio_path_if_latest_tts_job(tts_job.id, "stale.wav") is False
    assert repo.promote_chapter_translation_path_if_current_job(translate_job.id, "stale.txt") is False


def test_refresh_job_progress_uses_segment_count_when_total_units_differs(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, segments = _create_segmented_job(repo, total_units=99)

    for segment in segments:
        repo.complete_segment(segment.id, result_text=segment.source_text)

    refreshed = repo.get_job(job.id)
    assert refreshed.status == JobStatus.COMPLETED
    assert refreshed.total_units == 2
    assert refreshed.completed_units == 2


def test_refresh_job_progress_marks_all_failed_job_failed(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, segments = _create_segmented_job(repo)

    for segment in segments:
        repo.fail_segment(segment.id, "failed")

    assert repo.get_job(job.id).status == JobStatus.FAILED


def test_refresh_job_progress_marks_mixed_results_completed_with_errors(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, segments = _create_segmented_job(repo)

    repo.complete_segment(segments[0].id, result_text="ok")
    repo.fail_segment(segments[1].id, "failed")

    assert repo.get_job(job.id).status == JobStatus.COMPLETED_WITH_ERRORS


def test_two_repositories_acquire_different_pending_segments(tmp_path: Path):
    db_path = tmp_path / "app.db"
    repo = Repository(db_path)
    _, _, job, _ = _create_segmented_job(repo)
    competing_repo = Repository(db_path)

    first = repo.acquire_next_pending_segment(job.id)
    second = competing_repo.acquire_next_pending_segment(job.id)

    assert first is not None
    assert second is not None
    assert first.id != second.id
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.RUNNING,
        SegmentStatus.RUNNING,
    ]


def test_initialize_resets_running_jobs_and_segments_to_pending(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    _, _, job, _ = _create_segmented_job(repo)
    segment = repo.acquire_next_pending_segment(job.id)
    assert segment is not None

    Repository(tmp_path / "app.db").initialize()

    assert repo.get_job(job.id).status == JobStatus.PENDING
    assert repo.list_segments(job.id)[0].status == SegmentStatus.PENDING
