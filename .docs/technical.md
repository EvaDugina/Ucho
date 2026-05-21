# Technical — Psycho

## brunelleschi_stage

- **Стадия:** POC B
- **Последнее обновление:** 2026-05-21

---

## technology

**Стек:**

- **Язык / фреймворк:** Python 3.12 + aiogram (Telegram), APScheduler (daily-тикер)
- **СУБД:** не используется (граф в Markdown-файлах внутри Obsidian-vault)
- **Очередь / брокер:** не используется
- **AI-провайдер:** локальный Ollama (через openai-совместимый API). Модель по умолчанию `qwen2.5:14b-instruct`, fallback `qwen2.5:7b-instruct` для CPU.
- **Прочее:** PyYAML (парсинг frontmatter), python-dotenv, git CLI (как safety net для записи в vault), YandexDisk-клиент на хосте (как механизм синка vault).

**Переменные окружения:**

| Переменная | Назначение | Пример |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather | `123:abc…` |
| `OWNER_TELEGRAM_ID` | владелец/админ (всегда разрешён) | `123456789` |
| `ALLOWED_TELEGRAM_IDS` | доп. доверенные id через запятую (начальный список; рантайм — в `.psycho/users.json`) | `111,222` |
| `VAULT_HOST_PATH` | абсолютный путь к Obsidian-vault на хосте; пробрасывается в контейнер как `/vault` | `C:/Users/eva/YandexDisk/Obsidian/Psycho` |
| `VAULT_PATH` | путь внутри контейнера (можно переопределить для тестов) | `/vault` |
| `OPENAI_BASE_URL` | URL Ollama | `http://ollama:11434/v1` |
| `OPENAI_API_KEY` | игнорируется Ollama, но требуется openai-sdk | `ollama` |
| `OPENAI_MODEL` | имя модели в Ollama | `qwen2.5:14b-instruct` |
| `LLM_TIMEOUT` | таймаут одного LLM-вызова, сек (без него sdk ждёт ~600 c) | `90` |
| `LLM_COOLDOWN_SEC` | мин. интервал между LLM-операциями одного пользователя (anti-DoS) | `4` |
| `DAILY_HOUR` | час суток для авто-вопроса | `20` |

**Поведение `DEBUG`:**

Сейчас флага `DEBUG` нет — single-user проект, prod-конфигурация и есть «как запускаем». Если будем выкладывать на shared сервер, добавим `DEBUG` для разделения hardening (см. notes → active plans).

---

## architecture

- **Подход:** монолит, асинхронный (aiogram + asyncio + openai async client). Один процесс бота + один процесс Ollama в соседнем контейнере.
- **Компоненты:**
  - Telegram-бот (`bot/`) — диалоговый слой, маршрутизация команд, форматирование сообщений.
  - Ollama — локальный LLM-сервер, GPU-проброс через NVIDIA Container Toolkit.
  - Obsidian-vault на хосте — хранилище графа и raw-логов, синхронизируется через YandexDisk-клиент.
- **Разделение труда (capture-first):** локальная Qwen (live) только захватывает — диалог, `raw/`, **черновые** концепты `status: draft` без связей/конфликтов. Выверенный граф строит сильная модель (Claude) раз в неделю через скилл `.claude/skills/weekly-review/` (proposal → apply под git): промоушн `draft → stable`, дедуп/слияние, связи, реальные противоречия, переписывание `profile/`, осмысленный MOC.
- **Multi-user изоляция:** бот обслуживает несколько доверенных пользователей; у каждого — своя база в `<vault>/users/<user_id>/` (concepts/raw/profile/notes/digests/_index/_state/_session). `.psycho/` (manifest, log, startup-check, users.json) и `.git/` — глобальные на корне (один safety net, ключи манифеста — относительные пути). Текущий пользователь — request-scoped через `userctx` (contextvar, async-безопасно): aiogram-middleware ставит его на каждый update; data-слой (vault/graph/moc) маршрутизирует пути по `userctx.user_root()`. Whitelist + роли — `bot/users.py` (`OWNER_TELEGRAM_ID` = админ, гости — env + `.psycho/users.json`). Гостю при первом обращении — disclaimer о приватности.
- **Потоки данных:**
  - Пользователь шлёт сообщение → handler → LLM (через Ollama) → парсинг JSON (`observations` — только анализ) → код пишет в vault: raw дословно, slug из имени, дедуп решает create/update → атомарная запись через `git_wrap` → MOC rebuild.
  - APScheduler раз в день → `send_daily_question` → handler в обход Telegram-входа.
  - При старте контейнера → `selfcheck.run()` (механический, без LLM): MOC rebuild всех доменов + валидация связей + `.psycho/startup-check.md`. Затем `session.restore()` + (при `pending_answer`) `process_pending_on_startup`. Офлайн-сообщения доезжают из очереди Telegram — polling не выставляет `drop_pending_updates`.
- **Внешние зависимости:** только Telegram Bot API. Никаких внешних AI-провайдеров.

**Модули и ответственность:**

- `bot/main.py` — точка входа, регистрация router + scheduler, восстановление сессии.
- `bot/config.py` — env-переменные, `DOMAINS`, пути `VAULT_PATH / MANIFEST_PATH / LOG_PATH`.
- `bot/handlers.py` — все Telegram-хэндлеры команд и текстов, оркестрация `_apply_processed` (raw дословно + домен сессии → profile → из `observations`: slug=`slugify(name)`, дедуп `resolve_slug`/Jaccard → draft/append evidence → MOC); возвращает `(created, updated)`. Связи/конфликты в live НЕ строит — это weekly-review.
- `bot/llm.py` — обёртка openai-клиента, режимы `ask` / `process` / `review` + `summarize_session`, фолбэк-логирование. Клиент с `timeout=LLM_TIMEOUT` + `max_retries=1` (не висим ~600 c при зависшей Ollama).
- `bot/ratelimit.py` — per-user ограничитель LLM-операций (anti-DoS на общий GPU): single-flight (1 активный вызов на пользователя) + cooldown. `try_acquire`/`release`, встроен во все пользовательские LLM-точки `handlers.py` (тикер/recovery — без лимита).
- `bot/graph.py` — `Concept` dataclass, `save_concept` (с drift check + slug sanitize + atomic write), `_render` (callouts), `_parse_file` (обе версии формата), `resolve_slug`, `find_similar_concept` (Jaccard).
- `bot/vault.py` — `ensure_layout` + `ensure_git_repo`, `git_wrap` транзакция, `append_log`, `next_q_num` + `_state.json`, `append_raw` с block-id, `append_profile`, `append_note` (свободные заметки `/ucho` в `notes/`), `iter_history`. `_ensure_user_graph_settings` сидит `users/<uid>/.obsidian/graph.json` из шаблона `bot/assets/graph.json` новому юзеру (идемпотентно — существующий не трогает).
- `bot/assets/graph.json` — шаблон настроек графа Obsidian для папки пользователя (фильтр только `concepts` + скрытие MOC через `-file:<DOMAIN>` + без сирот, `showTags`, цветовые группы по доменам, серые узлы-теги). Копируется в каждый новый `users/<uid>/.obsidian/`.
- `bot/session.py` — активная сессия, `_session.json` через atomic write, `from_dict` отбрасывает неизвестные поля.
- `bot/scheduler.py` — APScheduler с cron-триггером.
- `bot/atomic.py` — `atomic_write_text` / `atomic_write_json` (tmp + fsync + os.replace).
- `bot/manifest.py` — `record(path)` / `check_drift(path)` через `.psycho/manifest.json`.
- `bot/moc.py` — `rebuild_domain_moc(domain)` пересборка MOC-ноды `<DOMAIN>.md` (имя = тема заглавными → узел графа = категория) с группировкой по type; удаляет легаси `_moc.md`.
- `bot/selfcheck.py` — механический self-check при старте по всем пользователям (MOC rebuild + валидация связей + дубли/сироты → `.psycho/startup-check.md`), без LLM.
- `bot/userctx.py` — request-scoped текущий пользователь (contextvar) + `user_root()` для per-user маршрутизации путей.
- `bot/users.py` — whitelist-реестр (`OWNER` + env + `.psycho/users.json`), роли, consent.
- `bot/validation.py` — `safe_slug` / `slugify` (транслит ru→latin для вывода slug из имени концепта кодом) / `safe_user_text` / `escape_raw_block` / `is_valid_telegram_command_arg` и пр.
- `prompts/system.md` + `prompts/review.md` + `prompts/summarize.md` + `prompts/seeds.md` — промпты под режимы `ask`/`process`/`review`/`summarize`. Несут **персону «Иуда из Кариота»** (характер — в формулировках вопросов/комментариев; JSON-контракт строгий, механика не меняется).
- `scripts/migrate_domains.py` — одноразовый CLI-скрипт миграции 4→10 доменов.
- `scripts/migrate_to_multiuser.py` — одноразовый перенос корня владельца → `users/<owner>/` (dry-run + `--apply` под git_wrap, с post-верификацией). Выполнен; можно удалить.

**Данные и контракты:**

- **Концепт** (`concepts/<domain>/<slug>.md`): frontmatter `type/domain/slug/created/updated/status/supports/contradicts/derived_from/related/aliases` (+ `tags` у выверенных), тело — callouts `[!summary]/[!quote]/[!question]/[!contradiction]/[!source]`. `status`: `draft` (создан ботом live, ascii-slug, без связей) → `stable` (выверен Claude в weekly-review). Промежуточные `tentative`/`contested`. У `stable` `slug` = имя файла = **русский заголовок** (наглядные узлы графа), все ссылки ведут по русскому имени; старый ascii-slug — в `aliases` (якорь дедупа). `tags` (ставит weekly-review, бот их не трогает): доменный `<DOMAIN>` CAPS + сквозные русские темы из реестра `<base>/_tags.md` — второе измерение графа.
- **Raw-блок** (`raw/YYYY-MM-DD.md`): `## Q<N> · HH:MM · <domain>`, `**Q:** …`, `**A:** …`, `^Q<N>` block-id на отдельной строке.
- **Manifest** (`.psycho/manifest.json`): `{version, files: {<rel-path>: {mtime_ns, size}}}`.
- **State** (`_state.json`): `{last_q_num: int}`.
- **Session** (`_session.json`): `Session` dataclass сериализован, включая `mode`, `domain`, `last_question`, `history`, `pending_answer` (двухфазный коммит).
- **Контракт LLM `process`-режима (LLM только анализ — запись в БД делает код):**
  ```json
  {
    "type": "processed",
    "observations": [{"domain": "ethics", "type": "principle", "name": "Нарушение слова", "summary": "...", "quote": "дословный фрагмент ответа"}],
    "debate_message": "...",
    "question_type": "concrete|hypothetical|comparison|emotional_anchor|challenge",
    "close_session": false
  }
  ```
  LLM **не присылает** `slug`, `raw_entry`, `concepts_to_create/update`, связи — код их игнорирует. Идентичность/запись на коде: slug выводит `validation.slugify` из `name`, домен raw — из сессии, create-vs-update решает дедуп (`resolve_slug` по имени/slug + Jaccard), `quote` валидируется как дословная подстрока ответа. Граф (связи/конфликты) строит weekly-review.

---

## project_structure

```
Psycho/
├── bot/                    Telegram-бот + ядро
│   ├── main.py             точка входа
│   ├── config.py           env, домены, пути
│   ├── handlers.py         маршруты команд + _apply_processed
│   ├── llm.py              обёртка над Ollama (4 режима)
│   ├── graph.py            Concept dataclass + рендер/парсер + dedup/resolve
│   ├── vault.py            git_wrap, log, layout, raw, state
│   ├── session.py          активная сессия с persistence
│   ├── scheduler.py        APScheduler daily
│   ├── atomic.py           atomic_write_text/json
│   ├── manifest.py         mtime drift detection
│   ├── moc.py              per-domain MOC rebuild
│   ├── selfcheck.py        механический self-check при старте
│   └── validation.py       safe_slug/user_text/etc.
├── prompts/                промпты (system/review/summarize) + seeds
├── scripts/
│   └── migrate_domains.py  одноразовая миграция 4→10
├── docker-compose.yml      bot + ollama (GPU-проброс)
├── Dockerfile              python:3.12-slim + git
├── requirements.txt        зафиксированные версии
├── .env.example            пример конфига
└── README.md
```

В самом vault при первом запуске создаются: `.git/`, `.gitignore`, `.psycho/manifest.json`, `.psycho/log.md`, `concepts/<domain>/`, `raw/`, `profile/`, `notes/` (свободные заметки `/ucho`), `_index.md`, `_state.json`. При каждом старте — `.psycho/startup-check.md`.

---

## documentation

- `.docs/product.md` — продуктовое видение.
- `.docs/technical.md` — техническое устройство (этот файл).
- `.docs/demo.md` — короткий gist для нетехнического читателя.
- `README.md` — как запустить и задеплоить локально.

**Политика комментариев в коде:** комментируем намерения, ограничения и сложные решения (`# что и почему`), не очевидный код. Все новые модули этапов 1-3 имеют docstring с угрозами/инвариантами на уровне модуля.

---

## instructions

### Локальный запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

Один раз при первом запуске: `docker exec -it psycho-ollama ollama pull qwen2.5:14b-instruct` (~9 GB).

### Развёртывание

`deploy.sh` пока **нет**. Проект single-user на собственной машине, развёртывание = `docker compose up -d --build` после `git pull` + опциональный `docker exec psycho-ollama ollama pull <модель>` при смене модели.

Если когда-то выложим на shared сервер — нужен полноценный `deploy.sh` с idempotent-режимом (см. notes → active plans).

### Тестирование

- **Сценарии:** 30 автоматических проверок через `docker compose run` на изолированном vault (`VAULT_PATH=/tmp/psycho-test`). Покрывают этапы 1-3: atomic writes, drift detection, wikilink validation, slug sanitization, callout render/parser roundtrip, alias resolve, Jaccard dedup, migration `_apply_one`, MOC rebuild.
- **Команда запуска:** ad-hoc через `docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot python <<'PY' ... PY`. Скрипты тестов **не сложены в `tests/smoke/`** — это технический долг PoC B, см. notes → active plans.
- **Целевое покрытие:** не отслеживается (PoC B). Главное — happy path всех 10 доменов + drift-сценарий.

### Бэкапы

Не делаются в текущей стадии. Полагаемся на:
- Git внутри vault (`git_wrap` коммитит до/после каждой операции записи).
- YandexDisk-history (хранит версии файлов на серверах Яндекса).

Полноценные бэкапы (`pg_dump`-аналог, cron, ротация, проверка восстановления) — обязательство MVP A.

---

## quality

### Чеклисты

- **Автоматические:** 30 e2e-проверок (этапы 1+2+3 плана `vast-inventing-raccoon.md`). Не оформлены как `pytest`, гоняются вручную через docker.
- **Ручные:** 
  - `/ask <domain>` для каждого из 10 доменов → концепт создаётся.
  - Ручная правка `.md` в Obsidian → следующий `/ask` не теряет правку.
  - `/ucho <заметка>` → заметка в `notes/` + концепты в граф.
  - `/review` → бот отвечает по существующей базе.
  - Граф View в Obsidian → видны узлы и связи.

### Наблюдаемость и логирование

- **Куда пишем:**
  - Stderr контейнера (через стандартный `logging`) — `docker compose logs -f bot`.
  - `<vault>/.psycho/log.md` (append-only) — операционный журнал (drift skip, dedup, sanitize, llm-фолбэки).
  - Git внутри vault — каждый `_apply_processed` оставляет два коммита (`psycho(<uid>): before <op>` / `psycho(<uid>): <op>`). Коммит затрагивает только поддерево пользователя `users/<uid>/` — данные разных пользователей не смешиваются; `.psycho/` выведена из-под git (в `.gitignore`).
- **Формат:** в `log.md` — `[YYYY-MM-DD HH:MM] LEVEL op — details`. В stderr — стандартный python logging.
- **Ротация:** нет. На PoC B не критично — лог растёт медленно (десятки строк в неделю). С MVP A добавим ротацию по объёму.
- **Healthcheck:** `/pebble` команда в Telegram — мгновенный «буль.» (liveness самого бота, без LLM-вызова). Внешнего healthcheck-endpoint нет.

### Безопасность

- **Whitelist одного пользователя** (`OWNER_TELEGRAM_ID`) — все хэндлеры начинают с `_is_owner()`. Подсказки команд видны только владельцу через `BotCommandScopeChat(chat_id=OWNER_TELEGRAM_ID)`.
- **Секреты только в `.env`**: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`. `.env` в `.gitignore`.
- **Валидация ввода пользователя** (`bot/validation.py`):
  - `safe_user_text` — лимит 10 000 символов, control-байты выкинуты.
  - `escape_raw_block` — zero-width префикс ломает попытки подделать `## Q<n>` / `**Q:**` / `**A:**` в начале строки.
  - `is_valid_telegram_command_arg` — отбивает `/`, `\`, `|`, `;`, `&`, `$`, `` ` ``, control-символы в аргументах.
  - `safe_slug` — путь невалидного slug → пустота → отказ записи (защита от path traversal в концепт-файлах).
- **HTML-escape** всего динамического контента в `_format_q` + лимит 3500 символов (защита от 400 Bad Request от Telegram).
- **Whitelist callback `ask:<domain>`** — только `any` или конкретный домен из `DOMAINS`.
- **LLM возвращает только то что в whitelist:** домен/type/status/relation kind проверяются по closed-list, фолбэки логируются.
- **Никаких stacktrace пользователю** — все exceptions перехвачены, в чат уходит нейтральная фраза.

---

## rules

### Stage constraints

**На текущей стадии (POC B) делаем:**

- Зафиксированные зависимости (`requirements.txt` с `==`).
- Один `.env` + `.env.example` с описанием.
- Один способ запуска локально (`docker compose up -d`).
- `.gitignore` под Python + Docker + IDE-мусор + `.env`.
- Базовое логирование (stderr контейнера + `.psycho/log.md`).
- Whitelist (владелец + доверенные) + роли + валидация ввода.
- **Multi-user изоляция данных** (`users/<id>/`, userctx, per-user сессии) — взято «**сверх POC B**» (multi-user формально MVP A; берём только изоляцию + whitelist для пары доверенных, без полного MVP-A hardening).
- Atomic writes + drift detection + git_wrap транзакция в vault.
- Smoke-проверки этапов 1-3 (30 шт, гоняются вручную через docker).

**Intentionally deferred (что НЕ делаем до следующей стадии):**

- MVP-A hardening поверх multi-user: rate limiting на пользователя, структурные JSON-логи, бэкапы по cron с ротацией и тестом восстановления, мини-дашборд «кто сколько пользуется». Берём только если станет реальной альфой.
- `deploy.sh` для shared-сервера — разворачивается на собственной машине через docker compose.
- `tests/smoke/` как формальный набор pytest-сценариев — есть e2e-скрипты, но не оформлены (тех. долг).
- Reverse-proxy (Caddy/nginx) — бот ходит в Telegram наружу, входящих HTTP нет.
- Healthcheck-endpoint, Sentry, Grafana, мониторинг — MVP B.
- 152-ФЗ-режим, открытый доступ для произвольных пользователей — MVP B (сейчас только доверенные по whitelist).
- E2E через эмулятор Telegram, нагрузочное — MVP B.
- DEBUG-флаг для разделения dev/prod — пока нет prod-окружения.

---

## accept

PoC B техчасть считается принятой, когда:

- `docker compose up -d` запускает бота и Ollama без ручных шагов после `cp .env.example .env`.
- 30 e2e-проверок проходят (`docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot python <<...`).
- Бот работает неделю без падений на реальном vault владельца.
- `.psycho/log.md` создан, наполняется, читаемый глазами.
- `git log` внутри vault показывает регулярные пары `psycho(<uid>): before <op>` / `psycho(<uid>): <op>`, каждая ограничена поддеревом `users/<uid>/`.
- Ручная правка концепта в Obsidian → следующий ответ не перетёр правку (drift detection сработал).
- Open Graph View в Obsidian с фильтром `path:concepts/` показывает узлы 10 доменов + связи.

---

## notes

### Active plans

**Реализовано 2026-05-21 (LLM-только-анализ + персона Иуды + теги графа):**

- `process` переведён на `observations`: LLM отдаёт только анализ + вопрос; запись/идентичность — в коде (`_apply_processed_inner`): raw дословно + домен сессии, `slug` через `validation.slugify` (транслит), create-vs-update через дедуп, `quote` валидируется как подстрока ответа. Меньше нагрузка/ошибки JSON у Qwen.
- Промпты (`system/review/summarize/seeds`) — персона **«Иуда из Кариота»**; механика и JSON-контракт не тронуты.
- Граф (через `weekly-review`): русские имена `stable`-концептов + теги (доменный `<DOMAIN>` CAPS + сквозные русские из `_tags.md`) + цвет по домену + серые теги + скрытие MOC-файлов. Шаблон настроек графа — `bot/assets/graph.json`, сидится новому юзеру в `ensure_layout`.
- Образ пересобран, бот перезапущен; живой прогон против Qwen подтвердил новый контракт.

**Реализовано 2026-05-21 (multi-user изоляция, «сверх POC B»):**

- `users/<id>/` раскладка: владелец перенесён скриптом `migrate_to_multiuser.py`
  (выполнен, верификация PASS: 5 концептов, find_concepts ок). `.psycho/`+git глобальные.
- `bot/userctx.py` (contextvar) + aiogram-middleware `AccessMiddleware` (whitelist-гейт +
  set_user + consent). Пути `vault`/`graph`/`moc` — функции от `user_root()`.
- `bot/users.py` реестр + роли; админ-команды `/adduser`/`/removeuser`/`/users`.
- `session.py` → `dict[uid → Session]`; `restore_all()`; per-user `_state.json`/`_session.json`.
- `selfcheck`/scheduler/recovery — циклы по пользователям.
- `weekly-review` SKILL.md — работа в `users/<id>/`.
- План: `MULTIUSER_PLAN.md` (рабочий, сложить сюда и удалить после стабилизации).
- Deferred (см. stage constraints): MVP-A hardening (rate limiting, бэкапы, дашборд, структурные логи), embedding-дедуп.

**Реализовано 2026-05-21 (capture-first разделение труда):**

- Бот переведён в режим «только захват»: `process` создаёт черновые концепты `status: draft`, БЕЗ `relations`/`conflicts`. `_apply_processed_inner` больше не строит связи и контр-callouts; `concepts_to_update` только дописывает evidence (не патчит summary). `CONCEPT_STATUSES` += `draft`.
- `prompts/system.md` `mode: process` переписан под capture-first (без связей/конфликтов, draft, debate=вопрос).
- Скилл `weekly-review` апгрейжен до v2: теперь это **сборщик графа** (proposal → apply под git): промоушн `draft → stable`, дедуп/слияние, связи, реальные противоречия, переписывание `profile/`, осмысленный MOC, digest. Claude правит `concepts/`/`profile/`/MOC напрямую; `raw/` и служебное бота не трогает.
- Отложено до MVP A: локальная embedding-модель (`nomic-embed-text` в Ollama) для дедупа/поиска.

**Реализовано 2026-05-21 (русские узлы графа в weekly-review):**

- Выверенные (`stable`) концепты теперь имеют **русский `slug` = имя файла = заголовок**; все ссылки (`related`, тело, profile, MOC, digest) ведут по русскому имени (kepano-принцип «ссылка по имени, а не по id») → граф Obsidian полностью на русском, без ascii-ghost-нод. Старый ascii-slug уходит в `aliases` — якорь, по которому `resolve_slug` бота находит концепт и не плодит дубль.
- **Write-barrier:** `safe_slug` бота принимает только ASCII, поэтому `append_evidence`/`add_alias`/`save_concept` по русскому slug возвращают `None` — `stable`-концепты защищены от перезаписи ботом. (Прежнее обещание про drift-detection для этого не годится: manifest ключуется по пути, переименованный русский файл ему неизвестен → `check_drift` = `False`.) Следствие: новые цитаты к `stable` доносит weekly-review из `raw/`, не бот. Изменений в коде бота не потребовалось.
- Из praxis добавлены: Фаза 3 LINT (битые ссылки, инвариант русских имён, симметрия `contradicts`, draft-остатки, todo-в-stable) и закрытая схема callout-ов. Первая строка `[!summary]` — законченное предложение (её берёт MOC).

**Реализовано 2026-05-21 (ревизия команд + startup self-check):**

- Команды: убраны `/discuss`, `/answer`, `/end`; `/requestion`(старый)→`/echo`, `/retry`→`/requestion` («повторить вопрос»), `/ping`→`/pebble` («буль.»); добавлены `/ucho` (заметка в `notes/` + разбор в граф), `/help`. `/start` — «кнопка смыва»: закрывает активную сессию (данные целы), заменяет собой `/end`.
- `bot/selfcheck.py` — механический self-check при старте контейнера (MOC rebuild всех доменов + валидация связей + дубли/сироты → `.psycho/startup-check.md`). Вызывается из `main.py` до polling. Без LLM.
- `bot/vault.py::append_note` + `notes/<date>.md`.
- `find_concepts`/`all_slugs` теперь пропускают `_*.md` (баг: `_moc.md` парсился как концепт-сирота).
- Режим `discuss` удалён из `session.Mode` и `llm._system`; `prompts/discuss.md` удалён.
- Глубокий смысловой реиндекс вынесен в отдельный скилл `weekly-review` (`.claude/skills/weekly-review/`), запускается вручную из сильного агента (Claude), не из контейнера — Qwen 14B для реструктуризации слаба. Stage-решение, не drift.

**Реализовано (этапы 1-3 плана `vast-inventing-raccoon.md`, коммит `29c00c8`):**

- Этап 1 — Safety net: atomic writes, git_wrap, manifest+mtime drift, wikilink validation, slug sanitization, `.psycho/log.md`, валидация ввода (`bot/validation.py`), escape_raw_block против newline-injection в raw, html.escape с лимитами в Telegram-выводе.
- Этап 2 — Obsidian-native: aliases во frontmatter, block refs `^Q<N>` в raw, callouts (`[!summary]/[!quote]/[!question]/[!contradiction]/[!source]`), парсер обоих форматов (callouts + старый H2), `resolve_slug` (slug → aliases → name), `add_alias`, `add_contradiction_note`.
- Этап 3 — MOC + dedup + migration: `bot/moc.py` (`rebuild_domain_moc`, автoобновление в той же git_wrap транзакции), `graph.find_similar_concept` (Jaccard на биграммах с порогом 0.7 и guard на коротких summary), интеграция dedup в `_apply_processed` (resolve_slug + Jaccard → update + alias), `scripts/migrate_domains.py` (двухфазная миграция 4→10).

**Сделано сверх PoC B (для дискуссии — нужно ли откатывать или оставить):**

- Git как safety net в vault — это полу-обвязка из praxis-скилла, формально PoC B этого не требует. Решили оставить: даёт `/undo`-семантику бесплатно и хорошо ложится на single-user + YandexDisk-синк.
- Manifest с mtime и drift detection — тоже сверх PoC B. Оставили по той же причине: YandexDisk-pull без drift detection реально может затереть правку.

**Временно сломано / отложено:**

- `tests/smoke/` как формализованный pytest-набор — e2e-скрипты есть в истории команд, но не сложены в проект. Технический долг, поднять до MVP A.
- `deploy.sh` — не сделан, single-user через docker compose покрывает текущие потребности. Если когда-то shared сервер — приоритет.
- Двухфазный коммит ответа (`Session.pending_answer`) — поле есть в dataclass, но recovery-логика в handlers ещё не разведена полностью. Active.

### Manual verification scenarios (PoC B)

- **Drift на ручную правку:**
  - Предусловия: концепт `<vault>/concepts/ethics/chestnost.md` существует.
  - Шаги: открой в Obsidian, добавь строку, сохрани. В Telegram задай вопрос про честность, ответь. Бот пишет ответ.
  - Ожидаемый результат: твоя правка на месте, в `.psycho/log.md` появилась строка `drift_skipped` или операция прошла на другой файл; концепт не перезаписан целиком.

- **Черновик без связей (capture-first):**
  - Шаги: задай вопрос, ответь развёрнуто. Бот создаёт концепт.
  - Ожидаемый результат: новый файл `concepts/<domain>/<slug>.md` со `status: draft`, БЕЗ `supports/contradicts/...` (связи пустые), без `> [!contradiction]` callout. Связи появятся только после прогона `weekly-review` из Claude.

- **Dedup через Jaccard (live, лёгкий):**
  - Предусловия: концепт `chestnost` с summary вида «не лгать никому даже когда удобно».
  - Шаги: ответь так, чтобы LLM захотела создать концепт с близкой формулировкой.
  - Ожидаемый результат: новый файл НЕ создаётся; в `chestnost.md` второй evidence-callout + alias; в `.psycho/log.md` строка `dedup_jaccard` или `concept_alias_resolved`.

- **MOC автообновление:**
  - Предусловия: `<vault>/concepts/knowledge/` пустой или не существует.
  - Шаги: задай вопрос про знание, ответь, дай LLM создать концепт.
  - Ожидаемый результат: появился `concepts/knowledge/_moc.md` с разделом нужного type и пунктом — новым черновым концептом.

- **weekly-review строит граф (Claude):**
  - Предусловия: несколько `draft`-концептов за неделю.
  - Шаги: в Claude Code запусти скилл `weekly-review`, подтверди план (Фаза 2).
  - Ожидаемый результат: черновики стали `stable` (или слиты), появились связи и реальные `[!contradiction]`-callouts, `profile/<domain>.md` переписан в обзор, создан `digests/<неделя>.md`; `git log` vault содержит пару `weekly-review … before/applied`; `raw/` не тронут.

- **Restart-safety:**
  - Предусловия: активная сессия (Q открыт, ответа не было).
  - Шаги: `docker compose restart bot`.
  - Ожидаемый результат: после старта `/start` показывает «активная сессия Q<N>». `_session.json` валидный JSON, недоработанный ответ дожимается сам.

### Технические решения

- **2026-05-20:** atomic writes через `tmp + os.replace` (вместо ftell+fsync без replace) — единственная защита от полу-записанных файлов под YandexDisk-pull. Применяется ко всем критичным JSON и концептам.
- **2026-05-20:** git как safety net внутри vault, а не снаружи. Vault в YandexDisk-папке, `.git/` синкается тоже — это даёт `psycho-undo`-семантику между устройствами. Риск конфликтов git при работе с нескольких устройств принят (single-user, маловероятен).
- **2026-05-20:** парсер концептов поддерживает два формата (callouts + старый H2) — нужно для миграции и для того, чтобы ручные правки в Obsidian в любом из стилей не теряли данные.
- **2026-05-20:** dedup через Jaccard на биграммах токенов (порог 0.7), не BM25. Граф ≤ сотни узлов, простой алгоритм достаточен, BM25-индекс — оверкилл для PoC B.
- **2026-05-20:** валидация ввода — Defence-in-depth: на границе Telegram (`_accept_user_text`), на входе в vault (`escape_raw_block` в `append_raw`), на входе в граф (`safe_slug` в `save_concept`/`add_relation`/`append_evidence`), на выходе в Telegram (`html.escape` в `_format_q`).

### Технический долг

- E2E-сценарии не оформлены как `pytest` в `tests/smoke/` — гоняются ad-hoc через `docker compose run python <<...`.
- `deploy.sh` отсутствует — для single-user покрыто `docker compose up -d`, но формально PoC B требует.
- Двухфазный коммит ответа (`Session.pending_answer`) — поле введено, recovery-логика не доведена в handlers.
- Промпты LLM (`prompts/system.md`) пока описывают старый формат концепта (H2-секции). LLM по-прежнему отдаёт data в виде JSON, который бот рендерит в callouts — но в идеале и сам промпт обновить под новый формат, чтобы LLM лучше «понимала» как читать существующие файлы.
- Бэкап-стратегия: сейчас полагаемся на git внутри vault + YandexDisk-history. До MVP A нужна явная стратегия (тест восстановления).

### Журнал изменений

- **2026-05-20:** документ создан после завершения этапов 1-3 плана `vast-inventing-raccoon.md` (safety net + Obsidian-native + dedup/MOC).

---

## ai_pipeline

- **Разделение труда (две модели, разные роли):**
  - **Qwen 14B локально (live, в контейнере)** — захват: режимы `ask`, `process` (capture-first: только черновики + evidence, без связей/конфликтов), `review`, `summarize`. GPU `qwen2.5:14b-instruct` / CPU-fallback `qwen2.5:7b-instruct`. Режим `discuss` убран 2026-05-21.
  - **Сильная модель (Claude в Claude Code, вручную раз в неделю)** — сборка графа: скилл `weekly-review` (промоушн draft→stable, дедуп/слияние, связи, реальные противоречия, profile, MOC). Не в контейнере, запускается пользователем.
  - Классификация при миграции 4→10 (`scripts/migrate_domains.py`) → Qwen, temperature=0.
  - Embeddings → не используются (live-дедуп через slug+alias+Jaccard). Векторный дедуп/поиск (`nomic-embed-text` в Ollama) — кандидат на MVP A.
- **Провайдер:** локальный Ollama, openai-совместимый API через `OPENAI_BASE_URL=http://ollama:11434/v1`.
- **LLM-бенчмарки:** не делаются на PoC B. До MVP B добавим минимальный набор «правильно ли парсится JSON ответа» / «правильно ли выбран домен» / «находит ли реальные противоречия».

---

## telegram_bot

- **Токен:** в `.env` (`TELEGRAM_BOT_TOKEN`). Никогда в коде, в `.gitignore` весь `.env`.
- **Whitelist админских команд:** один user_id из env (`OWNER_TELEGRAM_ID`). Все хэндлеры начинают с `_is_owner(message)`. Команд-меню (через `BotCommandScopeChat`) видно только владельцу.
- **Валидация входящих:** см. `## quality → ### Безопасность` выше — `safe_user_text` (10k char + control-байты), `escape_raw_block` (newline-injection), `is_valid_telegram_command_arg` (path/shell-символы), `safe_slug` (path traversal в файловые имена).
- **Обработка ошибок:** все exceptions перехвачены — в чат уходит нейтральная фраза («Не получилось разобрать ответ. Сформулируй ещё раз или /end.»). Stacktrace пишется в stderr контейнера и `.psycho/log.md`.
