# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Telegram-бот «Ухо» (персона «Иуда из Кариота»), который ведёт граф психо-философского
портрета пользователя в Obsidian-vault. На каждый ответ извлекает концепты-черновики,
пишет сырые Q&A и (раз в неделю, отдельным проходом) собирает выверенный граф связей.
Стадия проекта — **POC B**. Рабочий язык кода, документации и промптов — русский.

Авторитетная глубокая документация — [.docs/technical.md](.docs/technical.md)
(устройство, контракты данных, безопасность, stage-ограничения). README местами
отстаёт от кода по списку команд — источник правды по командам это
`bot/main.py::BOT_COMMANDS` и хэндлеры в `bot/handlers.py`.

## Запуск и разработка

Всё исполняется в Docker — локальный запуск скриптов/тестов вне контейнера запрещён
(см. глобальный `~/.claude/CLAUDE.md`). Бот + Ollama поднимаются вместе:

```powershell
docker compose up -d                 # поднять bot + ollama
docker compose logs -f bot           # логи бота
docker compose up -d --build bot     # пересобрать только bot после правок кода
docker exec -it psycho-ollama ollama pull qwen2.5:14b-instruct   # один раз, ~9 GB
```

Конфиг — `.env` (из `.env.example`). `VAULT_HOST_PATH` обязателен: путь к vault на
хосте, пробрасывается в контейнер как `/vault`. `.env` под `.gitignore` и **закрыт
для чтения** — не пытайся его прочитать.

## Тесты

Формального `pytest`-набора нет (тех. долг PoC B). ~30 e2e-проверок гоняются ad-hoc
на изолированном vault:

```powershell
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot python <<'PY'
# ... сценарий ...
PY
```

После правок кода всегда пересобирай образ (`--build`) — `bot/`, `prompts/`, `scripts/`
копируются внутрь образа на build-стадии, а не монтируются.

## Архитектура (большая картина)

**Capture-first, две модели с разными ролями** — ключевой принцип:
- **Qwen 14B локально (live, в контейнере)** только *захватывает*: режимы `ask` /
  `process` + `about_present` (портрет). В `process` создаёт лишь черновые концепты
  (`status: draft`) с evidence — **без связей и конфликтов**.
- **Claude (вручную раз в неделю, НЕ в контейнере)** *собирает граф*: скилл
  `.claude/skills/weekly-review/` делает промоушн draft→stable, дедуп/слияние, связи,
  реальные противоречия, переписывает `profile/` и MOC. Qwen 14B для этого слаба.

**Хранилище — файлы, не БД.** Граф живёт в Obsidian-vault как Markdown:
`raw/` (сырые Q&A), `concepts/<domain>/<slug>.md` (узлы графа), `profile/`, `notes/`.
Служебное per-user: `_state.json` (счётчик Q), `_session.json` (активная сессия),
`_qmap.json` (карта `message_id→вопрос`).

**Multi-user изоляция через contextvar.** У каждого доверенного — своя база в
`<vault>/users/<uid>/`. Текущий пользователь хранится в `bot/userctx.py` (request-scoped
contextvar, async-безопасно); `AccessMiddleware` ставит его на каждый update. Весь
data-слой (`vault`/`graph`/`moc`/`session`) маршрутизирует пути через
`userctx.user_root()` — uid не прокидывается через сигнатуры. `.psycho/` (manifest,
log, users.json) и `.git/` — **глобальные** на корне, НЕ per-user.

**Поток обработки ответа:** сообщение → handler → `llm.process_answer` (возвращает
ТОЛЬКО `observations` — анализ) → `_apply_processed` в `bot/handlers.py` пишет в vault
кодом: raw дословно, slug через `validation.slugify`, create-vs-update через дедуп
(`resolve_slug` + Jaccard) → всё под `vault.git_wrap` транзакцией → MOC rebuild.

**Дневной вопрос:** `bot/scheduler.py` (APScheduler, cron) → `send_daily_question`
заходит в обход Telegram-входа, по каждому пользователю в цикле.

**Restart-safety:** на старте `selfcheck.run()` (механический, без LLM) +
`session.restore_all()` + recovery незавершённых ответов (`Session.pending_answer`,
двухфазный коммит). Polling не выставляет `drop_pending_updates` — офлайн-сообщения
доезжают.

## Критичные инварианты (легко сломать)

- **LLM в `process` отдаёт только `observations`** (анализ + следующий вопрос). Запись
  в граф, идентичность концептов, slug, create/update — целиком на коде. Не возвращай
  LLM к генерации slug/raw_entry/связей — контракт в `prompts/process.md` строгий.
- **`_send_question()` в `handlers.py` — единственная точка отправки любого вопроса**
  (главный, кларифер, `/echo`, `/requestion`, recovery). Только она пишет в `qmap`.
  Отправляешь вопрос мимо неё — reply/`/answer N` на него не разрезолвятся.
- **Не меняй `slug` во frontmatter существующих концептов** — сломаешь wikilink-связи.
- **`stable`-концепты (выверены weekly-review) имеют русский `slug`=имя файла=заголовок.**
  `safe_slug` бота принимает только ASCII — это намеренный write-barrier: бот физически
  не может перезаписать `stable`. Новые цитаты к ним доносит weekly-review из `raw/`.
- **Один git-коммит = данные одного пользователя** (поддерево `users/<uid>/`). `.psycho/`
  выведена из-под git. Пары коммитов `psycho(<uid>): before <op>` / `psycho(<uid>): <op>`.
- **Бот никогда не удаляет файлы** в vault — чистка только руками в Obsidian.
- **Все exceptions перехвачены** — пользователю уходит нейтральная фраза, stacktrace
  в stderr + `.psycho/log.md`. Не выпускай trace в чат.

## Соглашения

- Комментарии — про намерение и ограничения (`# что и почему`), не про очевидный код.
  Новые модули имеют module-docstring с угрозами/инвариантами.
- При смене стадии или значимых решениях актуализируй `.docs/*` (формат Vibe++,
  ведётся скиллом `brunelleschi-plus`) — это часть контракта проекта.
- Промпты (`prompts/`) несут персону «Иуда из Кариота» в формулировках; JSON-контракт
  и механику персона не меняет.
