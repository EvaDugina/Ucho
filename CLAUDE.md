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
(см. глобальный `~/.claude/CLAUDE.md`). Live-LLM работает через OpenRouter:

```powershell
docker compose up -d                 # поднять bot
docker compose logs -f bot           # логи бота
docker compose up -d --build bot     # пересобрать только bot после правок кода
```

Конфиг — `.env` (из `.env.example`). `VAULT_HOST_PATH` обязателен: путь к vault на
хосте, пробрасывается в контейнер как `/vault`; `OPENAI_API_KEY` — ключ OpenRouter.
`.env` под `.gitignore` и **закрыт для чтения** — не пытайся его прочитать.

## Тесты

Pytest-набор гоняется внутри Docker на изолированном vault:

```powershell
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot pytest
```

Ad-hoc e2e-сценарии тоже запускай только через `docker compose run --rm ... bot`.

После правок кода всегда пересобирай образ (`--build`) — `bot/`, `prompts/`, `scripts/`
и `tests/` копируются внутрь образа на build-стадии, а не монтируются.

## Архитектура (большая картина)

**Capture-first, две модели с разными ролями** — ключевой принцип:
- **OpenRouter live-модель** только *захватывает*: режимы `ask` / `process` /
  `classify_mood` / `about_present`. Primary:
  `qwen/qwen3-235b-a22b-2507`, fallback:
  `deepseek/deepseek-v4-flash`. В `process` создаёт лишь черновые
  концепты (`status: draft`) с evidence — **без связей и конфликтов**.
- **Claude (вручную, НЕ в контейнере)** *собирает выверенные документы* двумя скиллами:
  `.claude/skills/reconcista/` — граф знаний (промоушн draft→stable, дедуп/слияние, связи,
  реальные противоречия, `02_profile/`, MOC, теги, digest); `.claude/skills/depersonalization/`
  — портрет носителя (`03_personality/about.md`), анализ настроения (`03_personality/mood.md`),
  психометрика (`03_personality/profile.md`), soft skills (`03_personality/softskills.md`),
  граф `01_mood/`, `03_personality/user_prompt.md`. Live-модель для этого не используется.

**Хранилище — файлы, не БД.** Граф живёт в Obsidian-vault как Markdown:
`00_raw/sessions/` (полный event-log сессий, источник истины переписки),
`00_raw/qna/` (человекочитаемая Q&A-проекция), `02_concepts/<domain>/<slug>.md`,
`02_profile/`, `02_digest/`, `03_personality/`, `01_mood/`.
Служебное per-user: `_state.json` (счётчик Q/daily marker), `_session.json`
(только активное runtime-состояние и pending refs, без полной истории).

**Multi-user изоляция через contextvar.** У каждого доверенного — своя база в
`<vault>/users/<uid>/`. Текущий пользователь хранится в `bot/userctx.py` (request-scoped
contextvar, async-безопасно); `AccessMiddleware` ставит его на каждый update. Весь
data-слой (`vault`/`graph`/`moc`/`session`) маршрутизирует пути через
`userctx.user_root()` — uid не прокидывается через сигнатуры. `.psycho/` (manifest,
log, users.json) и `.git/` — **глобальные** на корне, НЕ per-user.

**Поток обработки ответа:** сообщение → `00_raw/sessions` (до LLM) → `01_mood`
→ `llm.process_answer` (возвращает ТОЛЬКО анализ) → `_apply_processed` пишет
`00_raw/qna`, `02_concepts`, `02_profile`, `03_personality/deltas` кодом:
raw дословно, slug через `validation.slugify`, create-vs-update через дедуп
(`resolve_slug` + Jaccard) → всё под `vault.git_wrap` транзакцией → MOC rebuild.

**Дневной вопрос:** `bot/scheduler.py` (APScheduler, cron) → `send_daily_question`
заходит в обход Telegram-входа, по каждому пользователю в цикле.

**Restart-safety:** на старте `selfcheck.run()` (механический, без LLM) +
`session.restore_all()` + recovery незавершённых ответов (`pending_answer_event_id`
в `00_raw/sessions`, двухфазный коммит). Polling не выставляет
`drop_pending_updates` — офлайн-сообщения доезжают.

## Критичные инварианты (легко сломать)

- **LLM в `process` отдаёт только `observations`** (анализ + следующий вопрос). Запись
  в граф, идентичность концептов, slug, create/update — целиком на коде. Не возвращай
  LLM к генерации slug/raw_entry/связей — контракт в `prompts/process.md` строгий.
- **`_send_question()` в `handlers.py` — единственная точка отправки любого вопроса**
  (главный, кларифер, `/echo`, `/requestion`, recovery). Только она пишет bot-событие
  в `00_raw/sessions`; `qmap/questions/sessions` — восстановимые обёртки.
  Отправляешь вопрос мимо неё — reply/`/answer N` на него не разрезолвятся.
- **Не меняй `slug` во frontmatter существующих концептов** — сломаешь wikilink-связи.
- **`stable`-концепты (выверены reconcista) имеют русский `slug`=имя файла=заголовок.**
  `safe_slug` бота принимает только ASCII — это намеренный write-barrier: бот физически
  не может перезаписать `stable`. Новые цитаты к ним доносит reconcista из `00_raw/`.
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
