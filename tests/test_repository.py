from pathlib import Path

from ebook_to_audio.models import JobKind, JobStatus, SegmentStatus
from ebook_to_audio.repository import Repository


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
