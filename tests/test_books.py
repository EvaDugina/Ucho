from __future__ import annotations

import io
import json
import zipfile
from random import Random

import pytest

from bot import books, handlers, userctx
from bot.services import deletion_service

PARAGRAPH_A = "Это содержательный абзац о выборе, памяти и ответственности человека. " * 8
PARAGRAPH_B = "Второй абзац нужен для случайного выбора точной книжной цитаты. " * 8
TEXT = PARAGRAPH_A + "\n\n" + PARAGRAPH_B
MARKDOWN = "# Книга\n\n## Первая глава\n\n" + TEXT + "\n\n## Вторая глава\n\n" + TEXT


def _fb2() -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Испытание</book-title>"
        "<author><first-name>Иван</first-name><last-name>Тестов</last-name></author>"
        "</title-info></description><body>"
        "<section><title><p>Первая глава</p></title><p>"
        + PARAGRAPH_A
        + "</p><section><title><p>Подраздел</p></title><p>"
        + PARAGRAPH_B
        + "</p></section></section>"
        "<section><title><p>Вторая глава</p></title><p>"
        + PARAGRAPH_A
        + "</p></section></body>"
        '<body name="notes"><section><p>Служебное примечание не для цитат.</p></section></body>'
        "</FictionBook>"
    ).encode()


def _epub(
    *,
    traversal: bool = False,
    toc: str = "nav",
    same_document: bool = True,
    html_doctype: bool = False,
    unsafe_entity: bool = False,
) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        if traversal:
            archive.writestr("../escape.txt", "bad")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
            "</rootfiles></container>",
        )
        nav_manifest = ""
        spine_toc = ""
        extra = ""
        if toc == "nav":
            nav_manifest = (
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                'properties="nav"/>'
            )
            archive.writestr(
                "OEBPS/nav.xhtml",
                ("<!DOCTYPE html>" if html_doctype else "")
                + '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                '<nav epub:type="toc"><ol>'
                '<li><a href="chapter.xhtml#one">Первая глава</a></li>'
                '<li><a href="chapter.xhtml#two">Вторая глава</a></li>'
                "</ol></nav></body></html>",
            )
        elif toc == "ncx":
            nav_manifest = '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            spine_toc = ' toc="ncx"'
            archive.writestr(
                "OEBPS/toc.ncx",
                "<ncx><navMap>"
                '<navPoint><navLabel><text>Первая глава</text></navLabel>'
                '<content src="chapter.xhtml#one"/></navPoint>'
                '<navPoint><navLabel><text>Вторая глава</text></navLabel>'
                '<content src="chapter.xhtml#two"/></navPoint>'
                "</navMap></ncx>",
            )
        if not same_document:
            extra = (
                '<item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>'
            )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>'
            "<dc:title>EPUB Test</dc:title><dc:creator>Автор</dc:creator>"
            "</metadata><manifest>"
            + nav_manifest
            + '<item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            + extra
            + f"</manifest><spine{spine_toc}><itemref idref=\"c1\"/>"
            + ('<itemref idref="c2"/>' if not same_document else "")
            + "</spine></package>",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            (
                '<!DOCTYPE html [<!ENTITY unsafe "boom">]>'
                if unsafe_entity
                else "<!DOCTYPE html>"
                if html_doctype
                else ""
            )
            + "<html><head><style>НЕ ЦИТИРОВАТЬ</style></head><body>"
            f'<h1 id="one">Первая глава</h1><p>{PARAGRAPH_A}</p>'
            f'<h1 id="two">Вторая глава</h1><p>{PARAGRAPH_B}</p>'
            "</body></html>",
        )
        if not same_document:
            archive.writestr(
                "OEBPS/chapter2.xhtml",
                f"<html><body><h1>Третья глава</h1><p>{PARAGRAPH_A} "
                "Уникальное продолжение spine.</p></body></html>",
            )
    return target.getvalue()


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("book.md", MARKDOWN.encode()),
        ("book.markdown", (MARKDOWN + "\n\nУникальное расширение markdown.").encode()),
        ("book.fb2", _fb2()),
        ("book.epub", _epub()),
    ],
)
def test_supported_formats_create_structure(as_user, filename, payload):
    result = books.ingest(payload, filename, uploader_uid=as_user)
    directory = books.vault.books_dir() / result["id"]
    assert (directory / f"source.{filename.rsplit('.', 1)[1]}").exists()
    assert (directory / "text.txt").read_text(encoding="utf-8").strip()
    structure = json.loads((directory / "structure.json").read_text(encoding="utf-8"))
    assert structure["version"] == books.STRUCTURE_VERSION
    assert len(structure["chapters"]) >= 1
    assert result["chapter_count"] == len(structure["chapters"])


@pytest.mark.parametrize("filename", ["book.txt", "book.pdf", "book.docx"])
def test_unsupported_formats_are_rejected(as_user, filename):
    with pytest.raises(books.BookError, match="EPUB, FB2 и Markdown"):
        books.ingest(TEXT.encode(), filename, uploader_uid=as_user)


def test_dedup_and_epub_traversal_protection(as_user):
    first = books.ingest(MARKDOWN.encode(), "same.md", uploader_uid=as_user)
    second = books.ingest(MARKDOWN.encode(), "renamed.markdown", uploader_uid=as_user)
    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    with pytest.raises(books.BookError, match="небезопасный путь"):
        books.ingest(_epub(traversal=True), "evil.epub", uploader_uid=as_user)
    assert books.get_book("../../outside") is None
    with pytest.raises(books.BookError, match="идентификатор|не найдена"):
        books.choose_excerpt("../../outside")


def test_epub_allows_html5_doctype_but_rejects_entity(as_user):
    accepted = books.ingest(
        _epub(html_doctype=True),
        "doctype.epub",
        uploader_uid=as_user,
    )
    assert accepted["chapter_count"] == 2
    with pytest.raises(books.BookError, match="DTD или entity"):
        books.ingest(
            _epub(unsafe_entity=True),
            "entity.epub",
            uploader_uid=as_user,
        )


def test_size_and_empty_limits(as_user, monkeypatch):
    monkeypatch.setattr(books, "BOOK_UPLOAD_MAX_BYTES", 10)
    with pytest.raises(books.BookError):
        books.ingest(MARKDOWN.encode(), "large.md", uploader_uid=as_user)
    monkeypatch.setattr(books, "BOOK_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
    with pytest.raises(books.BookError):
        books.ingest(b"# short", "empty.md", uploader_uid=as_user)
    monkeypatch.setattr(books, "BOOK_EXTRACTED_MAX_BYTES", 100)
    with pytest.raises(books.BookError, match="Распакованный EPUB"):
        books.ingest(_epub(), "expanded.epub", uploader_uid=as_user)


def test_existing_book_id_with_other_sha_is_not_overwritten(as_user):
    payload = (MARKDOWN + "\n\nunique collision").encode()
    suffix, _, _, normalized = books._extract(payload, "collision.md")
    digest = books.hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    directory = books.vault.books_dir() / digest[:16]
    directory.mkdir(parents=True, exist_ok=True)
    metadata = directory / "metadata.json"
    metadata.write_text(
        json.dumps({"id": digest[:16], "sha256": "other"}),
        encoding="utf-8",
    )
    with pytest.raises(books.BookError, match="конфликт"):
        books.ingest(payload, f"collision{suffix}", uploader_uid=as_user)
    assert json.loads(metadata.read_text(encoding="utf-8"))["sha256"] == "other"


@pytest.mark.parametrize("toc", ["nav", "ncx"])
def test_epub_toc_splits_chapters_and_skips_style(as_user, toc):
    book = books.ingest(_epub(toc=toc), f"{toc}.epub", uploader_uid=as_user)
    structure = books._load_structure(book["id"])
    assert [chapter["title"] for chapter in structure["chapters"]] == [
        "Первая глава",
        "Вторая глава",
    ]
    assert all(
        "НЕ ЦИТИРОВАТЬ" not in paragraph["text"]
        for chapter in structure["chapters"]
        for paragraph in chapter["paragraphs"]
    )


def test_epub_without_toc_uses_spine_documents(as_user):
    book = books.ingest(
        _epub(toc="none", same_document=False),
        "spine.epub",
        uploader_uid=as_user,
    )
    structure = books._load_structure(book["id"])
    assert len(structure["chapters"]) == 2
    assert structure["chapters"][0]["source_locator"].endswith("chapter.xhtml")
    assert structure["chapters"][1]["source_locator"].endswith("chapter2.xhtml")


def test_fb2_nested_sections_do_not_duplicate_text_or_notes(as_user):
    book = books.ingest(_fb2(), "nested.fb2", uploader_uid=as_user)
    structure = books._load_structure(book["id"])
    assert [chapter["title"] for chapter in structure["chapters"]] == [
        "Первая глава",
        "Вторая глава",
    ]
    all_text = "\n".join(
        paragraph["text"]
        for chapter in structure["chapters"]
        for paragraph in chapter["paragraphs"]
    )
    assert all_text.count(PARAGRAPH_B.strip()) == 1
    assert "Служебное примечание" not in all_text
    assert any(section["title"] == "Подраздел" for section in structure["chapters"][0]["sections"])


def test_markdown_heading_tree_and_headingless_fallback(as_user):
    book = books.ingest(MARKDOWN.encode(), "headings.md", uploader_uid=as_user)
    structure = books._load_structure(book["id"])
    assert book["title"] == "Книга"
    assert [chapter["title"] for chapter in structure["chapters"]] == [
        "Первая глава",
        "Вторая глава",
    ]
    plain = books.ingest(
        (TEXT + "\n\nУникальный текст документа без заголовков.").encode(),
        "plain.markdown",
        uploader_uid=as_user,
    )
    assert books._load_structure(plain["id"])["chapters"][0]["title"] == "plain"


def test_excerpt_never_crosses_top_level_chapter(as_user):
    first_marker = "АЛЬФА "
    second_marker = "БЕТА "
    content = (
        "# Книга\n\n## Первая\n\n"
        + (first_marker + "мысль о выборе. ") * 30
        + "\n\n### Подраздел\n\n"
        + (first_marker + "продолжение. ") * 20
        + "\n\n## Вторая\n\n"
        + (second_marker + "другая мысль. ") * 30
    )
    book = books.ingest(content.encode(), "boundary.md", uploader_uid=as_user)
    candidates = books._excerpt_candidates(book["id"])
    assert candidates
    assert all(
        not (first_marker in item.text and second_marker in item.text)
        for item in candidates
    )
    assert {item.chapter_title for item in candidates} == {"Первая", "Вторая"}


def test_library_global_but_toggles_and_scores_personal(as_user):
    book = books.ingest(MARKDOWN.encode(), "global.md", uploader_uid=as_user)
    books.set_reminder_enabled(book["id"], False)
    userctx.set_user(as_user + 100_000)
    books.vault.ensure_layout()
    assert books.get_book(book["id"]) is not None
    assert books.reminder_enabled(book["id"]) is True
    assert books.score(book["id"]) == 0


def test_weight_formula_and_score_only_once(as_user):
    first = books.ingest((MARKDOWN + "\n\nfirst").encode(), "first.md", uploader_uid=as_user)
    second = books.ingest((MARKDOWN + "\n\nsecond").encode(), "second.md", uploader_uid=as_user)
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
    excerpt = books.choose_excerpt(first["id"], rng=Random(1))
    books.set_pending_reminder(first, excerpt, message_id=7)
    assert books.consume_pending_reminder()["book_id"] == first["id"]
    assert books.consume_pending_reminder() is None
    assert books.score(first["id"]) == 3


def test_leta_keeps_global_books(as_user):
    book = books.ingest(MARKDOWN.encode(), "keep.md", uploader_uid=as_user)
    books.set_reminder_enabled(book["id"], False)
    deletion_service.delete_current_user_data()
    assert books.get_book(book["id"]) is not None
    assert books.reminder_enabled(book["id"]) is True


def test_corrupt_metadata_and_unusable_structure_are_not_candidates(as_user):
    book = books.ingest((MARKDOWN + "\n\ncorrupt").encode(), "corrupt.md", uploader_uid=as_user)
    for item in books.list_books():
        books.set_reminder_enabled(str(item["id"]), item["id"] == book["id"])
    directory = books.vault.books_dir() / book["id"]
    (directory / "structure.json").unlink()
    assert books.choose_for_reminder() is None

    bad_dir = books.vault.books_dir() / "aaaaaaaaaaaaaaaa"
    bad_dir.mkdir()
    (bad_dir / "metadata.json").write_text('{"id":"../../outside"}', encoding="utf-8")
    assert all(item["id"] != "../../outside" for item in books.list_books())


@pytest.mark.asyncio
async def test_sea_callback_passes_only_selected_excerpt_to_llm(as_user, monkeypatch):
    book = books.ingest((MARKDOWN + "\n\ncallback").encode(), "callback.md", uploader_uid=as_user)
    excerpt = books.BookExcerpt(
        "Фрагмент книги",
        "a" * 16,
        "Первая глава",
        ("Подраздел",),
        "chapter.xhtml#one",
    )
    monkeypatch.setattr(handlers.books, "get_book", lambda _: book)
    monkeypatch.setattr(handlers.books, "choose_excerpt", lambda _: excerpt)
    monkeypatch.setattr(handlers.ratelimit, "try_acquire", lambda _: True)
    monkeypatch.setattr(handlers.ratelimit, "release", lambda _: None)

    llm_input = {}

    async def ask(**kwargs):
        llm_input.update(kwargs)
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
    assert llm_input["excerpt"] == "Фрагмент книги"
    assert llm_input["chapter_title"] == "Первая глава"
    assert captured["metadata"]["chapter_id"] == "a" * 16
    assert captured["text"] == "Как ты понимаешь этот фрагмент?"
