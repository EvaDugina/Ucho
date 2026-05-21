import json
import logging
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from . import userctx
from .atomic import atomic_write_json, atomic_write_text
from .config import DOMAINS, LOG_PATH, PSYCHO_META_DIR, VAULT_PATH
from .validation import escape_raw_block, safe_question_text, safe_user_text

log = logging.getLogger(__name__)


# Per-user пути: зависят от текущего пользователя (userctx). Раньше были
# модульными константами от VAULT_PATH — теперь функции, т.к. у каждого
# пользователя свой `<vault>/users/<uid>/`. `.psycho/` и git — глобальные.
def raw_dir() -> Path:
    return userctx.user_root() / "raw"


def profile_dir() -> Path:
    return userctx.user_root() / "profile"


def notes_dir() -> Path:
    return userctx.user_root() / "notes"


def index_file() -> Path:
    return userctx.user_root() / "_index.md"


def state_file() -> Path:
    return userctx.user_root() / "_state.json"

# Парсер записей вида:
#   ## Q42 · 14:32 · politics
#   **Q:** ...
#   **A:** ...
_ENTRY_RE = re.compile(
    r"##\s+Q(\d+)\s*[·\-—]\s*(\d{2}:\d{2})\s*[·\-—]\s*(\w+)\s*\n"
    r"\*\*Q:\*\*\s*(.*?)\n"
    r"\*\*A:\*\*\s*(.*?)(?=\n##\s+Q\d+\s*[·\-—]|\Z)",
    re.DOTALL,
)

# .gitignore по умолчанию для vault. НЕ синкаем obsidian-кеш и trash.
# .psycho/ — ГЛОБАЛЬНАЯ метаинформация (manifest, log, users.json,
# startup-check), общая для всех пользователей. Её НЕ версионируем: иначе её
# постоянные изменения примешивались бы в пер-юзерные коммиты и нарушали
# изоляцию (один коммит = данные одного пользователя). Содержимое
# `users/<id>/` версионируется.
_DEFAULT_GITIGNORE = """\
# Obsidian local state
.obsidian/workspace*.json
.obsidian/cache
.trash/

# Psycho global metadata (not per-user; kept out of per-user commits)
.psycho/

# OS junk
.DS_Store
Thumbs.db
desktop.ini

# Editor tmp
*.tmp
*.swp
"""


# ---------- git plumbing ----------


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Запустить git-команду в vault. Используется git, доступный в PATH контейнера."""
    return subprocess.run(
        ["git", *args],
        cwd=str(VAULT_PATH),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _is_git_repo() -> bool:
    if not (VAULT_PATH / ".git").exists():
        return False
    try:
        _git("rev-parse", "--is-inside-work-tree")
        return True
    except subprocess.CalledProcessError:
        return False


def ensure_git_repo() -> None:
    """Гарантировать, что vault — git репозиторий.

    Если git не установлен — пишем warning и продолжаем без safety net
    (бот не должен падать только из-за этого; разработчик увидит в логах).
    """
    if not _git_available():
        log.warning("git not available — safety net disabled; install git in the container")
        return
    fresh = not _is_git_repo()
    if fresh:
        log.info("initializing git repo in vault: %s", VAULT_PATH)
        _git("init", "-b", "main", check=False)
        # local identity на случай, если в контейнере не настроен глобальный
        _git("config", "user.email", "psycho-bot@local", check=False)
        _git("config", "user.name", "Psycho Bot", check=False)
    # Гарантируем, что .gitignore существует И содержит правило `.psycho/`.
    # Если файл уже был (типичный случай существующего вольта) — дописываем
    # недостающее правило, иначе глобальная метаинформация осталась бы
    # трекаемой и `git add -A` ниже снова её бы добавил.
    gitignore = VAULT_PATH / ".gitignore"
    if not gitignore.exists():
        atomic_write_text(gitignore, _DEFAULT_GITIGNORE)
    else:
        current = gitignore.read_text(encoding="utf-8")
        if ".psycho/" not in current:
            block = "\n# Psycho global metadata (not per-user; kept out of per-user commits)\n.psycho/\n"
            atomic_write_text(gitignore, current.rstrip("\n") + "\n" + block)
    # .psycho/ — глобальная метаинформация; снимаем с учёта (идемпотентно,
    # --ignore-unmatch → no-op если уже не трекается), чтобы она не
    # примешивалась в пер-юзерные коммиты. Теперь, когда правило в .gitignore
    # есть, последующий `git add -A` её обратно не добавит.
    rm = _git("rm", "-r", "--cached", "--ignore-unmatch", ".psycho", check=False)
    if fresh:
        _git("add", "-A", check=False)
        # --allow-empty: если vault только что инициализирован и пуст
        _git("commit", "-m", "psycho(all): init", "--allow-empty", check=False)
    elif (rm.stdout or "").strip():
        # реально сняли .psycho/ с учёта на уже существующем репо — зафиксируем
        # разово (включая обновлённый .gitignore)
        _git("add", "-A", check=False)
        _git("commit", "-m", "psycho(all): untrack .psycho", check=False)


def _scope() -> tuple[Optional[str], str]:
    """Текущий scope коммита: ``(pathspec, label)``.

    Изоляция пользователей: коммит затрагивает ТОЛЬКО поддерево текущего
    пользователя ``users/<uid>/``, а ``label`` (id пользователя) попадает в
    сообщение коммита. Если пользователь не выставлен (миграции, разовые
    глобальные операции) — ``(None, "all")`` → коммитим весь вольт.
    """
    uid = userctx.current_uid()
    if uid is None:
        return None, "all"
    return f"users/{uid}", str(uid)


def _git_head() -> Optional[str]:
    if not _git_available() or not _is_git_repo():
        return None
    try:
        return _git("rev-parse", "HEAD").stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def _git_commit(
    message: str, scope: Optional[str] = None, allow_empty: bool = False
) -> Optional[str]:
    """Коммит. При ``scope`` затрагивает только это поддерево (``users/<uid>/``),
    иначе — весь вольт. Возвращает sha или None если git недоступен/нечего коммитить.
    """
    if not _git_available() or not _is_git_repo():
        return None
    try:
        _git("add", scope if scope else "-A")
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        if scope:
            # pathspec на самом commit — гарантия, что в коммит попадёт ТОЛЬКО
            # поддерево этого пользователя, даже если что-то ещё оказалось в индексе.
            args += ["--", scope]
        _git(*args)
        result = _git("rev-parse", "HEAD")
        return result.stdout.strip() or None
    except subprocess.CalledProcessError as exc:
        log.warning("git commit failed (%s): %s", message, exc.stderr.strip())
        return None


def commit_all(message: str, allow_empty: bool = False) -> Optional[str]:
    """Зафиксировать данные ТЕКУЩЕГО пользователя. Публичный хук «закоммитить сейчас».

    Коммит затрагивает только ``users/<uid>/`` (см. ``_scope``); id пользователя
    указывается в сообщении (``psycho(<uid>): <message>``). Данные разных
    пользователей НИКОГДА не смешиваются в одном коммите.

    По умолчанию не плодит пустые коммиты (`allow_empty=False`). Используется как
    явная фиксация после каждого ответа (захватывает в т.ч. `_session.json`).
    """
    scope, label = _scope()
    return _git_commit(f"psycho({label}): {message}", scope=scope, allow_empty=allow_empty)


def _restore_scope(sha: str, scope: Optional[str]) -> bool:
    """Откат: при ``scope`` восстановить только поддерево пользователя из ``sha``,
    иначе — жёсткий reset всего вольта."""
    if not scope:
        return _git_reset_hard(sha)
    if not _git_available() or not _is_git_repo() or not sha:
        return False
    try:
        _git("checkout", sha, "--", scope)
        # подчистим untracked-файлы пользователя, появившиеся в провалившейся операции
        _git("clean", "-fd", scope, check=False)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git restore %s -- %s failed: %s", sha, scope, exc.stderr.strip())
        return False


def _git_reset_hard(sha: str) -> bool:
    if not _git_available() or not _is_git_repo() or not sha:
        return False
    try:
        _git("reset", "--hard", sha)
        # подчистим untracked-файлы, появившиеся в провалившейся операции
        _git("clean", "-fd", check=False)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git reset --hard %s failed: %s", sha, exc.stderr.strip())
        return False


# ---------- operation log ----------


def append_log(level: str, op: str, details: str = "") -> None:
    """Append-only лог операций в ``<vault>/.psycho/log.md``.

    Формат:  ``[YYYY-MM-DD HH:MM] <LEVEL> <op> — <details>``
    """
    try:
        PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
        if not LOG_PATH.exists():
            LOG_PATH.write_text("# Operation log\n\n", encoding="utf-8")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"[{ts}] {level.upper()} {op}"
        if details:
            line += f" — {details}"
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Лог не должен ломать основной flow.
        log.exception("append_log failed")


# ---------- git_wrap транзакция ----------


@contextmanager
def git_wrap(op_name: str) -> Iterator[None]:
    """Контекст-менеджер: pre-commit → блок записи → post-commit.

    На исключение внутри блока — ``git reset --hard <pre>``, лог-warn,
    исключение пробрасывается дальше.

    Если git недоступен — менеджер просто выполняет блок без обёртки и
    пишет одну warn-запись в log.md.
    """
    if not _git_available() or not _is_git_repo():
        append_log("warn", "git_unavailable", f"op={op_name} ran without safety net")
        yield
        return

    scope, label = _scope()
    # Снимок-точка для отката: фиксируем поддерево пользователя; если фиксировать
    # нечего — точка отката = текущий HEAD.
    pre_sha = _git_commit(f"psycho({label}): before {op_name}", scope=scope) or _git_head()
    try:
        yield
    except Exception as exc:
        append_log("error", op_name, f"failed: {exc!r} — attempting rollback")
        if pre_sha:
            ok = _restore_scope(pre_sha, scope)
            append_log("error", op_name, f"rollback {'ok' if ok else 'FAILED'} to {pre_sha[:8]}")
        raise
    else:
        _git_commit(f"psycho({label}): {op_name}", scope=scope)


# ---------- layout / init ----------


def ensure_layout() -> None:
    """Создать структуру для ТЕКУЩЕГО пользователя (userctx) + глобальный .psycho.

    Per-user: raw/profile/notes/_index. Глобально (корень вольта): .psycho/,
    git-репо.
    """
    raw_dir().mkdir(parents=True, exist_ok=True)
    profile_dir().mkdir(parents=True, exist_ok=True)
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        f = profile_dir() / f"{domain}.md"
        if not f.exists():
            f.write_text(f"# Портрет: {domain}\n\n", encoding="utf-8")
    idx = index_file()
    if not idx.exists():
        lines = ["# Psycho — индекс", "", "## Портрет по темам", ""]
        lines += [f"- [[profile/{d}|{d}]]" for d in DOMAINS]
        lines += ["", "## Сырые логи", "", "Папка `raw/` — Q&A по дням."]
        idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Operation log\n\n", encoding="utf-8")
    _ensure_user_graph_settings()
    ensure_git_repo()


# Дефолтные настройки графа Obsidian для папки пользователя. Шаблон лежит в
# репозитории (`bot/assets/graph.json`) и копируется в `users/<uid>/.obsidian/`
# нового пользователя — чтобы его папку можно было открыть отдельным вольтом с
# готовой раскраской по доменам, серыми тегами и фильтром (только concepts, без
# сирот). Существующий graph.json НЕ трогаем — у пользователя могут быть правки.
_GRAPH_TEMPLATE = Path(__file__).parent / "assets" / "graph.json"


def _ensure_user_graph_settings() -> None:
    gj = userctx.user_root() / ".obsidian" / "graph.json"
    if gj.exists() or not _GRAPH_TEMPLATE.exists():
        return
    try:
        gj.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(gj, _GRAPH_TEMPLATE.read_text(encoding="utf-8"))
    except OSError:
        log.warning("could not seed graph.json for %s", userctx.user_root())


# ---------- сквозная нумерация вопросов ----------


def _load_state() -> dict:
    sf = state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to load state, resetting")
            append_log("warn", "state_corrupted", "_state.json unreadable, resetting to 0")
    return {"last_q_num": 0}


def _save_state(state: dict) -> None:
    ensure_layout()
    atomic_write_json(state_file(), state)


def next_q_num() -> int:
    state = _load_state()
    state["last_q_num"] = int(state.get("last_q_num", 0)) + 1
    _save_state(state)
    return state["last_q_num"]


# ---------- запись ----------


def append_raw(q_num: int, when: datetime, domain: str, question: str, answer: str) -> Path:
    """Append Q&A в `raw/YYYY-MM-DD.md`. Санитизирует ввод от подделки
    заголовков ``## Q...`` и ``**Q:**``/``**A:**`` (см. ``validation.escape_raw_block``).

    Лимиты длины применяются к answer; если обрезано — пишем warn в log.md.
    """
    ensure_layout()
    if domain not in DOMAINS and domain != "user":
        # 'user' допускается как метка для /requestion, но в файл идёт как есть;
        # неожиданное значение всё равно записываем, чтобы не терять данные,
        # но отмечаем в логе.
        append_log("warn", "append_raw_unknown_domain", f"q={q_num} domain={domain!r}")
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = raw_dir() / f"{date_str}.md"

    q_clean = safe_question_text(question)
    a_clean, a_truncated = safe_user_text(answer)
    if a_truncated:
        append_log("warn", "raw_answer_truncated", f"Q{q_num} length>limit")
    q_clean = escape_raw_block(q_clean)
    a_clean = escape_raw_block(a_clean)

    # Block-id `^Q<n>` после **A:** — Obsidian-native якорь, на который концепты
    # ссылаются как [[raw/<date>#^Q<n>]] (kepano obsidian-skills, block refs).
    # Якорь должен быть на отдельной строке после контента, чтобы Obsidian
    # привязал его к предыдущему блоку, а не к следующему.
    block = (
        f"## Q{q_num} · {time_str} · {domain}\n"
        f"**Q:** {q_clean}\n"
        f"**A:** {a_clean}\n"
        f"^Q{q_num}\n\n"
    )
    if not path.exists():
        path.write_text(f"# {date_str}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def append_note(when: datetime, text: str) -> Path:
    """Append свободную заметку (/ucho) в `notes/YYYY-MM-DD.md` verbatim.

    Человеческий скрэтчпад — отдельно от raw (машинный Q&A-лог). Текст
    санитизируется так же, как ответ пользователя (control-байты, лимит),
    и экранируется против подделки raw-заголовков на случай чтения парсером.
    """
    nd = notes_dir()
    nd.mkdir(parents=True, exist_ok=True)
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = nd / f"{date_str}.md"
    clean, truncated = safe_user_text(text)
    if truncated:
        append_log("warn", "note_truncated", f"{date_str} {time_str} length>limit")
    clean = escape_raw_block(clean)
    block = f"## {time_str}\n{clean}\n\n"
    if not path.exists():
        path.write_text(f"# Заметки · {date_str}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def append_profile(when: datetime, domain: str, fragment: str, raw_time: str) -> Path:
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")
    ensure_layout()
    date_str = when.strftime("%Y-%m-%d")
    path = profile_dir() / f"{domain}.md"
    # fragment — короткая выжимка от LLM. Чистим переводы строк и спец-разметку,
    # чтобы не сместить структуру профиля и не подделать заголовки.
    fragment_clean = safe_question_text(fragment)
    fragment_clean = escape_raw_block(fragment_clean)
    block = (
        f"### {date_str}\n"
        f"- {fragment_clean} _(из [[raw/{date_str}|{raw_time}]])_\n\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


# ---------- чтение истории ----------


def iter_history() -> list[dict]:
    """Все записи Q&A по всем дням, отсортированные по Q-номеру по возрастанию.

    Каждая запись: {n, date, time, domain, question, answer}.
    """
    rd = raw_dir()
    if not rd.exists():
        return []
    entries: list[dict] = []
    for path in sorted(rd.glob("*.md")):
        date_str = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            log.exception("failed to read %s", path)
            continue
        for m in _ENTRY_RE.finditer(text):
            entries.append({
                "n": int(m.group(1)),
                "date": date_str,
                "time": m.group(2),
                "domain": m.group(3),
                "question": m.group(4).strip(),
                "answer": m.group(5).strip(),
            })
    entries.sort(key=lambda e: e["n"])
    return entries


def find_question(n: int) -> Optional[dict]:
    for e in iter_history():
        if e["n"] == n:
            return e
    return None
