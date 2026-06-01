"""Валидация и санитизация пользовательского и LLM-ввода.

Контекст угроз (PoC B, Telegram-бот, один-владелец):

* **Path traversal через slug.** LLM может вернуть ``slug="../../etc/passwd"``,
  и ``_path_for(slug, domain)`` запишет файл вне ``02_concepts/<domain>/``. Все
  slug-и проходят через ``safe_slug`` → kebab-case, только ASCII, max 80.
* **Newline-injection в raw-логе.** Парсер ``_ENTRY_RE`` находит запись по
  ``^## Q\\d+ ·``. Если в ответе пользователя есть ``\\n## Q42 ...``, парсер
  раздвоит запись. ``escape_raw_block`` экранирует подобные строки.
* **YAML/Markdown injection в концепте.** Имя/summary/evidence идут в тело
  ``.md`` и (для evidence) внутрь спецстрочки с «». Чистим перевод строки и
  спецсимволы по месту.
* **DoS-длиной.** Telegram сам режет сообщения по 4096, но LLM может вернуть
  огромное summary или ответ пользователя может быть мегабайтным. Жёсткие
  лимиты ниже.

Все функции — pure, без сайд-эффектов. Логирование санитизации делает
вызывающий код (handlers/graph/vault), потому что только он знает контекст.
"""
from __future__ import annotations

import html
import re
import unicodedata

# Жёсткие лимиты. Числа подобраны под человеческий чат + чтобы влезть в окно
# контекста LLM и не пухнуть в vault.
MAX_USER_TEXT = 10_000          # ответ пользователя в /answer / реплика в сессии
MAX_QUESTION_TEXT = 2_000       # /requestion + вопросы от LLM
MAX_SLUG_LEN = 80
MAX_NAME_LEN = 200
MAX_SUMMARY_LEN = 1_500
MAX_EVIDENCE_TEXT = 800
MAX_OPEN_QUESTION = 400

# slug: ASCII lowercase + цифры + дефис, без ведущего/закрывающего дефиса.
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SLUG_KEEP_RE = re.compile(r"[^a-z0-9-]+")

# Строки в ответе пользователя, которые ломают парсер raw-блоков.
_RAW_BLOCK_HEAD_RE = re.compile(r"(?m)^(##\s+Q\d+\s*[·\-—])")
# Защита от попыток подделать **Q:** / **A:** в теле ответа.
_QA_PREFIX_RE = re.compile(r"(?m)^(\*\*[QA]:\*\*)")

# Закрывающие frontmatter delimiters внутри значений — иначе ломают YAML-парсер.
_FRONTMATTER_DELIM_RE = re.compile(r"(?m)^---\s*$")


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    # Стараемся резать по слову, если есть пробел в последних 50 символах.
    head = s[:limit]
    sp = head.rfind(" ", max(0, limit - 50))
    if sp > 0:
        head = head[:sp]
    return head.rstrip() + "…"


def safe_slug(raw: str) -> str:
    """Привести строку к канонической slug-форме или вернуть пустую строку.

    Алгоритм:
    1. Unicode NFKD → отбросить combining-знаки.
    2. Lowercase.
    3. Все символы кроме ``[a-z0-9-]`` → ``-``.
    4. Свернуть подряд идущие дефисы и убрать ведущие/закрывающие.
    5. Обрезать до ``MAX_SLUG_LEN``, снова strip(``-``).

    Если результат пуст или не матчит ``_SLUG_RE`` (на случай если входная строка
    была одними дефисами) — вернуть ``""``. Вызывающий решает что делать с
    пустым slug (пропустить концепт, попросить LLM переформулировать, и т.п.).
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = _SLUG_KEEP_RE.sub("-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > MAX_SLUG_LEN:
        s = s[:MAX_SLUG_LEN].rstrip("-")
    return s if _SLUG_RE.fullmatch(s) else ""


# Транслитерация ru→latin для вывода slug ИЗ имени концепта на стороне кода
# (LLM больше не присылает slug — только имя). Простая BGN/PCGN-подобная карта.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(raw: str) -> str:
    """Имя концепта → канонический ascii-slug (транслит + safe_slug).

    LLM присылает только русское ``name``; slug (имя файла черновика, ascii —
    его требует ``save_concept``) выводит код, детерминированно. Пустую строку
    возвращаем, если после чистки ничего не осталось — вызывающий решает.
    """
    if not raw:
        return ""
    s = "".join(_TRANSLIT.get(ch, _TRANSLIT.get(ch.lower(), ch)) if ch.lower() in _TRANSLIT else ch for ch in raw.lower())
    return safe_slug(s)


def safe_name(raw: str) -> str:
    """Имя концепта: одна строка, без \\n, ограниченная длина."""
    if not raw:
        return ""
    s = str(raw).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return _truncate(s, MAX_NAME_LEN)


def safe_summary(raw: str) -> str:
    """Summary: многострочный текст, без YAML-разделителей, ограниченная длина."""
    if not raw:
        return ""
    s = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    # Не даём вписать `---` своей строкой — иначе пользователь увидит мусор в
    # frontmatter при чтении файла глазами (на сам YAML это не повлияет — он
    # уже спаршен, но визуально путает).
    s = _FRONTMATTER_DELIM_RE.sub("———", s)
    s = s.strip()
    return _truncate(s, MAX_SUMMARY_LEN)


def safe_evidence_text(raw: str) -> str:
    """Цитата/перефраз для evidence-строки. Заворачивается в «» в _render,
    поэтому убираем сами «» из тела и переводы строк."""
    if not raw:
        return ""
    s = str(raw).replace("\r", " ").replace("\n", " ")
    s = s.replace("«", '"').replace("»", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return _truncate(s, MAX_EVIDENCE_TEXT)


def safe_open_question(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return _truncate(s, MAX_OPEN_QUESTION)


def safe_user_text(raw: str, limit: int = MAX_USER_TEXT) -> tuple[str, bool]:
    """Ответ пользователя для записи в 00_raw и подачи в LLM.

    Возвращает ``(text, truncated)`` — флаг говорит, что текст был обрезан.
    Перевод строк сохраняется (это нужно для читаемости ответа), но
    нормализуется к ``\\n``; нулевые байты и control-символы (кроме \\t и \\n)
    выкидываются.
    """
    if not raw:
        return "", False
    s = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or (ord(ch) >= 0x20 and ord(ch) != 0x7f))
    truncated = len(s) > limit
    if truncated:
        s = _truncate(s, limit)
    return s.strip(), truncated


def safe_question_text(raw: str) -> str:
    """Вопрос (от пользователя через /requestion или от LLM) — одна-две строки."""
    if not raw:
        return ""
    s, _ = safe_user_text(raw, limit=MAX_QUESTION_TEXT)
    # Сжимаем многострочный вопрос в одну линию: вопросы в Telegram обычно
    # короткие, многострочные легко портят форматирование.
    s = re.sub(r"\n+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_KEEP_PUNCT = {".", ",", "?"}
_COMMENT_KEEP_PUNCT = {".", "?"}


def strip_extra_punctuation(text: str) -> str:
    """Legacy-очистка текста Иуды, где запятая ещё сохраняется.

    Новые комментарии/реакции должны использовать `strip_comment_punctuation`;
    вопросы и `/about` больше не проходят через этот sanitizer.
    """
    if not text:
        return ""
    s = str(text).replace("…", ".")
    s = "".join(ch if (ch.isalnum() or ch.isspace() or ch in _KEEP_PUNCT) else " " for ch in s)
    s = re.sub(r"[^\S\n]+", " ", s)          # схлопнуть пробелы/табы, не трогая \n
    s = re.sub(r"\s+([.,?])", r"\1", s)       # убрать пробел перед . , ?
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()


def strip_comment_punctuation(text: str) -> str:
    """Очистить только комментарии/реакции Иуды на ответы пользователя.

    Для комментариев дополнительно убираем запятые: остаются буквы/цифры,
    пробелы/переводы строк и только ``. ?``. Не применять к вопросам, `/about`
    и любым словам пользователя.
    """
    if not text:
        return ""
    s = str(text).replace("…", ".")
    s = "".join(ch if (ch.isalnum() or ch.isspace() or ch in _COMMENT_KEEP_PUNCT) else " " for ch in s)
    s = re.sub(r"[^\S\n]+", " ", s)
    s = re.sub(r"\s+([.?])", r"\1", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()


def safe_chat_html(text: str) -> str:
    """Текст ОТ LLM для показа в Telegram с ``parse_mode='HTML'``.

    Гарантия: что бы модель ни сгенерировала — HTML-теги, markdown, фрагмент
    «кода» — Telegram покажет это как ОБЫЧНЫЙ ТЕКСТ и не интерпретирует как
    разметку. Экранируем ``& < >`` (``html.escape``) и выкидываем control-символы
    (кроме ``\\n``/``\\t``). Вывод LLM нигде в пайплайне не исполняется — только
    пишется в файлы (через safe_*-санитайзеры) или показывается (через эту
    функцию). Никаких eval/exec/инструментов у модели нет.
    """
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or (ord(ch) >= 0x20 and ord(ch) != 0x7f))
    return html.escape(s)


def escape_raw_block(text: str) -> str:
    """Защита парсера raw-логов: экранирует строки, имитирующие границу блока.

    Парсер ``_ENTRY_RE`` ищет ``^## Q\\d+ ·`` и ``^**Q:**`` / ``^**A:**``.
    Если ответ пользователя содержит такие строки, они подделают разметку.
    Префиксуем нулевой ширины ``\\u200b`` — невидим для пользователя, но
    ломает regex-якорь.
    """
    if not text:
        return ""
    s = _RAW_BLOCK_HEAD_RE.sub(r"​\1", text)
    s = _QA_PREFIX_RE.sub(r"​\1", s)
    return s


def is_valid_telegram_command_arg(raw: str, max_len: int = 200) -> bool:
    """Базовая проверка аргумента команды (для domain/slug-хинтов).

    Не пропускает: пустые, слишком длинные, содержащие control-символы
    (кроме пробела/таба), путевые сепараторы, шеллы.
    """
    if not raw or len(raw) > max_len:
        return False
    for ch in raw:
        if ch in {"\x00", "/", "\\", "|", ";", "&", "$", "`", "\n", "\r"}:
            return False
        if ord(ch) < 0x20 and ch not in {"\t", " "}:
            return False
    return True
