# Ухо

Стадия: **POC B**

Закрытый Telegram-бот с характером Иуды из Кариота. Бот задаёт вопросы, сохраняет
дословный разговор до LLM-анализа, замечает настроение, накапливает подтверждённые
наблюдения о характере и поддерживает разговоры по общей библиотеке книг.

## Что умеет

- raw-first диалог с recovery после рестарта;
- десять тем `/ask` как навигация и metadata;
- свободные заметки `/ucho`;
- mood current и помесячный event-log для каждого доверенного пользователя;
- pending personality-дельты с дословной evidence и версионируемый `/about`;
- face actions `/like`, `/regen`, `/remask`;
- общая загрузка TXT/MD/EPUB/FB2 и книжные разговоры `/sea`;
- персональные книжные toggles, scores и цитаты вместо reminder-текста;
- whitelist и изоляция `users/<uid>`.

Графа мировоззрения, концептов, MOC, digest, psychometrics и специальных агентских
скиллов в проекте больше нет.

## Команды

Для всех доверенных:

- `/pebble`
- `/ucho <текст>`
- `/ask [тема или затравка]`
- `/about`
- `/regen [маска]` — reply на реакцию
- `/like` — reply на реакцию
- `/remask` — reply на вопрос или реакцию
- `/leta`
- `/help`
- `/start`
- `/upload`
- `/sea`

Только владелец:

- `/adduser <telegram_user_id>`
- `/removeuser <telegram_user_id>`
- `/users`

Неизвестная slash-команда отвечает ссылкой на `/help` и не анализируется.

## Хранилище

```text
vault/
├── users/<uid>/
│   ├── 00_raw/sessions/*.jsonl
│   ├── 01_mood/current.md
│   ├── 01_mood/events/YYYY-MM.jsonl
│   ├── 01_personality/deltas.json
│   ├── 01_personality/about/current.md
│   ├── 01_personality/about/versions/*.md
│   ├── 01_personality/face/*
│   ├── _session.json
│   └── _state.json
├── books/<book-id>/
│   ├── source.<ext>
│   ├── text.txt
│   └── metadata.json
└── .psycho/
    ├── users.json
    └── log.md
```

`00_raw/sessions` — единственный источник истины переписки. `_session.json` нужен
только для recovery, `_state.json` — для нумерации, расписания и персонального
книжного состояния. Библиотека `books/` общая.

## Быстрый старт

Нужны Docker Engine и Docker Compose.

```powershell
Copy-Item .env.example .env
```

Заполните минимум:

```dotenv
TELEGRAM_BOT_TOKEN=...
OWNER_TELEGRAM_ID=...
AITUNNEL_API_KEY=...
VAULT_HOST_PATH=C:/path/to/vault
```

Если задан `OPENROUTER_API_KEY`, он используется вместо AITunnel.

```powershell
docker compose up -d --build bot
docker compose logs -f bot
```

Исходники копируются в image, поэтому после правок нужен `--build`.

## Книги

Поддерживаются TXT, MD, EPUB и FB2 до 20 МБ.

- Отправьте документ с caption `/upload`; или
- отправьте `/upload`, затем один документ в течение десяти минут.

EPUB разбирается в памяти без извлечения ZIP на диск. Проверяются traversal,
DTD/entity, повреждения, число файлов и общий распакованный объём. Идентичность книги
— SHA-256 нормализованного текста; дубликат не перезаписывается.

В `/sea` можно выбрать книгу или открыть персональные настройки напоминаний. Новая
книга включена у всех по умолчанию. Вес для цитаты:

```text
score(book) - min_score(включённых книг) + 1
```

Первый следующий обычный текст без другой reply-цели повышает score выбранной книги
ровно на один.

## Миграция старого vault

Миграция никогда не запускается автоматически.

Preview:

```powershell
docker compose run --rm bot python scripts/migrate_simplified_storage.py
```

Apply:

```powershell
docker compose run --rm bot python scripts/migrate_simplified_storage.py --apply
```

Она импортирует уникальные Q&A/notes в `legacy-import.jsonl`, переносит mood/about,
face и старые дельты, проверяет количество событий и удаляет legacy-деревья. Каждый
пользователь обрабатывается в отдельной Git-транзакции. Изменённый вручную
`.obsidian/graph.json` остаётся с предупреждением; неизменённый старый шаблон
удаляется. Повторный запуск идемпотентен.

## Тесты и проверки

Всё исполняется только в Docker.

```powershell
docker compose build bot
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot pytest
docker compose run --rm -e VAULT_PATH=/tmp/psycho-smoke bot pytest tests/smoke
docker compose run --rm bot ruff check bot scripts tests
docker run --rm -v "${PWD}:/repo" zricethezav/gitleaks:latest detect --source=/repo
```

## Деплой

Серверные скрипты находятся в `deploy/`. Основной путь:

```bash
./deploy/deploy.sh
```

Параметры репозитория, ветки, app/vault каталогов и отключение отдельных шагов
описаны в [deploy/README.md](deploy/README.md).

## Приватность и безопасность

- Владелец инфраструктуры имеет доступ к vault.
- Принятый текст передаётся настроенному внешнему LLM-провайдеру.
- Незнакомые пользователи игнорируются middleware.
- `.env`, ключи и runtime-логи не коммитятся.
- Git-коммиты пользовательских данных ограничены `users/<uid>`, книжные —
  `books/`.
- `/leta` удаляет только каталог текущего пользователя и не затрагивает книги.

Подробные требования и устройство: [.docs/functionality.md](.docs/functionality.md)
и [.docs/technical.md](.docs/technical.md).
