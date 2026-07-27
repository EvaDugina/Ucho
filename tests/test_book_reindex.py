from __future__ import annotations

import io
import json
import zipfile

import pytest

from bot import books, userctx, vault
from scripts import reindex_books

TEXT = "Содержательный старый текст о выборе, памяти и ответственности. " * 20


def _epub() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OPS/content.opf",
            '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:title>Старый EPUB</dc:title></metadata><manifest>"
            '<item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine></package>',
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            f"<html><body><h1>Глава EPUB</h1><p>{TEXT}</p></body></html>",
        )
    return target.getvalue()


def _fb2() -> bytes:
    return (
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Старый FB2</book-title></title-info></description>"
        f"<body><section><title><p>Глава FB2</p></title><p>{TEXT}</p></section></body>"
        "</FictionBook>"
    ).encode()


def _legacy_book(book_id: str, suffix: str, payload: bytes) -> None:
    directory = vault.books_dir() / book_id
    directory.mkdir(parents=True)
    (directory / f"source.{suffix}").write_bytes(payload)
    (directory / "text.txt").write_text("legacy text\n", encoding="utf-8")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "id": book_id,
                "sha256": "legacy-" + book_id,
                "title": "Старое название",
                "author": "",
                "source_filename": f"old.{suffix}",
                "source_format": suffix,
                "uploader_uid": 1,
                "uploaded_at": "2026-01-01T00:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_preview_apply_and_idempotent_reindex(as_user):
    fixtures = {
        "1" * 16: ("epub", _epub()),
        "2" * 16: ("fb2", _fb2()),
        "3" * 16: ("md", ("# Старая MD\n\n## Глава\n\n" + TEXT).encode()),
    }
    for book_id, (suffix, payload) in fixtures.items():
        _legacy_book(book_id, suffix, payload)
    txt_id = "4" * 16
    _legacy_book(txt_id, "txt", TEXT.encode())

    state = vault._load_state()
    state["books"] = {
        "disabled": [txt_id],
        "scores": {txt_id: 7, "1" * 16: 2},
        "pending_reminder": {"book_id": txt_id, "excerpt": "Старая цитата"},
    }
    vault._save_state(state)
    raw_path = userctx.user_root() / "00_raw" / "sessions" / "old.jsonl"
    raw_path.write_text(
        json.dumps({"metadata": {"book_id": txt_id}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raw_before = raw_path.read_bytes()

    preview = reindex_books.run(apply=False)
    assert preview["mode"] == "preview"
    indexed = {item["id"]: item for item in preview["supported"]}
    assert set(fixtures).issubset(indexed)
    assert all(indexed[book_id]["status"] == "reindex" for book_id in fixtures)
    assert preview["delete_txt"] == [txt_id]
    assert not any(
        (vault.books_dir() / book_id / "structure.json").exists()
        for book_id in fixtures
    )
    assert (vault.books_dir() / txt_id).exists()

    applied = reindex_books.run(apply=True)
    assert applied["errors"] == []
    assert set(fixtures).issubset({item["id"] for item in applied["reindexed"]})
    assert applied["deleted_txt"] == [txt_id]
    assert not (vault.books_dir() / txt_id).exists()
    assert raw_path.read_bytes() == raw_before
    for book_id in fixtures:
        assert books.get_book(book_id)["id"] == book_id
        assert (vault.books_dir() / book_id / "structure.json").exists()
        assert (vault.books_dir() / book_id / "text.txt").read_text(
            encoding="utf-8"
        ) == "legacy text\n"

    userctx.set_user(as_user)
    updated_state = vault._load_state()["books"]
    assert txt_id not in updated_state["disabled"]
    assert txt_id not in updated_state["scores"]
    assert updated_state["pending_reminder"] is None
    assert updated_state["scores"]["1" * 16] == 2

    repeated = reindex_books.run(apply=True)
    assert repeated["errors"] == []
    assert repeated["reindexed"] == []
    assert repeated["deleted_txt"] == []
    assert all(item["status"] == "current" for item in repeated["supported"])


def test_corrupt_saved_source_is_reported_without_mutation(as_user):
    book_id = "5" * 16
    _legacy_book(book_id, "epub", b"not-a-zip")
    before = (vault.books_dir() / book_id / "metadata.json").read_bytes()
    report = reindex_books.run(apply=True)
    assert any(item.get("id") == book_id for item in report["errors"])
    assert not (vault.books_dir() / book_id / "structure.json").exists()
    assert (vault.books_dir() / book_id / "metadata.json").read_bytes() == before


def test_reindex_rolls_back_book_scope_on_write_error(as_user, monkeypatch):
    book_id = "6" * 16
    _legacy_book(book_id, "md", ("# Книга\n\n## Глава\n\n" + TEXT).encode())
    metadata_path = vault.books_dir() / book_id / "metadata.json"
    before = metadata_path.read_bytes()
    original = books.atomic_write_json

    def fail_metadata(path, value):
        if path.name == "metadata.json":
            raise RuntimeError("write failed")
        return original(path, value)

    monkeypatch.setattr(books, "atomic_write_json", fail_metadata)
    with pytest.raises(RuntimeError, match="write failed"):
        books.reindex_saved_book(book_id)
    assert metadata_path.read_bytes() == before
    assert not (vault.books_dir() / book_id / "structure.json").exists()
