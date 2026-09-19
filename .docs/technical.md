# Technical — Ухо

## brunelleschi_stage

- Стадия: POC B
- Последнее обновление: 2026-09-19

## technology

- Python 3.12, aiogram 3.13, APScheduler 3.10, markdown-it-py 4.2.
- OpenAI-compatible LLM: OpenRouter при непустом `OPENROUTER_API_KEY`, иначе
  AITunnel. Primary/fallback задаются env.
- Файловый vault вместо БД; Git для данных не используется.
- Docker Compose — единственный runtime и тестовый контур.
- Контейнер работает с `TZ=Europe/Moscow`: локальные `datetime.now()` и имена
  версий `/about` получают московское время. `DAILY_TZ` отдельно задаёт зону
  расписания. Метка в имени raw-сессии остаётся в UTC, как описано ниже.
- Pydantic валидирует LLM JSON для personality.

Главные env: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
`ALLOWED_TELEGRAM_IDS`, `VAULT_HOST_PATH`, `VAULT_PATH`, provider keys/models,
`DAILY_HOUR`, `DAILY_TZ`, reminder window, `DEBUG`, `BACKUP_HOST_PATH`,
`BACKUP_ENABLED`, `BACKUP_WEEKDAY`, `BACKUP_HOUR`, `BACKUP_KEEP`,
`BACKGROUND_JOBS_ENABLED`, `STARTUP_RECOVERY_ENABLED`, логирование и proxy.
Полный список с комментариями — в `.env.example`.

`DEBUG=true` по умолчанию выключает backup и фоновые startup jobs, но оставляет
ручной Telegram-диалог и файловую запись. Явные `*_ENABLED` переопределяют это.

## architecture

Монолитный async-бот с request-scoped user context.

Поток обычного текста:

1. Middleware проверяет whitelist и устанавливает `userctx`.
2. Handler разрешает explicit reply, затем pending книжной цитаты, затем активную
   сессию или свободную заметку.
3. `conversation_service` обязательно дописывает typed user event в
   `00_raw/sessions/<timestamp>_<uuid>.jsonl`, flush/fsync завершает запись до LLM.
   Из JSONL обновляется читаемая Markdown-страница для Obsidian.
4. `classify_mood` обновляет `01_mood/current.md` и идемпотентный месячный
   event-log до запуска `process_answer`.
5. `process_answer` возвращает `reaction` и `personality_delta`.
6. `answer_service` принимает только дельты с дословной quote, выдаёт ID/raw event
   ID/status pending.
7. Реакция отправляется через `session_messages`, которое сохраняет assistant event.

`prompts/process.md` требует прямого ответа от первого лица и запрещает
метаописание пользовательской фразы как «реплики» или «образа». Неоднозначный
короткий текст вызывает осторожный личный отклик без навязанного смысла.
`prompts/about.md` задаёт ту же грамматическую позицию для ответа `/about`;
внутренний синтез профиля и дельты сохраняют третье лицо.

`/ucho <текст>` создаёт новый `session_id` до raw-записи. Первая запись имеет
тип `note` и не получает `q_num` прежнего вопроса. Реакция становится опорой
для следующих сообщений этой сессии.

Имя файла получает UTC-время первого raw-события (naive timestamp остаётся как
записан) и UUID. Новые сессии используют свой UUID; для исторических строковых
`session_id` файловый UUID вычисляется стабильно через UUIDv5. Внутренние
`session_id` и `event_id` при переименовании не меняются. Ручной preview/apply —
`scripts/rename_session_files.py`; запуск при старте не требуется.

`_session.json` содержит активный runtime и pending refs. `_state.json` содержит
номер вопроса, daily/reminder plan, upload wait, книжные toggles/scores/pending.
Полная переписка не дублируется в runtime-файлах.

`about_service` делает два последовательных вызова: нейтральный synthesis, затем
нейтральное представление пользователю. Сначала атомарно записываются
version/current, после чего захваченные pending-дельты получают status
`synthesized`. Handler сначала отправляет пересказ, затем сохранённый полный
профиль через `about_messages`: YAML-метаданные становятся блоком кода,
Markdown-заголовки — жирными, каждый HTML-фрагмент помещается в лимит Telegram.
Synthesis возвращает JSON с текстом и четырьмя обязательными характеристиками;
схема проверяет типы и диапазоны, неизвестные характеристики допускают `null`.
Приложение собирает YAML-блок с четырьмя русскими названиями. Дата и счётчик
сообщений удаляются и из старого формата; `null` и low/medium/high
показываются по-русски. Старые английские названия переводятся без нового анализа.
Telegram timestamp перед сохранением имени версии переводится
в `DAILY_TZ`. Внешние Markdown-ограждения снимаются при чтении, записи и показе.
Профиль без четырёх обязательных полей однократно дополняется при `/about`:
модель определяет метаданные, текст разделов остаётся исходным, сохраняется новая
версия. Ошибка ответа модели оставляет текущий профиль и дельты без изменений.

При синтезе LLM получает два сообщения: system с правилами профиля и user
с полным текущим профилем и JSON-массивом всех pending-дельт. В дельтах передаются
аспект, вывод, дословная цитата, уверенность и служебные идентификаторы/время/статус.
Raw-история, вопросы бота, журнал настроения и книги отдельно в этот запрос не входят.
Для пересказа второй вызов получает system из `prompts/about.md` и user с полным
сохранённым профилем. Без новых дельт и при актуальном формате вызывается только
пересказ; новая версия профиля не создаётся.

`books.py` без LLM разбирает EPUB/FB2/Markdown/TXT и строит версионированный
`structure.json`. EPUB использует nav/NCX, при их отсутствии — linear spine;
FB2 — верхние section основного body; Markdown — CommonMark headings; TXT —
абзацы в одном разделе с названием файла. Цитата
120–1200 символов никогда не пересекает верхнюю главу. Только после выбора
`BookExcerpt` диалоговый слой вызывает `ask_book_question`.

Оригинал, нормализованный текст, metadata и структура сохраняются под стабильным
`books/<book-id>`. EPUB ZIP не извлекается. Новая книга записывается во
временный каталог и становится видимой только после завершения всех файлов.

Ключевые модули:

- `handlers.py` — разрешённые команды, callback и precedence входящего текста.
- `session.py`/`session_log.py` — recovery state и единственный raw source.
- `moods.py`/`mood_file.py` — нормализация mood, current и месячные события.
- `about.py` — хранилище дельт и версий профиля.
- `books.py` — библиотека и персональное книжное состояние.
- `scheduler.py`/`daily_service.py`/`reminder_service.py` — фоновые сообщения.
- `recovery.py` — pending, queued и offline backlog.
- `backup.py` — снимки каталога, проверка хешей и ротация.
- `scripts/repair_books.py` — ручной осмотр и восстановление книжного индекса.

## project_structure

```text
bot/        runtime
prompts/    нейтральные LLM-контракты
tests/      pytest и smoke
deploy/     server scripts
.docs/      продукт, требования, техника, демо
```

Vault:

```text
users/<uid>/
  00_raw/sessions/*.{jsonl,md}
  01_mood/current.md
  01_mood/events/YYYY-MM.jsonl
  02_personality/deltas.json
  02_personality/about/current.md
  02_personality/about/versions/*.md
  _session.json
  _state.json
books/<book-id>/{source.*,text.txt,structure.json,metadata.json}
.ucho/{users.json,log.md}
```

## documentation

- `.docs/product.md` — цель и контекст.
- `.docs/functionality.md` — канонические требования и acceptance.
- `.docs/technical.md` — текущая реализация.
- `.docs/demo.md` — короткий сценарий для нетехнического читателя.
- `README.md` — запуск, резервное копирование и восстановление.

Комментарии в коде объясняют инварианты и угрозы, а не очевидный синтаксис.

## instructions

### Локальный запуск

```powershell
Copy-Item .env.example .env
docker compose up -d --build bot
docker compose logs -f bot
```

### Развёртывание

`deploy/deploy.sh` подготавливает Docker-хост и отдельные каталоги данных и
копий, обновляет код, запускает проверки и поднимает compose. Подробности —
`deploy/README.md`.

### Тестирование

```powershell
docker compose build bot
docker compose run --rm -e VAULT_PATH=/tmp/ucho-test bot pytest
docker compose run --rm bot ruff check bot scripts tests
docker run --rm -v "${PWD}:/repo" zricethezav/gitleaks:latest detect --source=/repo
```

### Бэкапы

При старте создаётся снимок, если за текущую календарную неделю зоны `DAILY_TZ`
его ещё нет. Затем планировщик делает снимок раз в неделю: по умолчанию в
понедельник в 03:00 зоны `DAILY_TZ`. Если снимок за эту неделю уже есть,
планировщик пропускает запуск.
Операция синхронна внутри event loop, чтобы записи самого бота не пересекались
с копированием. `VAULT_HOST_PATH` и `BACKUP_HOST_PATH` — разные каталоги хоста.
Снимок сначала пишется во временный каталог, затем переименовывается;
`manifest.json` содержит размеры и SHA-256 файлов. Старые снимки удаляются
после `BACKUP_KEEP` готовых копий, максимум четырёх; лишние старые снимки
удаляются также при старте. Это локальная копия на том же хосте, без
автоматической отправки на другой сервер.

## quality

### Чеклисты

- Pytest покрывает команды/layout, raw-before-LLM/recovery, mood/personality,
  книги/reminders, `/leta` и backup.
- `tests/smoke/test_main_paths.py` проверяет чистый vault.
- Перед deploy обязательны полный pytest, smoke, Ruff и Gitleaks.

### Наблюдаемость и логирование

Python logging пишет stderr/docker logs и ротируемый `.logs/bot.log`.
`.ucho/log.md` хранит нейтральные операции без stacktrace для пользователя.

### Безопасность

- Whitelist применяется к message и callback.
- `/upload` и `/sea` дополнительно проверяют owner ID на командах, документах и
  callback из старых сообщений; меню и `/help` скрывают их от остальных.
- Секреты только в `.env`.
- XML DTD/entity запрещены; ZIP paths/объём/число членов проверяются.
- EPUB/FB2/Markdown/TXT разбираются локально; LLM не участвует в индексации,
  извлечении metadata или выборе фрагмента.
- Книжные ID и metadata проверяются до построения пути; symlink-книги отклоняются.
- Session ID ограничен безопасным набором символов до построения пути JSONL.
- Telegram/LLM dynamic output экранируется перед HTML.
- Производные profile/mood и книжный контекст передаются LLM как fenced user-data,
  а не как system prompt.
- Пользовательская запись без request-scoped uid отклоняется.
- `/leta` проверяет точный resolved path `users/<uid>`.
- Recovery, daily и reminder повторно проверяют действующий whitelist.
- Ручные/книжные/daily-вопросы, ответы, `/about`, reminder и `/leta` меняют
  пользовательскую сессию только под одним per-user lock.
- Durable merge-slot принимает текст только во время уже записанного raw-pending
  ответа; другие занятые LLM-операции возвращают busy без привязки к старой сессии.

## rules

### Stage constraints

На текущей стадии делаем:

- Docker deploy, pinned dependencies, structured logs.
- Recovery, атомарная файловая запись и резервные копии (по явному запросу
  сверх обычного минимума POC B).
- Программные тесты критического пути и smoke.

Intentionally deferred:

- MFA, публичная регистрация, полноценный аудит действий.
- Метрики/дашборды/алерты и нагрузочные тесты.
- Автоматическая отправка копий на другой хост и регулярный restore drill.
- Web UI, PostgreSQL, полнотекстовый поиск книг.

## accept

Техническая готовность определяется `.docs/functionality.md` → `## accept` и
финальными Docker-командами из `## instructions`.

## notes

### Active plans

- Реализовано: упрощённое storage/analysis ядро, общая библиотека и файловые копии.
- Реализовано: структурный книжный индекс.
- Отложено: управление жизненным циклом общих книг.

### Manual verification scenarios

1. Новый пользователь: `/ask` → ответ → `/about`; проверить raw/mood/deltas/version.
2. `/upload` EPUB → проверить главы в `structure.json` → `/sea` → выбрать книгу →
   ответить на вопрос.
3. Дождаться книжной цитаты → ответить обычным текстом → убедиться, что score вырос
   один раз и pending очищен.
4. Создать снимок → проверить хеши → создать следующий и проверить ротацию.

### Технические решения

- 2026-07-27: `00_raw/sessions` назначен единственным источником истины.
- 2026-07-27: mood и personality обслуживаются только runtime-кодом.
- 2026-07-27: библиотека глобальна, предпочтения остаются per-user.
- 2026-07-27: EPUB разбирается только in-memory stdlib-парсером.
- 2026-07-27: персона, голос, лицо и пользовательские настройки тона удалены.
- 2026-07-27: все фоновые пути повторно применяют актуальный whitelist.
- 2026-07-28: EPUB/FB2/Markdown индексируются обычными парсерами; LLM видит только
  выбранную цитату, а верхняя глава служит её жёсткой границей.
- 2026-09-19: данные остаются в открытых файлах; Git-коммиты vault заменены
  еженедельными проверяемыми снимками отдельной папки хоста.

### Технический долг

- POC B не блокирует конкурентные uploads разных пользователей общим async lock;
  каждый новый каталог книги публикуется после завершения записи.
- В репозитории пока нет GitHub Actions; pytest, Ruff, smoke и Gitleaks запускаются
  вручную в Docker.

### Журнал изменений

- 2026-07-27: удалены персона и face-подсистема; документация приведена к
  нейтральной упрощённой архитектуре.
- 2026-07-28: TXT/PDF исключены из загрузки; добавлены structure.json и ручная
  переиндексация общей библиотеки.
- 2026-09-19: локальный старый vault перенесён, Git-зависимость данных и
  одноразовые миграции убраны, добавлены файловые снимки.

## ai_pipeline

- `ask`: один вопрос по теме или затравке.
- `ask_book_question`: вопрос только по уже выбранному фрагменту и его chapter
  metadata; полная книга модели не передаётся.
- `classify_mood`: categorical sign/energy/direction/quality/dominance.
- `process`: нейтральная reaction + personality deltas.
- `synthesize_about`: нейтральный внутренний профиль.
- `about_present`: нейтральное представление профиля пользователю.

Primary и fallback выбираются per task из env; специальных внешних скиллов нет.

## telegram_bot

Команды публикуются per-chat scope только доверенным. Владелец дополнительно видит
admin-команды. Polling не сбрасывает pending updates; startup сначала дожимает
pending/queued/offline сообщения, затем выполняет daily catch-up.
