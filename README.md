# Ухо

Стадия: **POC B**

Закрытый нейтральный Telegram-бот. Бот задаёт вопросы, сохраняет
дословный разговор до LLM-анализа, замечает настроение, накапливает подтверждённые
наблюдения о характере и поддерживает разговоры по общей библиотеке книг.

## Что умеет

- raw-first диалог с recovery после рестарта;
- десять тем `/ask` как навигация и metadata;
- свободные заметки `/ucho`, начинающие новый контекст разговора;
- mood current и помесячный event-log для каждого доверенного пользователя;
- pending personality-дельты с дословной evidence и версионируемый `/about`;
- общая загрузка EPUB/FB2/Markdown/TXT и книжные разговоры `/sea`;
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
- `/leta`
- `/help`
- `/start`

Только владелец:

- `/upload`
- `/sea`
- `/adduser <telegram_user_id>`
- `/removeuser <telegram_user_id>`
- `/users`

Неизвестная slash-команда отвечает ссылкой на `/help` и не анализируется.

## Хранилище

```text
vault/
├── users/<uid>/
│   ├── 00_raw/sessions/*.{jsonl,md}
│   ├── 01_mood/current.md
│   ├── 01_mood/events/YYYY-MM.jsonl
│   ├── 02_personality/deltas.json
│   ├── 02_personality/about/current.md
│   ├── 02_personality/about/versions/*.md
│   ├── _session.json
│   └── _state.json
├── books/<book-id>/
│   ├── source.<ext>
│   ├── text.txt
│   ├── structure.json
│   └── metadata.json
└── .ucho/
    ├── users.json
    └── log.md
```

`00_raw/sessions/*.jsonl` — единственный источник истины переписки. Рядом с каждым
JSONL автоматически создаётся читаемая Markdown-страница для Obsidian. Бот
пересоздаёт её из JSONL после новой записи и при старте, поэтому правки страницы
будут перезаписаны. `_session.json` нужен
только для recovery, `_state.json` — для нумерации, расписания и персонального
книжного состояния. Библиотека `books/` общая.
Имена пары файлов имеют вид `YYYYMMDDTHHMMSS_<uuid>.jsonl` и
`YYYYMMDDTHHMMSS_<uuid>.md`, поэтому разговоры сортируются по времени начала.

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
VAULT_HOST_PATH=C:/path/to/UchoVault
BACKUP_HOST_PATH=C:/path/to/UchoBackups
```

Если задан `OPENROUTER_API_KEY`, он используется вместо AITunnel.

```powershell
docker compose up -d --build bot
docker compose logs -f bot
```

Исходники копируются в image, поэтому после правок нужен `--build`.

## Книги

Владелец может загрузить EPUB, FB2, `.md`, `.markdown` и `.txt` до 20 МБ.
PDF отклоняется.

- Отправьте документ с caption `/upload`; или
- отправьте `/upload`, затем один документ в течение десяти минут.

Структура извлекается обычными парсерами без LLM. EPUB использует nav/NCX или
linear spine fallback, FB2 — верхние section основного body, Markdown — CommonMark
headings, TXT — абзацы в одном разделе с названием файла. EPUB разбирается в памяти
без извлечения ZIP на диск; проверяются
traversal, DTD/entity, повреждения, число файлов и общий распакованный объём.

Фрагмент выбирается только внутри одной верхней главы. Лишь после этого LLM
получает цитату до 1200 символов и формирует вопрос; полная книга модели не
передаётся. SHA-256 нормализованного текста определяет дедупликацию новых книг.

Владелец в `/sea` может выбрать книгу или открыть свои настройки напоминаний. Новая
книга включена у всех по умолчанию. Вес для цитаты:

```text
score(book) - min_score(включённых книг) + 1
```

Первый следующий обычный текст без другой reply-цели повышает score выбранной книги
ровно на один.

## Резервные копии

Рабочие файлы постоянно находятся в `VAULT_HOST_PATH` на хосте и доступны
Obsidian как обычная папка. Git-репозитория данных бот не создаёт.
Готовые снимки пишутся в отдельный `BACKUP_HOST_PATH`: при старте, если за
текущую неделю копии нет, и раз в неделю в `BACKUP_WEEKDAY` и `BACKUP_HOUR`
по `DAILY_TZ`. По умолчанию это понедельник в 03:00. Хранятся не более четырёх
последних снимков всей папки, то есть не более четырёх копий каждого пользователя;
после успешной новой копии самая старая удаляется. Без явного пути Compose
использует `../UchoBackups`.
В копию не попадают `.git`, `.obsidian`, ключи и временные файлы.

Создать снимок вручную и проверить его:

```powershell
docker compose run --rm bot python -m bot.backup create /vault /backups --keep 4
docker compose run --rm bot python -m bot.backup verify "/backups/<имя-снимка>"
```

Восстановление делает копию в новый пустой каталог. Остановите бота, проверьте
снимок, затем смонтируйте на хосте отдельный каталог назначения как
`/restore-parent` и выполните команду ниже. После сверки укажите новый
путь в `VAULT_HOST_PATH` и запустите бота. Ничего не перезаписывается поверх
текущих данных. Копии находятся на том же хосте; перенос на другой хост пока
выполняется вручную.

```powershell
docker compose run --rm -v "C:/path/to/restore-parent:/restore-parent" bot python -m bot.backup restore "/backups/<имя-снимка>" /restore-parent/ucho-restored
```

Если индекс старой EPUB/FB2/Markdown/TXT-книги повреждён, при остановленном боте
сначала проверьте состояние, затем восстановите его из сохранённого исходника:

```powershell
docker compose run --rm bot python scripts/repair_books.py
docker compose run --rm bot python scripts/repair_books.py --apply
```

## Тесты и проверки

Всё исполняется только в Docker.

```powershell
docker compose build bot
docker compose run --rm -e VAULT_PATH=/tmp/ucho-test bot pytest
docker compose run --rm -e VAULT_PATH=/tmp/ucho-smoke bot pytest tests/smoke
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
- Принятый текст и выбранная книжная цитата передаются настроенному внешнему
  LLM-провайдеру; полная книга не передаётся.
- Незнакомые пользователи игнорируются middleware.
- Удалённые из whitelist пользователи не получают recovery и фоновые сообщения.
- Бот не использует ролевую персону, лица, маски или настройку собственного тона.
- `.env`, ключи и runtime-логи не коммитятся.
- `/leta` очищает только активный каталог текущего пользователя; старые
  резервные снимки остаются до ротации.

Подробные требования и устройство: [.docs/functionality.md](.docs/functionality.md)
и [.docs/technical.md](.docs/technical.md).
