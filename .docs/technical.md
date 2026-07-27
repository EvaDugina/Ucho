# Technical — Ухо

## brunelleschi_stage

- Стадия: POC B
- Последнее обновление: 2026-07-27

## technology

- Python 3.12, aiogram 3.13, APScheduler 3.10.
- OpenAI-compatible LLM: OpenRouter при непустом `OPENROUTER_API_KEY`, иначе
  AITunnel. Primary/fallback задаются env.
- Файловый vault вместо БД; Git CLI как scoped safety-net и push.
- Docker Compose — единственный runtime и тестовый контур.
- Pydantic валидирует LLM JSON для personality.

Главные env: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
`ALLOWED_TELEGRAM_IDS`, `VAULT_HOST_PATH`, `VAULT_PATH`, provider keys/models,
`DAILY_HOUR`, `DAILY_TZ`, reminder window, `DEBUG`, `VAULT_GIT_ENABLED`,
`BACKGROUND_JOBS_ENABLED`, `STARTUP_RECOVERY_ENABLED`, логирование и proxy.
Полный список с комментариями — в `.env.example`.

`DEBUG=true` по умолчанию выключает Git-vault и фоновые startup jobs, но оставляет
ручной Telegram-диалог и файловую запись. Явные `*_ENABLED` переопределяют это.

## architecture

Монолитный async-бот с request-scoped user context.

Поток обычного текста:

1. Middleware проверяет whitelist и устанавливает `userctx`.
2. Handler разрешает explicit reply, затем pending книжной цитаты, затем активную
   сессию или свободную заметку.
3. `conversation_service` обязательно дописывает typed user event в
   `00_raw/sessions/<session>.jsonl` и фиксирует raw.
4. `classify_mood` обновляет `01_mood/current.md` и месячный event-log.
5. `process_answer` возвращает `reaction`, `personality_delta`,
   `mask_frequency_draft`.
6. `answer_service` принимает только дельты с дословной quote, выдаёт ID/raw event
   ID/status pending.
7. Реакция отправляется через `session_messages`, которое сохраняет assistant event.

`_session.json` содержит активный runtime и pending refs. `_state.json` содержит
номер вопроса, daily/reminder plan, upload wait, книжные toggles/scores/pending.
Полная переписка не дублируется в runtime-файлах.

`about_service` делает два последовательных вызова: нейтральный synthesis, затем
persona presentation. Сначала атомарно записываются version/current, после чего
захваченные pending-дельты получают status `synthesized`.

`books.py` разбирает TXT/MD/FB2/EPUB в памяти. Оригинал и metadata сохраняются под
`books/<sha-prefix>`. EPUB ZIP не извлекается. Общие записи идут через
`books_git_wrap`; персональные — через `git_wrap`.

Ключевые модули:

- `handlers.py` — разрешённые команды, callback и precedence входящего текста.
- `session.py`/`session_log.py` — recovery state и единственный raw source.
- `moods.py`/`mood_file.py` — нормализация mood и face preferences.
- `about.py` — хранилище дельт и версий профиля.
- `books.py` — библиотека и персональное книжное состояние.
- `scheduler.py`/`daily_service.py`/`reminder_service.py` — фоновые сообщения.
- `recovery.py` — pending, queued и offline backlog.

## project_structure

```text
bot/        runtime
prompts/    persona и LLM-контракты
scripts/    ручная миграция vault
tests/      pytest и smoke
deploy/     server scripts
.docs/      продукт, требования, техника, демо
```

Vault:

```text
users/<uid>/
  00_raw/sessions/*.jsonl
  01_mood/current.md
  01_mood/events/YYYY-MM.jsonl
  01_personality/deltas.json
  01_personality/about/current.md
  01_personality/about/versions/*.md
  01_personality/face/*
  _session.json
  _state.json
books/<book-id>/{source.*,text.txt,metadata.json}
.psycho/{users.json,log.md}
```

## documentation

- `.docs/product.md` — цель и контекст.
- `.docs/functionality.md` — канонические требования и acceptance.
- `.docs/technical.md` — текущая реализация.
- `.docs/demo.md` — короткий сценарий для нетехнического читателя.
- `README.md` — запуск, операции и миграция.

Комментарии в коде объясняют инварианты и угрозы, а не очевидный синтаксис.

## instructions

### Локальный запуск

```powershell
Copy-Item .env.example .env
docker compose up -d --build bot
docker compose logs -f bot
```

### Развёртывание

`deploy/deploy.sh` подготавливает Docker-хост, синхронизирует app/vault, создаёт
env из закрытого файла, запускает проверки и поднимает compose. Подробности —
`deploy/README.md`.

### Тестирование

```powershell
docker compose build bot
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot pytest
docker compose run --rm bot ruff check bot scripts tests
docker run --rm -v "${PWD}:/repo" zricethezav/gitleaks:latest detect --source=/repo
```

### Миграция старого vault

```powershell
docker compose run --rm bot python scripts/migrate_simplified_storage.py
docker compose run --rm bot python scripts/migrate_simplified_storage.py --apply
```

Первый вызов только preview. Автоматически при старте миграция не запускается.

### Бэкапы

Для POC B отдельная backup-система отложена. Git-репозиторий vault даёт
операционный safety-net, но не считается полноценным offsite backup.

## quality

### Чеклисты

- Pytest покрывает команды/layout, raw-before-LLM/recovery, mood/personality,
  книги/reminders, `/leta` и миграцию.
- `tests/smoke/test_main_paths.py` проверяет чистый vault.
- Перед deploy обязательны полный pytest, smoke, Ruff и Gitleaks.

### Наблюдаемость и логирование

Python logging пишет stderr/docker logs и ротируемый `.logs/bot.log`.
`.psycho/log.md` хранит нейтральные операции vault без stacktrace для пользователя.

### Безопасность

- Whitelist применяется к message и callback.
- Секреты только в `.env`.
- XML DTD/entity запрещены; ZIP paths/объём/число членов проверяются.
- Telegram/LLM dynamic output экранируется перед HTML.
- Git scope предотвращает захват чужих user dirs и библиотеки.
- `/leta` проверяет точный resolved path `users/<uid>`.

## rules

### Stage constraints

На текущей стадии делаем:

- Docker deploy, pinned dependencies, structured logs.
- Recovery, scoped Git, migration preview/apply.
- Программные тесты критического пути и smoke.

Intentionally deferred:

- MFA, публичная регистрация, полноценный аудит действий.
- Метрики/дашборды/алерты и нагрузочные тесты.
- Автоматические offsite backups и restore drill.
- Web UI, PostgreSQL, полнотекстовый поиск книг.

## accept

Техническая готовность определяется `.docs/functionality.md` → `## accept` и
финальными Docker-командами из `## instructions`.

## notes

### Active plans

- Реализовано: упрощённое storage/analysis ядро, общая библиотека, migration.
- Отложено: управление жизненным циклом общих книг.

### Manual verification scenarios

1. Новый пользователь: `/ask` → ответ → `/about`; проверить raw/mood/deltas/version.
2. `/upload` EPUB → `/sea` → выбрать книгу → ответить на вопрос.
3. Дождаться книжной цитаты → ответить обычным текстом → убедиться, что score вырос
   один раз и pending очищен.
4. Preview миграции старого fixture-vault → apply → повторный apply.

### Технические решения

- 2026-07-27: `00_raw/sessions` назначен единственным источником истины.
- 2026-07-27: mood и personality обслуживаются только runtime-кодом.
- 2026-07-27: библиотека глобальна, предпочтения остаются per-user.
- 2026-07-27: EPUB разбирается только in-memory stdlib-парсером.

### Технический долг

- POC B не блокирует конкурентные uploads разных пользователей общим async lock;
  файловая Git-транзакция защищает целостность, но не даёт распределённой блокировки.

### Журнал изменений

- 2026-07-27: документация полностью приведена к упрощённой архитектуре.

## ai_pipeline

- `ask`: один вопрос по теме или затравке.
- `ask_book_question`: вопрос только по переданному фрагменту.
- `classify_mood`: categorical sign/energy/direction/quality/dominance.
- `process`: reaction + personality deltas + mask draft.
- `synthesize_about`: нейтральный внутренний профиль.
- `about_present`: озвучивание профиля персоной.

Primary и fallback выбираются per task из env; специальных внешних скиллов нет.

## telegram_bot

Команды публикуются per-chat scope только доверенным. Владелец дополнительно видит
admin-команды. Polling не сбрасывает pending updates; startup сначала дожимает
pending/queued/offline сообщения, затем выполняет daily catch-up.
