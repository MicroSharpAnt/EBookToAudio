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
