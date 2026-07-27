"""Общая библиотека книг с детерминированным структурным индексом.

EPUB никогда не извлекается на диск: ZIP-члены проверяются и читаются в памяти.
FB2 и EPUB считаются недоверенным XML/HTML-вводом. LLM в этом модуле не
используется: она получает только уже выбранный :class:`BookExcerpt`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import random
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree

from markdown_it import MarkdownIt

from . import vault
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text
from .config import BOOK_EXTRACTED_MAX_BYTES, BOOK_UPLOAD_MAX_BYTES

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".epub", ".fb2"}
SUPPORTED_FORMATS = {suffix.lstrip(".") for suffix in SUPPORTED_EXTENSIONS}
STRUCTURE_VERSION = 1
PARSER_VERSION = 1
MIN_BOOK_CHARS = 200
MIN_EXCERPT_CHARS = 120
MAX_EXCERPT_CHARS = 1200
MAX_EXCERPT_CANDIDATES = 10_000
BOOK_ID_RE = re.compile(r"[0-9a-f]{16}")
CHAPTER_ID_RE = re.compile(r"[0-9a-f]{16}")


class BookError(ValueError):
    """Безопасная ошибка пользовательского книжного ввода."""


@dataclass(frozen=True)
class ParsedParagraph:
    text: str
    section_path: tuple[str, ...] = ()
    source_locator: str = ""


@dataclass
class ParsedChapter:
    title: str
    source_locator: str
    paragraphs: list[ParsedParagraph] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)


@dataclass
class ParsedBook:
    suffix: str
    title: str
    author: str
    chapters: list[ParsedChapter]


@dataclass(frozen=True)
class BookExcerpt:
    """Дословный фрагмент и его положение в структуре книги."""

    text: str
    chapter_id: str
    chapter_title: str
    section_path: tuple[str, ...]
    source_locator: str

    def metadata(self) -> dict:
        return {
            "excerpt": self.text,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "section_path": list(self.section_path),
            "source_locator": self.source_locator,
        }


@dataclass(frozen=True)
class _HTMLBlock:
    tag: str
    text: str
    anchor: str
    heading_level: int | None


class _XHTMLExtractor(HTMLParser):
    """Извлекает только читаемые блочные элементы и их ближайшие anchors."""

    _IGNORED = {"head", "script", "style", "nav", "svg", "noscript"}
    _BLOCKS = {"p", "li", "blockquote", "pre", "dt", "dd"}
    _HEADINGS = {f"h{level}" for level in range(1, 7)}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_HTMLBlock] = []
        self._ignored_depth = 0
        self._active_anchor = ""
        self._block_tag: str | None = None
        self._block_anchor = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value or "" for key, value in attrs}
        anchor = values.get("id") or values.get("name")
        if anchor:
            self._active_anchor = anchor[:300]
        if tag in self._IGNORED:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "br" and self._block_tag is not None:
            self._parts.append(" ")
            return
        if self._block_tag is None and tag in self._BLOCKS | self._HEADINGS:
            self._block_tag = tag
            self._block_anchor = self._active_anchor
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth or self._block_tag != tag:
            return
        text = _clean_text("".join(self._parts))
        if text:
            level = int(tag[1]) if tag in self._HEADINGS else None
            self.blocks.append(_HTMLBlock(tag, text, self._block_anchor, level))
        self._block_tag = None
        self._block_anchor = ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._block_tag is not None:
            self._parts.append(data)


def _clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace("\x00", "").split()).strip()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BookError("Не смог определить кодировку текста.")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    paragraphs = [_clean_text(part) for part in re.split(r"\n\s*\n", text)]
    normalized = "\n\n".join(part for part in paragraphs if part).strip()
    if len(normalized) < MIN_BOOK_CHARS:
        raise BookError("В книге слишком мало извлекаемого текста.")
    if len(normalized.encode("utf-8")) > BOOK_EXTRACTED_MAX_BYTES:
        raise BookError("Распакованный текст книги превышает допустимый размер.")
    return normalized


def _reject_unsafe_xml_declarations(data: bytes, *, allow_html_doctype: bool = False) -> None:
    upper = data.upper()
    if b"<!ENTITY" in upper:
        raise BookError("XML с DTD или entity не поддерживается.")
    checked = (
        re.sub(br"<!DOCTYPE\s+html\s*>", b"", data, flags=re.IGNORECASE)
        if allow_html_doctype
        else data
    )
    if b"<!DOCTYPE" in checked.upper():
        raise BookError("XML с DTD или entity не поддерживается.")


def _safe_xml(data: bytes, *, allow_html_doctype: bool = False):
    _reject_unsafe_xml_declarations(data, allow_html_doctype=allow_html_doctype)
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise BookError("Повреждённый XML книги.") from exc


def _local(value: str) -> str:
    return value.rsplit("}", 1)[-1].lower()


def _node_text(node) -> str:
    return _clean_text(" ".join(node.itertext()))


def _direct(node, name: str) -> list:
    return [child for child in list(node) if _local(str(child.tag)) == name]


def _first_descendant(node, name: str):
    return next((item for item in node.iter() if _local(str(item.tag)) == name), None)


def _fb2_title(section, fallback: str) -> str:
    title_node = next(iter(_direct(section, "title")), None)
    return (_node_text(title_node) if title_node is not None else "") or fallback


def _fb2_collect(
    node,
    *,
    path: tuple[str, ...],
    locator: str,
    paragraphs: list[ParsedParagraph],
    sections: list[dict],
    depth: int,
) -> None:
    name = _local(str(node.tag))
    if name == "section":
        title = _fb2_title(node, f"Раздел {len(sections) + 1}")
        current_path = (*path, title)
        sections.append(
            {
                "title": title[:300],
                "level": depth,
                "source_locator": locator,
            }
        )
        for index, child in enumerate(list(node)):
            child_name = _local(str(child.tag))
            if child_name == "title":
                continue
            _fb2_collect(
                child,
                path=current_path,
                locator=f"{locator}/{child_name}[{index}]",
                paragraphs=paragraphs,
                sections=sections,
                depth=depth + (1 if child_name == "section" else 0),
            )
        return
    if name in {"p", "subtitle", "text-author", "v"}:
        text = _node_text(node)
        if text:
            paragraphs.append(ParsedParagraph(text, path, locator))
        return
    for index, child in enumerate(list(node)):
        child_name = _local(str(child.tag))
        if child_name == "title":
            continue
        _fb2_collect(
            child,
            path=path,
            locator=f"{locator}/{child_name}[{index}]",
            paragraphs=paragraphs,
            sections=sections,
            depth=depth,
        )


def _fb2(data: bytes, fallback_title: str) -> ParsedBook:
    root = _safe_xml(data)
    description = _first_descendant(root, "description")
    title_info = _first_descendant(description, "title-info") if description is not None else None
    book_title = _first_descendant(title_info, "book-title") if title_info is not None else None
    title = (_node_text(book_title) if book_title is not None else "") or fallback_title

    authors: list[str] = []
    if title_info is not None:
        for author_node in (
            node for node in title_info.iter() if _local(str(node.tag)) == "author"
        ):
            parts = []
            for part_name in ("first-name", "middle-name", "last-name", "nickname"):
                part = next(iter(_direct(author_node, part_name)), None)
                value = _node_text(part) if part is not None else ""
                if value:
                    parts.append(value)
            if parts:
                authors.append(" ".join(parts))

    bodies = [node for node in root.iter() if _local(str(node.tag)) == "body"]
    if not bodies:
        raise BookError("В FB2 отсутствует основной текст.")
    primary = next(
        (
            body
            for body in bodies
            if str(body.attrib.get("name") or "").casefold() not in {"notes", "comments"}
        ),
        bodies[0],
    )
    chapters: list[ParsedChapter] = []
    loose: list[ParsedParagraph] = []
    loose_sections: list[dict] = []
    for index, child in enumerate(list(primary)):
        child_name = _local(str(child.tag))
        locator = f"body[0]/{child_name}[{index}]"
        if child_name == "section":
            chapter_title = _fb2_title(child, f"Глава {len(chapters) + 1}")
            chapter = ParsedChapter(chapter_title[:300], locator)
            _fb2_collect(
                child,
                path=(),
                locator=locator,
                paragraphs=chapter.paragraphs,
                sections=chapter.sections,
                depth=1,
            )
            chapters.append(chapter)
        else:
            _fb2_collect(
                child,
                path=(),
                locator=locator,
                paragraphs=loose,
                sections=loose_sections,
                depth=1,
            )
    if loose:
        chapters.insert(0, ParsedChapter("Введение", "body[0]", loose, loose_sections))
    if not chapters:
        raise BookError("В FB2 не найден читаемый текст.")
    return ParsedBook(".fb2", title[:300], "; ".join(authors)[:300], chapters)


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    if len(archive.infolist()) > 5000:
        raise BookError("В EPUB слишком много файлов.")
    for info in archive.infolist():
        if info.flag_bits & 0x1:
            raise BookError("Зашифрованный EPUB не поддерживается.")
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


def _member_path(base: str, href: str, members: dict[str, zipfile.ZipInfo]) -> tuple[str, str]:
    decoded = unquote(str(href)).split("?", 1)[0]
    file_part, _, fragment = decoded.partition("#")
    if "\\" in file_part or "\x00" in file_part:
        raise BookError("EPUB содержит небезопасную ссылку.")
    name = posixpath.normpath(posixpath.join(base, file_part))
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise BookError("EPUB содержит небезопасную ссылку.")
    if name not in members:
        raise BookError("EPUB ссылается на отсутствующий файл.")
    return name, fragment


def _read_xhtml(archive: zipfile.ZipFile, name: str) -> list[_HTMLBlock]:
    try:
        data = archive.read(name)
    except (RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        raise BookError("Повреждённый или зашифрованный EPUB.") from exc
    _reject_unsafe_xml_declarations(data, allow_html_doctype=True)
    extractor = _XHTMLExtractor()
    try:
        extractor.feed(_decode_text(data))
        extractor.close()
    except Exception as exc:
        raise BookError("Повреждённый XHTML в EPUB.") from exc
    return extractor.blocks


def _toc_links(
    root,
    *,
    base: str,
    members: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    nav_nodes = [node for node in root.iter() if _local(str(node.tag)) == "nav"]
    for nav in nav_nodes:
        nav_type = " ".join(
            str(value) for key, value in nav.attrib.items() if _local(str(key)) == "type"
        ).casefold()
        if nav_type and "toc" not in nav_type:
            continue
        ol = next(iter(_direct(nav, "ol")), None)
        if ol is None:
            continue
        for item in _direct(ol, "li"):
            link = _first_descendant(item, "a")
            href = str(link.attrib.get("href") or "") if link is not None else ""
            title = _node_text(link) if link is not None else ""
            if not href:
                continue
            try:
                name, fragment = _member_path(base, href, members)
            except BookError:
                continue
            result.append((title or Path(name).stem, name, fragment))
        if result:
            return result
    return result


def _ncx_links(
    root,
    *,
    base: str,
    members: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, str, str]]:
    nav_map = _first_descendant(root, "navmap")
    if nav_map is None:
        return []
    result: list[tuple[str, str, str]] = []
    for point in _direct(nav_map, "navpoint"):
        label = _first_descendant(point, "navlabel")
        content = _first_descendant(point, "content")
        href = str(content.attrib.get("src") or "") if content is not None else ""
        if not href:
            continue
        try:
            name, fragment = _member_path(base, href, members)
        except BookError:
            continue
        result.append((_node_text(label) or Path(name).stem, name, fragment))
    return result


def _chapter_from_html(
    blocks: list[tuple[str, _HTMLBlock]],
    *,
    title: str,
    locator: str,
) -> ParsedChapter:
    chapter = ParsedChapter((title or "Без названия")[:300], locator)
    headings: list[tuple[int, str]] = []
    for index, (name, block) in enumerate(blocks):
        source_locator = f"{name}#{block.anchor or index}"
        if block.heading_level is not None:
            while headings and headings[-1][0] >= block.heading_level:
                headings.pop()
            headings.append((block.heading_level, block.text[:300]))
            chapter.sections.append(
                {
                    "title": block.text[:300],
                    "level": block.heading_level,
                    "source_locator": source_locator,
                }
            )
            continue
        chapter.paragraphs.append(
            ParsedParagraph(
                block.text,
                tuple(value for _, value in headings),
                source_locator,
            )
        )
    return chapter


def _epub(data: bytes, fallback_title: str) -> ParsedBook:
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
            (
                node.attrib.get("full-path")
                for node in container.iter()
                if _local(str(node.tag)) == "rootfile"
            ),
            None,
        )
        if not rootfile:
            raise BookError("В EPUB не найден OPF-манифест.")
        rootfile, _ = _member_path("", str(rootfile), members)
        opf = _safe_xml(archive.read(rootfile))
        title_node = _first_descendant(opf, "title")
        author_node = _first_descendant(opf, "creator")
        title = (_node_text(title_node) if title_node is not None else "") or fallback_title
        author = _node_text(author_node) if author_node is not None else ""

        manifest: dict[str, dict[str, str]] = {}
        spine: list[tuple[str, bool]] = []
        for node in opf.iter():
            name = _local(str(node.tag))
            if name == "item" and node.attrib.get("id") and node.attrib.get("href"):
                manifest[str(node.attrib["id"])] = {
                    "href": str(node.attrib["href"]),
                    "media_type": str(node.attrib.get("media-type") or ""),
                    "properties": str(node.attrib.get("properties") or ""),
                }
            elif name == "itemref" and node.attrib.get("idref"):
                spine.append(
                    (
                        str(node.attrib["idref"]),
                        str(node.attrib.get("linear") or "yes").casefold() != "no",
                    )
                )
        opf_base = posixpath.dirname(rootfile)
        documents: list[tuple[str, list[_HTMLBlock]]] = []
        document_names: set[str] = set()
        for item_id, linear in spine:
            item = manifest.get(item_id)
            if not item or not linear:
                continue
            try:
                name, _ = _member_path(opf_base, item["href"], members)
            except BookError:
                continue
            media_type = item["media_type"].casefold()
            if (
                media_type
                and "html" not in media_type
                and not name.casefold().endswith((".xhtml", ".html", ".htm"))
            ):
                continue
            if "nav" in item["properties"].casefold().split():
                continue
            blocks = _read_xhtml(archive, name)
            if blocks:
                documents.append((name, blocks))
                document_names.add(name)
        if not documents:
            raise BookError("В EPUB не найден читаемый текст.")

        toc: list[tuple[str, str, str]] = []
        nav_item = next(
            (
                item
                for item in manifest.values()
                if "nav" in item["properties"].casefold().split()
            ),
            None,
        )
        if nav_item:
            try:
                nav_name, _ = _member_path(opf_base, nav_item["href"], members)
                toc = _toc_links(
                    _safe_xml(archive.read(nav_name), allow_html_doctype=True),
                    base=posixpath.dirname(nav_name),
                    members=members,
                )
            except BookError:
                toc = []
        if not toc:
            ncx_item = next(
                (
                    item
                    for item in manifest.values()
                    if item["media_type"].casefold() == "application/x-dtbncx+xml"
                ),
                None,
            )
            if ncx_item:
                try:
                    ncx_name, _ = _member_path(opf_base, ncx_item["href"], members)
                    toc = _ncx_links(
                        _safe_xml(archive.read(ncx_name)),
                        base=posixpath.dirname(ncx_name),
                        members=members,
                    )
                except BookError:
                    toc = []

        global_blocks: list[tuple[str, _HTMLBlock]] = []
        doc_start: dict[str, int] = {}
        for name, blocks in documents:
            doc_start[name] = len(global_blocks)
            global_blocks.extend((name, block) for block in blocks)

        starts: list[tuple[int, str, str]] = []
        for toc_title, name, fragment in toc:
            if name not in document_names:
                continue
            start = doc_start[name]
            if fragment:
                start = next(
                    (
                        index
                        for index, (block_name, block) in enumerate(global_blocks)
                        if block_name == name and block.anchor == fragment
                    ),
                    start,
                )
            starts.append((start, toc_title, f"{name}#{fragment}" if fragment else name))
        unique_starts: dict[int, tuple[str, str]] = {}
        for start, toc_title, locator in starts:
            unique_starts.setdefault(start, (toc_title, locator))
        ordered = sorted((start, *value) for start, value in unique_starts.items())

        chapters: list[ParsedChapter] = []
        if ordered:
            for index, (start, chapter_title, locator) in enumerate(ordered):
                end = ordered[index + 1][0] if index + 1 < len(ordered) else len(global_blocks)
                chapter = _chapter_from_html(
                    global_blocks[start:end],
                    title=chapter_title,
                    locator=locator,
                )
                if chapter.paragraphs:
                    chapters.append(chapter)
        if not chapters:
            for name, blocks in documents:
                first_heading = next(
                    (block.text for block in blocks if block.heading_level is not None),
                    Path(name).stem,
                )
                chapter = _chapter_from_html(
                    [(name, block) for block in blocks],
                    title=first_heading,
                    locator=name,
                )
                if chapter.paragraphs:
                    chapters.append(chapter)
        if not chapters:
            raise BookError("В EPUB не найден читаемый текст.")
        return ParsedBook(".epub", title[:300], author[:300], chapters)


def _inline_text(token) -> str:
    if not token.children:
        return _clean_text(token.content)
    parts: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif child.type == "image":
            parts.append(child.content)
    return _clean_text("".join(parts))


def _markdown_blocks(text: str) -> list[dict]:
    parser = MarkdownIt("commonmark", {"html": False})
    tokens = parser.parse(text)
    blocks: list[dict] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open" and index + 1 < len(tokens):
            inline = tokens[index + 1]
            value = _inline_text(inline)
            if value:
                blocks.append(
                    {
                        "kind": "heading",
                        "level": int(token.tag[1]),
                        "text": value,
                        "locator": f"line:{(token.map or [0])[0] + 1}",
                    }
                )
        elif token.type == "paragraph_open" and index + 1 < len(tokens):
            inline = tokens[index + 1]
            value = _inline_text(inline)
            if value:
                blocks.append(
                    {
                        "kind": "paragraph",
                        "level": None,
                        "text": value,
                        "locator": f"line:{(token.map or [0])[0] + 1}",
                    }
                )
        index += 1
    return blocks


def _markdown(data: bytes, fallback_title: str, suffix: str) -> ParsedBook:
    blocks = _markdown_blocks(_decode_text(data))
    if not blocks:
        raise BookError("В Markdown не найден читаемый текст.")
    headings = [block for block in blocks if block["kind"] == "heading"]
    title = fallback_title
    if (
        blocks[0]["kind"] == "heading"
        and blocks[0]["level"] == 1
        and sum(1 for block in headings if block["level"] == 1) == 1
        and all(block["level"] > 1 for block in headings[1:])
    ):
        title = str(blocks[0]["text"])
        blocks = blocks[1:]
        headings = [block for block in blocks if block["kind"] == "heading"]

    if not headings:
        paragraphs = [
            ParsedParagraph(str(block["text"]), (), str(block["locator"]))
            for block in blocks
            if block["kind"] == "paragraph"
        ]
        return ParsedBook(
            suffix,
            title[:300],
            "",
            [ParsedChapter(title[:300], "document", paragraphs)],
        )

    top_level = min(int(block["level"]) for block in headings)
    chapters: list[ParsedChapter] = []
    current: ParsedChapter | None = None
    section_stack: list[tuple[int, str]] = []
    intro: list[ParsedParagraph] = []
    for block in blocks:
        if block["kind"] == "heading":
            level = int(block["level"])
            heading = str(block["text"])[:300]
            if level == top_level:
                current = ParsedChapter(heading, str(block["locator"]))
                chapters.append(current)
                section_stack = [(level, heading)]
            else:
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_stack.append((level, heading))
                if current is not None:
                    current.sections.append(
                        {
                            "title": heading,
                            "level": level,
                            "source_locator": str(block["locator"]),
                        }
                    )
            continue
        paragraph = ParsedParagraph(
            str(block["text"]),
            tuple(value for _, value in section_stack),
            str(block["locator"]),
        )
        if current is None:
            intro.append(paragraph)
        else:
            current.paragraphs.append(paragraph)
    if intro:
        chapters.insert(0, ParsedChapter("Введение", "document:preamble", intro))
    if not chapters:
        raise BookError("В Markdown не найден читаемый текст.")
    return ParsedBook(suffix, title[:300], "", chapters)


def parse_book(data: bytes, filename: str) -> ParsedBook:
    """Без LLM разобрать поддерживаемый исходник и сохранить верхние главы."""

    if not data or len(data) > BOOK_UPLOAD_MAX_BYTES:
        raise BookError("Файл пуст или превышает 20 МБ.")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise BookError("Поддерживаются только EPUB, FB2 и Markdown.")
    fallback = Path(filename).stem.strip() or "Без названия"
    if suffix == ".fb2":
        parsed = _fb2(data, fallback)
    elif suffix == ".epub":
        parsed = _epub(data, fallback)
    else:
        parsed = _markdown(data, fallback, suffix)
    _normalized_book_text(parsed)
    return parsed


def _normalized_book_text(parsed: ParsedBook) -> str:
    return _normalize_text(
        "\n\n".join(
            paragraph.text
            for chapter in parsed.chapters
            for paragraph in chapter.paragraphs
            if paragraph.text
        )
    )


def _chapter_id(parsed: ParsedBook, chapter: ParsedChapter, order: int) -> str:
    payload = f"{parsed.suffix}\x1f{chapter.source_locator}\x1f{order}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_artifacts(parsed: ParsedBook, source: bytes) -> tuple[str, dict, dict]:
    text = _normalized_book_text(parsed)
    source_digest = hashlib.sha256(source).hexdigest()
    content_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chapters = []
    for order, chapter in enumerate(parsed.chapters):
        chapters.append(
            {
                "id": _chapter_id(parsed, chapter, order),
                "order": order,
                "title": chapter.title[:300],
                "source_locator": chapter.source_locator[:500],
                "sections": chapter.sections,
                "paragraphs": [
                    {
                        "text": paragraph.text,
                        "section_path": list(paragraph.section_path),
                        "source_locator": paragraph.source_locator[:500],
                    }
                    for paragraph in chapter.paragraphs
                ],
            }
        )
    structure = {
        "version": STRUCTURE_VERSION,
        "parser_version": PARSER_VERSION,
        "source_format": parsed.suffix.lstrip("."),
        "source_sha256": source_digest,
        "content_sha256": content_digest,
        "chapters": chapters,
    }
    fields = {
        "source_sha256": source_digest,
        "content_sha256": content_digest,
        "structure_version": STRUCTURE_VERSION,
        "parser_version": PARSER_VERSION,
        "chapter_count": len(chapters),
        "characters": len(text),
    }
    return text, structure, fields


def _extract(data: bytes, filename: str) -> tuple[str, str, str, str]:
    """Компактный compatibility helper для тестов и миграционных проверок."""

    parsed = parse_book(data, filename)
    return parsed.suffix, parsed.title, parsed.author, _normalized_book_text(parsed)


def _metadata_path(book_id: str) -> Path:
    return vault.books_dir() / book_id / "metadata.json"


def _read_metadata(path: Path) -> dict | None:
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        book_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        if (
            isinstance(data, dict)
            and BOOK_ID_RE.fullmatch(book_id)
            and path.parent.name == book_id
        ):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def scan_books() -> list[dict]:
    """Вернуть валидные metadata, включая ещё не переиндексированные и TXT."""

    root = vault.books_dir()
    if not root.exists():
        return []
    result = []
    for path in sorted(root.glob("*/metadata.json")):
        data = _read_metadata(path)
        if data is None:
            log.warning("book metadata rejected: %s", path)
        else:
            result.append(data)
    return result


def _read_structure(metadata: dict) -> dict | None:
    book_id = str(metadata.get("id") or "")
    path = vault.books_dir() / book_id / "structure.json"
    if (
        str(metadata.get("source_format") or "").casefold() not in SUPPORTED_FORMATS
        or metadata.get("structure_version") != STRUCTURE_VERSION
        or metadata.get("parser_version") != PARSER_VERSION
        or not path.exists()
        or path.is_symlink()
        or path.parent.is_symlink()
    ):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != STRUCTURE_VERSION
        or data.get("parser_version") != PARSER_VERSION
        or data.get("source_format") != metadata.get("source_format")
        or data.get("source_sha256") != metadata.get("source_sha256")
        or data.get("content_sha256") != metadata.get("content_sha256")
        or not isinstance(data.get("chapters"), list)
        or not data["chapters"]
    ):
        return None

    chapter_ids: set[str] = set()
    has_text = False
    for chapter in data["chapters"]:
        if not isinstance(chapter, dict):
            return None
        chapter_id = str(chapter.get("id") or "")
        paragraphs = chapter.get("paragraphs")
        sections = chapter.get("sections")
        if (
            not CHAPTER_ID_RE.fullmatch(chapter_id)
            or chapter_id in chapter_ids
            or not isinstance(chapter.get("title"), str)
            or not isinstance(chapter.get("source_locator"), str)
            or not isinstance(paragraphs, list)
            or not isinstance(sections, list)
        ):
            return None
        chapter_ids.add(chapter_id)
        for section in sections:
            if (
                not isinstance(section, dict)
                or not isinstance(section.get("title"), str)
                or not isinstance(section.get("level"), int)
                or not isinstance(section.get("source_locator"), str)
            ):
                return None
        for paragraph in paragraphs:
            if (
                not isinstance(paragraph, dict)
                or not isinstance(paragraph.get("text"), str)
                or not paragraph["text"].strip()
                or not isinstance(paragraph.get("section_path"), list)
                or not all(
                    isinstance(value, str) for value in paragraph["section_path"]
                )
                or not isinstance(paragraph.get("source_locator"), str)
            ):
                return None
            has_text = True
    return data if has_text else None


def _structure_ready(metadata: dict) -> bool:
    return _read_structure(metadata) is not None


def ingest(data: bytes, filename: str, *, uploader_uid: int, at: datetime | None = None) -> dict:
    parsed = parse_book(data, filename)
    text, structure, fields = _build_artifacts(parsed, data)
    digest = fields["content_sha256"]

    for existing in scan_books():
        if (
            _structure_ready(existing)
            and str(existing.get("content_sha256") or existing.get("sha256") or "") == digest
        ):
            return {**existing, "duplicate": True}

    book_id = digest[:16]
    directory = vault.books_dir() / book_id
    metadata_path = directory / "metadata.json"
    if directory.is_symlink() or metadata_path.is_symlink():
        raise BookError("Каталог книги небезопасен.")
    if metadata_path.exists():
        existing = _read_metadata(metadata_path)
        if existing and str(existing.get("sha256") or "") == digest:
            raise BookError("Книга уже есть и ожидает переиндексации.")
        raise BookError("Обнаружен конфликт идентификатора книги.")
    if directory.exists() and not directory.is_dir():
        raise BookError("Путь книги занят небезопасным объектом.")
    metadata = {
        "id": book_id,
        "sha256": digest,
        "title": parsed.title,
        "author": parsed.author,
        "source_filename": Path(filename).name[:300],
        "source_format": parsed.suffix.lstrip("."),
        "uploader_uid": int(uploader_uid),
        "uploaded_at": (at or datetime.now()).isoformat(timespec="seconds"),
        "indexed_at": (at or datetime.now()).isoformat(timespec="seconds"),
        **fields,
    }
    with vault.books_git_wrap(f"upload {book_id}"):
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(directory / f"source{parsed.suffix}", data)
        atomic_write_text(directory / "text.txt", text + "\n")
        atomic_write_json(directory / "structure.json", structure)
        atomic_write_json(metadata_path, metadata)
    return {**metadata, "duplicate": False}


def list_books() -> list[dict]:
    return sorted(
        (item for item in scan_books() if _structure_ready(item)),
        key=lambda item: str(item.get("title") or "").casefold(),
    )


def get_book(book_id: str) -> dict | None:
    safe_id = str(book_id)
    if not BOOK_ID_RE.fullmatch(safe_id):
        return None
    data = _read_metadata(_metadata_path(safe_id))
    return data if data is not None and _structure_ready(data) else None


def _source_path(metadata: dict) -> Path:
    book_id = str(metadata.get("id") or "")
    if not BOOK_ID_RE.fullmatch(book_id):
        raise BookError("Некорректный идентификатор книги.")
    directory = vault.books_dir() / book_id
    source_format = str(metadata.get("source_format") or "").casefold().lstrip(".")
    expected = directory / f"source.{source_format}"
    if expected.exists() and not expected.is_symlink() and not directory.is_symlink():
        return expected
    candidates = [
        path
        for path in directory.glob("source.*")
        if path.is_file() and not path.is_symlink() and not directory.is_symlink()
    ]
    if len(candidates) != 1:
        raise BookError("Сохранённый исходник книги не найден.")
    return candidates[0]


def inspect_saved_book(book_id: str) -> dict:
    """Разобрать сохранённый source без записи и вернуть план переиндексации."""

    safe_id = str(book_id)
    if not BOOK_ID_RE.fullmatch(safe_id):
        raise BookError("Некорректный идентификатор книги.")
    metadata = _read_metadata(_metadata_path(safe_id))
    if metadata is None:
        raise BookError("Metadata книги повреждены.")
    source = _source_path(metadata)
    suffix = source.suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise BookError("Формат исходника больше не поддерживается.")
    data = source.read_bytes()
    original_name = str(metadata.get("source_filename") or "")
    filename = (
        original_name
        if Path(original_name).suffix.casefold() == source.suffix.casefold()
        else source.name
    )
    parsed = parse_book(data, filename)
    _, _, fields = _build_artifacts(parsed, data)
    current = (
        _structure_ready(metadata)
        and metadata.get("source_sha256") == fields["source_sha256"]
        and metadata.get("content_sha256") == fields["content_sha256"]
    )
    return {
        "id": safe_id,
        "source": source.name,
        "format": parsed.suffix.lstrip("."),
        "title": parsed.title,
        "author": parsed.author,
        "chapters": fields["chapter_count"],
        "characters": fields["characters"],
        "status": "current" if current else "reindex",
    }


def reindex_saved_book(book_id: str, *, at: datetime | None = None) -> dict:
    """Построить structure.json из сохранённого source, не меняя book-id."""

    preview = inspect_saved_book(book_id)
    if preview["status"] == "current":
        return preview
    metadata_path = _metadata_path(book_id)
    metadata = _read_metadata(metadata_path)
    if metadata is None:
        raise BookError("Metadata книги повреждены.")
    source = _source_path(metadata)
    data = source.read_bytes()
    original_name = str(metadata.get("source_filename") or "")
    filename = (
        original_name
        if Path(original_name).suffix.casefold() == source.suffix.casefold()
        else source.name
    )
    parsed = parse_book(data, filename)
    _, structure, fields = _build_artifacts(parsed, data)
    updated = {
        **metadata,
        "title": parsed.title,
        "author": parsed.author,
        "source_format": parsed.suffix.lstrip("."),
        "indexed_at": (at or datetime.now()).isoformat(timespec="seconds"),
        **fields,
    }
    with vault.books_git_wrap(f"reindex {book_id}"):
        atomic_write_json(metadata_path.parent / "structure.json", structure)
        atomic_write_json(metadata_path, updated)
    return {**preview, "status": "reindexed"}


def _load_structure(book_id: str) -> dict:
    safe_id = str(book_id)
    if not BOOK_ID_RE.fullmatch(safe_id):
        raise BookError("Книга не найдена или требует переиндексации.")
    metadata = _read_metadata(_metadata_path(safe_id))
    data = _read_structure(metadata) if metadata is not None else None
    if data is None:
        raise BookError("Книга не найдена или структурный индекс повреждён.")
    return data


def _sentence_windows(text: str) -> list[str]:
    sentences = [
        _clean_text(sentence)
        for sentence in re.split(r"(?<=[.!?…])\s+", text)
        if _clean_text(sentence)
    ]
    result: list[str] = []
    for start in range(len(sentences)):
        parts: list[str] = []
        for sentence in sentences[start:]:
            candidate = " ".join([*parts, sentence])
            if len(candidate) > MAX_EXCERPT_CHARS:
                break
            parts.append(sentence)
            if len(candidate) >= MIN_EXCERPT_CHARS:
                result.append(candidate)
                break
    return result


def _excerpt_candidates(book_id: str) -> list[BookExcerpt]:
    structure = _load_structure(str(book_id))
    candidates: list[BookExcerpt] = []
    for chapter in structure["chapters"]:
        if not isinstance(chapter, dict):
            continue
        chapter_id = str(chapter.get("id") or "")
        if not CHAPTER_ID_RE.fullmatch(chapter_id):
            continue
        chapter_title = str(chapter.get("title") or "Без названия")
        raw_paragraphs = chapter.get("paragraphs")
        if not isinstance(raw_paragraphs, list):
            continue
        paragraphs = [item for item in raw_paragraphs if isinstance(item, dict)]
        for start, item in enumerate(paragraphs):
            text = _clean_text(str(item.get("text") or ""))
            path = tuple(str(value) for value in item.get("section_path") or [])
            locator = str(item.get("source_locator") or chapter.get("source_locator") or "")
            if MIN_EXCERPT_CHARS <= len(text) <= MAX_EXCERPT_CHARS:
                candidates.append(BookExcerpt(text, chapter_id, chapter_title, path, locator))
            elif len(text) > MAX_EXCERPT_CHARS:
                candidates.extend(
                    BookExcerpt(value, chapter_id, chapter_title, path, locator)
                    for value in _sentence_windows(text)
                )
            else:
                parts = [text] if text else []
                for following in paragraphs[start + 1 :]:
                    value = _clean_text(str(following.get("text") or ""))
                    candidate = " ".join([*parts, value]).strip()
                    if len(candidate) > MAX_EXCERPT_CHARS:
                        break
                    parts.append(value)
                    if len(candidate) >= MIN_EXCERPT_CHARS:
                        candidates.append(
                            BookExcerpt(
                                candidate,
                                chapter_id,
                                chapter_title,
                                path,
                                locator,
                            )
                        )
                        break
            if len(candidates) >= MAX_EXCERPT_CANDIDATES:
                return candidates
    if not candidates:
        raise BookError("Не удалось выбрать содержательную цитату в пределах главы.")
    return candidates


def choose_excerpt(book_id: str, *, rng: random.Random | None = None) -> BookExcerpt:
    return (rng or random).choice(_excerpt_candidates(book_id))


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
    enabled: list[dict] = []
    for book in list_books():
        book_id = str(book["id"])
        if not reminder_enabled(book_id):
            continue
        try:
            _excerpt_candidates(book_id)
        except BookError:
            log.warning("book excluded from reminders: unusable index id=%s", book_id)
            continue
        enabled.append(book)
    if not enabled:
        return None
    scores = [score(str(book["id"])) for book in enabled]
    minimum = min(scores)
    weights = [value - minimum + 1 for value in scores]
    return (rng or random).choices(enabled, weights=weights, k=1)[0]


def set_pending_reminder(
    book: dict,
    excerpt: BookExcerpt,
    *,
    message_id: int | None = None,
    raw_event_id: str | None = None,
) -> None:
    state = vault._load_state()
    _book_state(state)["pending_reminder"] = {
        "book_id": str(book["id"]),
        "title": str(book.get("title") or ""),
        "author": str(book.get("author") or ""),
        **excerpt.metadata(),
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
