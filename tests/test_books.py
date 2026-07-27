from __future__ import annotations

import io
import zipfile
from random import Random

import pytest

from bot import books, handlers, userctx
from bot.services import deletion_service

TEXT = (
    "Это содержательный абзац о выборе, памяти и ответственности человека. " * 8
    + "\n\n"
    + "Второй абзац нужен для случайного выбора точной книжной цитаты. " * 8
)


def _fb2() -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Испытание</book-title>"
        "<author><first-name>Иван</first-name><last-name>Тестов</last-name></author>"
        "</title-info></description><body><section><p>"
        + TEXT
        + "</p></section></body></FictionBook>"
    ).encode()


def _epub(*, traversal: bool = False) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        if traversal:
            archive.writestr("../escape.txt", "bad")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns:dc="dc"><metadata><dc:title>EPUB Test</dc:title>'
            '<dc:creator>Автор</dc:creator></metadata><manifest>'
            '<item id="c1" href="chapter.xhtml"/></manifest><spine>'
            '<itemref idref="c1"/></spine></package>',
        )
        archive.writestr("OEBPS/chapter.xhtml", f"<html><body><p>{TEXT}</p></body></html>")
    return target.getvalue()


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("book.txt", TEXT.encode()),
        ("book.md", ("# Книга\n\n" + TEXT).encode()),
        ("book.fb2", _fb2()),
        ("book.epub", _epub()),
    ],
)
def test_all_formats_are_normalized(as_user, filename, payload):
    result = books.ingest(payload, filename, uploader_uid=as_user)
    directory = books.vault.books_dir() / result["id"]
    assert (directory / f"source.{filename.rsplit('.', 1)[1]}").exists()
    assert (directory / "text.txt").read_text(encoding="utf-8").strip()
    assert (directory / "metadata.json").exists()


def test_dedup_and_epub_traversal_protection(as_user):
    first = books.ingest(TEXT.encode(), "same.txt", uploader_uid=as_user)
    second = books.ingest(TEXT.encode(), "renamed.md", uploader_uid=as_user)
    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    with pytest.raises(books.BookError, match="небезопасный путь"):
        books.ingest(_epub(traversal=True), "evil.epub", uploader_uid=as_user)
    assert books.get_book("../../outside") is None
    with pytest.raises(books.BookError, match="идентификатор"):
        books.choose_excerpt("../../outside")


def test_size_and_empty_limits(as_user, monkeypatch):
    monkeypatch.setattr(books, "BOOK_UPLOAD_MAX_BYTES", 10)
    with pytest.raises(books.BookError):
        books.ingest(TEXT.encode(), "large.txt", uploader_uid=as_user)
    monkeypatch.setattr(books, "BOOK_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
    with pytest.raises(books.BookError):
        books.ingest(b"short", "empty.txt", uploader_uid=as_user)


def test_existing_book_id_with_other_sha_is_not_overwritten(as_user):
    payload = (TEXT + " unique collision").encode()
    suffix, _, _, normalized = books._extract(payload, "collision.txt")
    digest = books.hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    directory = books.vault.books_dir() / digest[:16]
    directory.mkdir(parents=True, exist_ok=True)
    metadata = directory / "metadata.json"
    metadata.write_text('{"sha256":"other"}', encoding="utf-8")
    with pytest.raises(books.BookError, match="конфликт"):
        books.ingest(payload, f"collision{suffix}", uploader_uid=as_user)
    assert metadata.read_text(encoding="utf-8") == '{"sha256":"other"}'


def test_library_global_but_toggles_and_scores_personal(as_user):
    book = books.ingest(TEXT.encode(), "global.txt", uploader_uid=as_user)
    books.set_reminder_enabled(book["id"], False)
    userctx.set_user(as_user + 100_000)
    books.vault.ensure_layout()
    assert books.get_book(book["id"]) is not None
    assert books.reminder_enabled(book["id"]) is True
    assert books.score(book["id"]) == 0


def test_weight_formula_and_score_only_once(as_user):
    first = books.ingest((TEXT + " first").encode(), "first.txt", uploader_uid=as_user)
    second = books.ingest((TEXT + " second").encode(), "second.txt", uploader_uid=as_user)
    state = books.vault._load_state()
    state["books"] = {"disabled": [], "scores": {first["id"]: 2, second["id"]: 5}}
    books.vault._save_state(state)

    class Capture(Random):
        def choices(self, population, weights=None, k=1):
            self.population = population
            self.weights = weights
            return [population[0]]

    rng = Capture()
    books.choose_for_reminder(rng=rng)
    weighted = {book["id"]: weight for book, weight in zip(rng.population, rng.weights)}
    assert weighted[first["id"]] == 3
    assert weighted[second["id"]] == 6
    books.set_pending_reminder(first, "Дословная цитата", message_id=7)
    assert books.consume_pending_reminder()["book_id"] == first["id"]
    assert books.consume_pending_reminder() is None
    assert books.score(first["id"]) == 3


def test_leta_keeps_global_books(as_user):
    book = books.ingest(TEXT.encode(), "keep.txt", uploader_uid=as_user)
    books.set_reminder_enabled(book["id"], False)
    deletion_service.delete_current_user_data()
    assert books.get_book(book["id"]) is not None
    assert books.reminder_enabled(book["id"]) is True


def test_corrupt_metadata_and_unusable_text_are_not_reminder_candidates(as_user):
    book = books.ingest((TEXT + " corrupt").encode(), "corrupt.txt", uploader_uid=as_user)
    for item in books.list_books():
        books.set_reminder_enabled(str(item["id"]), item["id"] == book["id"])
    directory = books.vault.books_dir() / book["id"]
    (directory / "text.txt").unlink()
    assert books.choose_for_reminder() is None

    bad_dir = books.vault.books_dir() / "aaaaaaaaaaaaaaaa"
    bad_dir.mkdir()
    (bad_dir / "metadata.json").write_text('{"id":"../../outside"}', encoding="utf-8")
    assert all(item["id"] != "../../outside" for item in books.list_books())


@pytest.mark.asyncio
async def test_sea_callback_uses_question_string_from_llm_dict(as_user, monkeypatch):
    book = books.ingest((TEXT + " callback").encode(), "callback.txt", uploader_uid=as_user)
    monkeypatch.setattr(handlers.books, "get_book", lambda _: book)
    monkeypatch.setattr(handlers.books, "choose_excerpt", lambda _: "Фрагмент книги")
    monkeypatch.setattr(handlers.ratelimit, "try_acquire", lambda _: True)
    monkeypatch.setattr(handlers.ratelimit, "release", lambda _: None)

    async def ask(**kwargs):
        return {"question": "Как ты понимаешь этот фрагмент?", "domain": "knowledge"}

    captured = {}

    async def send(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(handlers, "ask_book_question", ask)
    monkeypatch.setattr(handlers.session_messages, "send_question", send)

    class Callback:
        data = f"sea:b:{book['id']}"
        from_user = type("User", (), {"id": as_user})()
        message = type("Message", (), {"chat": type("Chat", (), {"id": as_user})()})()
        bot = object()

        async def answer(self, *args, **kwargs):
            return None

    await handlers.cb_sea(Callback())
    assert captured["text"] == "Как ты понимаешь этот фрагмент?"
    assert isinstance(captured["text"], str)
