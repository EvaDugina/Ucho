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
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot pytest
docker compose run --rm bot ruff check bot scripts tests
```

## Архитектура

- `00_raw/sessions` — единственный источник истины.
- Pipeline: raw commit → mood → process_answer → personality deltas → реакция.
- Evidence personality-дельты дословно присутствует в raw.
- `/about` синтезирует pending-дельты, версионирует профиль и отдельно формулирует
  нейтральный ответ.
- Per-user данные: `users/<uid>/{00_raw,01_mood,01_personality,_session,_state}`.
- Общая библиотека: `books/<book-id>`.
- `.psycho/`: whitelist и технический лог.

## Инварианты

- Никогда не вызывать LLM до обязательной raw-записи.
- Не возвращать graph/concepts/MOC/digest/psychometrics и специальные skills.
- Список команд изменяется только вместе с каноническим списком в требованиях.
- Неизвестные slash-команды не анализировать.
- Пользовательские Git-транзакции не захватывают чужие данные или `books/`.
- Книжная транзакция ограничена `books/`.
- `/leta` не удаляет общие книги.
- EPUB не извлекается на диск; traversal/DTD/entity/лимиты обязательны.
- Stacktrace остаётся в логах, не в Telegram.

## Миграция

`scripts/migrate_simplified_storage.py` по умолчанию делает preview; `--apply`
включается только вручную и идёт по пользователям с rollback.

## Соглашения

- Комментарии — про намерение и ограничения.
- Значимые решения обновляют `.docs/*` Vibe++.
- Промпты не задают боту персону, лицо, маску или гендер; контракт данных живёт
  в runtime-коде и `prompts/process.md`.
