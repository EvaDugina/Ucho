# AGENTS.md

## Что это

Telegram-бот «Ухо» без ролевой персоны. Стадия — **POC B**. Бот хранит
raw-разговор, производные mood/personality и общую библиотеку книг. Рабочий язык
кода, документации и промптов — русский.

Канонические документы:

- `.docs/product.md` — цель;
- `.docs/functionality.md` — требования и приёмка;
- `.docs/technical.md` — реализация;
- `.docs/demo.md` — демо.

## Запуск и тесты

Любое выполнение кода — только в Docker. `.env` закрыт для чтения.

```powershell
docker compose up -d --build bot
docker compose logs -f bot
docker compose run --rm -e VAULT_PATH=/tmp/psycho-test bot pytest
docker compose run --rm bot ruff check bot scripts tests
```

После изменений `bot/`, `prompts/`, `scripts/`, `tests/` image нужно пересобрать:
эти каталоги копируются на build-стадии.

## Архитектура

- `00_raw/sessions` — единственный источник истины переписки.
- Обработка текста: raw commit → mood → process_answer → personality deltas →
  реакция.
- Mood работает через один LLM-классификатор для всех доверенных.
- Personality delta валидна только с дословной quote из raw.
- `/about` сводит pending-дельты в нейтральный профиль и отдельным вызовом
  нейтрально показывает его пользователю.
- Per-user маршрутизация — `userctx`/`contextvar`, данные в `users/<uid>`.
- Общие книги — `books/`; toggles/scores/pending — в `_state.json` пользователя.
- `_session.json` хранит только recovery/queue runtime.
- `.psycho/` содержит whitelist и технический лог.

## Критичные инварианты

- Raw обязательно записывается до LLM. При ошибке pending ref остаётся для recovery.
- Вопросы отправляются через `services/session_messages.py`, чтобы Telegram ID попал
  в session-log.
- Новые публичные каталоги пользователя ограничены `00_raw`, `01_mood`,
  `01_personality`.
- Не возвращать `qna`, `notes`, graph/concepts/MOC/profile/digest и psychometrics.
- Список команд изменяется только вместе с каноническим списком в требованиях.
- Неизвестные slash-команды не анализировать.
- Пользовательский Git scope — `users/<uid>`; книжный — `books/`.
- `/leta` удаляет только `users/<uid>` и не трогает общую библиотеку.
- EPUB/FB2 — недоверенный ввод: без извлечения ZIP на диск, с лимитами и traversal
  проверками.
- Ошибки не показывают stacktrace в Telegram.

## Миграция

`scripts/migrate_simplified_storage.py` без аргументов — preview, `--apply` —
пользовательские Git-транзакции. Автоматически при старте не запускать.

## Соглашения

- Комментарии объясняют намерение и угрозы.
- Значимые решения отражаются в `.docs/*` в формате Vibe++.
- Промпты не задают боту персону, лицо, маску или гендер.
