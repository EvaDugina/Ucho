"""Общая библиотека книг и персональные настройки напоминаний.

EPUB никогда не извлекается на диск: ZIP-члены проверяются и читаются в памяти.
Файл книги считается недоверенным вводом; парсер не исполняет содержимое.
"""
from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import random
import re
import zipfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from . import vault
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text
from .config import BOOK_EXTRACTED_MAX_BYTES, BOOK_UPLOAD_MAX_BYTES

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".epub", ".fb2"}
MIN_BOOK_CHARS = 200


class BookError(ValueError):
    """Безопасная ошибка пользовательского книжного ввода."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BookError("Не смог определить кодировку текста.")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", text)]
    normalized = "\n\n".join(part for part in paragraphs if part).strip()
    if len(normalized) < MIN_BOOK_CHARS:
        raise BookError("В книге слишком мало извлекаемого текста.")
    if len(normalized.encode("utf-8")) > BOOK_EXTRACTED_MAX_BYTES:
        raise BookError("Распакованный текст книги превышает допустимый размер.")
    return normalized


def _safe_xml(data: bytes):
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise BookError("XML с DTD или entity не поддерживается.")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise BookError("Повреждённый XML книги.") from exc


def _fb2(data: bytes, fallback_title: str) -> tuple[str, str, str]:
    root = _safe_xml(data)
    title = next(
        (" ".join(node.itertext()).strip() for node in root.iter() if node.tag.endswith("book-title")),
        fallback_title,
    )
    first = next(
        (" ".join(node.itertext()).strip() for node in root.iter() if node.tag.endswith("first-name")),
        "",
    )
    last = next(
        (" ".join(node.itertext()).strip() for node in root.iter() if node.tag.endswith("last-name")),
        "",
    )
    paragraphs = [
        " ".join(node.itertext()).strip()
        for node in root.iter()
        if node.tag.endswith(("p", "subtitle", "title"))
    ]
    return title or fallback_title, " ".join(x for x in (first, last) if x), "\n\n".join(paragraphs)


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    if len(archive.infolist()) > 5000:
        raise BookError("В EPUB слишком много файлов.")
    for info in archive.infolist():
        if "\\" in info.filename or "\x00" in info.filename:
            raise BookError("EPUB содержит небезопасный путь.")
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
            raise BookError("EPUB содержит небезопасный путь.")
        total += max(0, int(info.file_size))
        if total > BOOK_EXTRACTED_MAX_BYTES:
            raise BookError("Распакованный EPUB превышает допустимый размер.")
        members[info.filename] = info
    return members


def _epub(data: bytes, fallback_title: str) -> tuple[str, str, str]:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BookError("Повреждённый EPUB.") from exc
    with archive:
        members = _zip_members(archive)
        container_name = "META-INF/container.xml"
        if container_name not in members:
            raise BookError("В EPUB отсутствует container.xml.")
        try:
            container = _safe_xml(archive.read(container_name))
        except (RuntimeError, zipfile.BadZipFile) as exc:
            raise BookError("Повреждённый или зашифрованный EPUB.") from exc
        rootfile = next(
            (node.attrib.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")),
            None,
        )
        if (
            not rootfile
            or "\\" in rootfile
            or ".." in PurePosixPath(rootfile).parts
            or rootfile not in members
        ):
            raise BookError("В EPUB не найден OPF-манифест.")
        opf = _safe_xml(archive.read(rootfile))
        title = next(
            (" ".join(node.itertext()).strip() for node in opf.iter() if node.tag.endswith("title")),
            fallback_title,
        )
        author = next(
            (" ".join(node.itertext()).strip() for node in opf.iter() if node.tag.endswith("creator")),
            "",
        )
        manifest: dict[str, str] = {}
        spine: list[str] = []
        for node in opf.iter():
            if node.tag.endswith("item") and node.attrib.get("id") and node.attrib.get("href"):
                manifest[node.attrib["id"]] = node.attrib["href"]
            elif node.tag.endswith("itemref") and node.attrib.get("idref"):
                spine.append(node.attrib["idref"])
        base = posixpath.dirname(rootfile)
        parts: list[str] = []
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, href.split("#", 1)[0]))
            if ".." in PurePosixPath(name).parts or name not in members:
                continue
            extractor = _TextExtractor()
            extractor.feed(_decode_text(archive.read(name)))
            if extractor.parts:
                parts.append(" ".join(extractor.parts))
        if not parts:
            raise BookError("В EPUB не найден читаемый текст.")
        return title or fallback_title, author, "\n\n".join(parts)


def _extract(data: bytes, filename: str) -> tuple[str, str, str, str]:
    if not data or len(data) > BOOK_UPLOAD_MAX_BYTES:
        raise BookError("Файл пуст или превышает 20 МБ.")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise BookError("Поддерживаются только TXT, MD, EPUB и FB2.")
    fallback = Path(filename).stem.strip() or "Без названия"
    if suffix in {".txt", ".md"}:
        title, author, text = fallback, "", _decode_text(data)
    elif suffix == ".fb2":
        title, author, text = _fb2(data, fallback)
    else:
        title, author, text = _epub(data, fallback)
    return suffix, title[:300], author[:300], _normalize_text(text)


def ingest(data: bytes, filename: str, *, uploader_uid: int, at: datetime | None = None) -> dict:
    suffix, title, author, text = _extract(data, filename)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    book_id = digest[:16]
    directory = vault.books_dir() / book_id
    metadata_path = directory / "metadata.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        return {**existing, "duplicate": True}
    metadata = {
        "id": book_id,
        "sha256": digest,
        "title": title,
        "author": author,
        "source_filename": Path(filename).name[:300],
        "source_format": suffix.lstrip("."),
        "uploader_uid": int(uploader_uid),
        "uploaded_at": (at or datetime.now()).isoformat(timespec="seconds"),
        "characters": len(text),
    }
    with vault.books_git_wrap(f"upload {book_id}"):
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(directory / f"source{suffix}", data)
        atomic_write_text(directory / "text.txt", text + "\n")
        atomic_write_json(metadata_path, metadata)
    return {**metadata, "duplicate": False}


def list_books() -> list[dict]:
    result: list[dict] = []
    root = vault.books_dir()
    if not root.exists():
        return result
    for path in sorted(root.glob("*/metadata.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                result.append(data)
        except Exception:
            log.exception("book metadata unreadable: %s", path)
    return sorted(result, key=lambda item: str(item.get("title") or "").casefold())


def get_book(book_id: str) -> dict | None:
    safe_id = str(book_id)
    if not re.fullmatch(r"[0-9a-f]{16}", safe_id):
        return None
    path = vault.books_dir() / safe_id / "metadata.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def choose_excerpt(book_id: str, *, rng: random.Random | None = None) -> str:
    path = vault.books_dir() / str(book_id) / "text.txt"
    if not path.exists():
        raise BookError("Текст книги не найден.")
    text = path.read_text(encoding="utf-8")
    paragraphs = [
        " ".join(part.split())
        for part in re.split(r"\n\s*\n", text)
        if 120 <= len(" ".join(part.split())) <= 1200
    ]
    if not paragraphs:
        sentences = re.split(r"(?<=[.!?…])\s+", " ".join(text.split()))
        paragraphs = [
            " ".join(sentences[i : i + 3])
            for i in range(max(0, len(sentences) - 2))
            if 120 <= len(" ".join(sentences[i : i + 3])) <= 1200
        ]
    if not paragraphs:
        raise BookError("Не удалось выбрать содержательную цитату.")
    return (rng or random).choice(paragraphs)


def _book_state(state: dict) -> dict:
    value = state.setdefault("books", {})
    value.setdefault("disabled", [])
    value.setdefault("scores", {})
    value.setdefault("pending_reminder", None)
    value.setdefault("upload_pending_until", None)
    return value


def begin_upload_wait(*, seconds: int) -> None:
    state = vault._load_state()
    _book_state(state)["upload_pending_until"] = (
        datetime.now() + timedelta(seconds=max(1, int(seconds)))
    ).isoformat(timespec="seconds")
    vault._save_state(state)


def upload_waiting(*, now: datetime | None = None) -> bool:
    state = vault._load_state()
    value = _book_state(state).get("upload_pending_until")
    try:
        deadline = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    current = now or datetime.now()
    if current <= deadline:
        return True
    _book_state(state)["upload_pending_until"] = None
    vault._save_state(state)
    return False


def clear_upload_wait() -> None:
    state = vault._load_state()
    books_state = _book_state(state)
    if books_state.get("upload_pending_until") is not None:
        books_state["upload_pending_until"] = None
        vault._save_state(state)


def reminder_enabled(book_id: str) -> bool:
    state = vault._load_state()
    return str(book_id) not in set(_book_state(state)["disabled"])


def set_reminder_enabled(book_id: str, enabled: bool) -> bool:
    if get_book(book_id) is None:
        return False
    state = vault._load_state()
    books_state = _book_state(state)
    disabled = set(str(x) for x in books_state["disabled"])
    if enabled:
        disabled.discard(book_id)
    else:
        disabled.add(book_id)
    books_state["disabled"] = sorted(disabled)
    vault._save_state(state)
    return True


def score(book_id: str) -> int:
    state = vault._load_state()
    try:
        return max(0, int(_book_state(state)["scores"].get(book_id, 0)))
    except (TypeError, ValueError):
        return 0


def choose_for_reminder(*, rng: random.Random | None = None) -> dict | None:
    enabled = [book for book in list_books() if reminder_enabled(str(book["id"]))]
    if not enabled:
        return None
    scores = [score(str(book["id"])) for book in enabled]
    minimum = min(scores)
    weights = [value - minimum + 1 for value in scores]
    return (rng or random).choices(enabled, weights=weights, k=1)[0]


def set_pending_reminder(
    book: dict,
    excerpt: str,
    *,
    message_id: int | None = None,
    raw_event_id: str | None = None,
) -> None:
    state = vault._load_state()
    _book_state(state)["pending_reminder"] = {
        "book_id": str(book["id"]),
        "title": str(book.get("title") or ""),
        "author": str(book.get("author") or ""),
        "excerpt": excerpt,
        "message_id": message_id,
        "raw_event_id": raw_event_id,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
    }
    vault._save_state(state)


def pending_reminder() -> dict | None:
    value = _book_state(vault._load_state()).get("pending_reminder")
    return value if isinstance(value, dict) and value.get("book_id") else None


def consume_pending_reminder() -> dict | None:
    state = vault._load_state()
    books_state = _book_state(state)
    pending = books_state.get("pending_reminder")
    if not isinstance(pending, dict) or not pending.get("book_id"):
        return None
    book_id = str(pending["book_id"])
    scores = books_state["scores"]
    scores[book_id] = max(0, int(scores.get(book_id, 0))) + 1
    books_state["pending_reminder"] = None
    vault._save_state(state)
    return pending


def expire_pending_reminder() -> None:
    state = vault._load_state()
    books_state = _book_state(state)
    if books_state.get("pending_reminder") is not None:
        books_state["pending_reminder"] = None
        vault._save_state(state)
