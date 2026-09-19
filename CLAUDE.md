# CLAUDE.md

## Что это

Telegram-бот «Ухо» без ролевой персоны. Стадия — **POC B**. Бот хранит
raw-разговор, производные mood/personality и общую библиотеку книг. Рабочий язык
кода, документации и промптов — русский.

Канонические документы: `.docs/product.md`, `.docs/functionality.md`,
`.docs/technical.md`, `.docs/demo.md`.

## Запуск и тесты

Выполнять код только в Docker. `.env` не читать и не коммитить.

```powershell
docker compose up -d --build bot
docker compose run --rm -e VAULT_PATH=/tmp/ucho-test bot pytest
docker compose run --rm bot ruff check bot scripts tests
```

## Архитектура

- `00_raw/sessions` — единственный источник истины.
- Pipeline: raw fsync → mood → process_answer → personality deltas → реакция.
- Evidence personality-дельты дословно присутствует в raw.
- `/about` синтезирует pending-дельты, версионирует профиль и отдельно формулирует
  нейтральный ответ.
- Per-user данные: `users/<uid>/{00_raw,01_mood,02_personality,_session,_state}`.
- Общая библиотека: `books/<book-id>`.
- `.ucho/`: whitelist и технический лог.
- Рабочие данные в `VAULT_HOST_PATH`; снимки в отдельном `BACKUP_HOST_PATH`.

## Инварианты

- Никогда не вызывать LLM до обязательной raw-записи.
- Не возвращать graph/concepts/MOC/digest/psychometrics и специальные skills.
- Список команд изменяется только вместе с каноническим списком в требованиях.
- Неизвестные slash-команды не анализировать.
- Пользовательская запись требует текущий uid.
- Новая книга становится видна только после полной записи файлов.
- `/leta` не удаляет общие книги.
- EPUB не извлекается на диск; traversal/DTD/entity/лимиты обязательны.
- Stacktrace остаётся в логах, не в Telegram.

## Резервные копии

Снимки создаются при старте без копии за текущую неделю и еженедельно;
хранятся не более четырёх готовых снимков. Проверка —
`python -m bot.backup verify <путь>`; восстановление только в новый каталог.
Git для пользовательских данных не используется.

## Соглашения

- Комментарии — про намерение и ограничения.
- Значимые решения обновляют `.docs/*` Vibe++.
- Промпты не задают боту персону, лицо, маску или гендер; контракт данных живёт
  в runtime-коде и `prompts/process.md`.
