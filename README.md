# Ucho — Telegram-бот для графа внутреннего мира

Личный AI-бот, который ведёт capture-first базу психо-философского портрета в Obsidian. Live-контур на каждый ответ:
- сохраняет полный session event-log до LLM-разбора,
- извлекает черновые наблюдения-концепты (`status: draft`) с evidence,
- обновляет Q&A-проекцию, заметки, mood/personality deltas,
- отвечает короткой реакцией от лица Иуды.

Связи между концептами, реальные противоречия, промоушн `draft → stable` и выверенная проза портрета собираются отдельными ручными проходами сильной моделью (`reconcista`, `depersonalization`), а не live-ботом.

**Приватность:** live-LLM работает через AITunnel. Тексты диалога уходят во внешний
AI-провайдер по AITunnel API; бот по-прежнему молчит со всеми, кроме доверенных
пользователей (whitelist по `OWNER_TELEGRAM_ID` + `ALLOWED_TELEGRAM_IDS`).

Стадия: **POC B**. Граф пишется в папку, заданную `VAULT_HOST_PATH`; при серверном запуске это путь к vault на сервере (например `/srv/psycho/vault`). Синхронизация хранилища между машинами — только через git.

## Что появляется в вольте

```
Psycho/
├─ 00_raw/
│  ├─ sessions/<session_id>.jsonl # полный event-log: bot/user/comment
│  ├─ qna/2026-05-18.md           # Q&A-проекция дня с block-id ^Q<n>
│  └─ notes/                      # свободные заметки
├─ 01_mood/                       # mood events, reports, timeseries
├─ 02_concepts/
│   ├─ ethics/                    # draft/stable узлы; live-бот пишет только draft-наблюдения
│   ├─ aesthetics/
│   ├─ politics/
│   ├─ everyday/
│   ├─ relationships/
│   ├─ identity/
│   ├─ mortality/
│   ├─ nationality/
│   ├─ knowledge/
│   └─ work/
├─ 02_profile/                    # короткие сводки по доменам (10 файлов)
├─ 02_digest/                     # обзоры/дайджесты
├─ 03_personality/                # about, mood, profile, softskills, deltas
├─ _index.md                       # навигация
├─ _state.json                     # счётчик Q-номеров
└─ _session.json                   # только runtime active-session refs
```

Чтобы увидеть граф: **Obsidian → Graph View → фильтр `path:02_concepts/`**.

## Установка

### 1. Среда

- Docker Desktop с **WSL 2 based engine**.
- AITunnel API key.

### 2. Токены

- Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → токен.
- AITunnel: создай ключ в личном кабинете и положи его в `AITUNNEL_API_KEY`.
- Свой Telegram user_id: [@userinfobot](https://t.me/userinfobot) → `/start`.
- В @BotFather: `/setjoingroups` → Disable; `/setprivacy` → Enable.

### 3. Конфиг

```powershell
cp .env.example .env
# обязательно заполни:
#   TELEGRAM_BOT_TOKEN
#   AITUNNEL_API_KEY
#   OWNER_TELEGRAM_ID
#   VAULT_HOST_PATH  (если хранишь вольт не по дефолтному пути)
```

`DAILY_HOUR` — час ежедневного вопроса в зоне `DAILY_TZ`, не UTC.

### 4. Запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

Compose поднимает только `bot`; локального LLM-сервиса в проекте больше нет.

## Команды бота

Источник правды по командам — `bot/main.py::BOT_COMMANDS` и хэндлеры в `bot/handlers.py`.

| Команда | Что делает |
|---|---|
| `/ask [тема]` | Главный вопрос; без темы → inline-кнопки выбора домена. Открывает сессию |
| `/echo <вопрос>` | Твой собственный вопрос как главный |
| `/ucho <текст>` | Свободная заметка → в граф; открывает сессию |
| `/requestion <N>` | Повторить выбранный вопрос Q\<N\> (мгновенно, без LLM) |
| `/about` | Каким я тебя вижу — отформатированный портрет, затем обычная сессия |
| `/history` | Последние 25 заданных вопросов (без ответов) |
| `/pebble` | Бросить камень → «буль.». Прозрачен: не трогает активную сессию |
| `/like` | Reply на реплику Иуды: добавить её в избранное |
| `/remask` | Reply на вопрос или комментарий Иуды: открыть меню смены лица |
| `/start` | Смыв: закрыть сессию (данные целы) |
| `/help` | Список команд |
| `/adduser` `/removeuser` `/users` `/dailyall` | Админ (только владелец) |

Команды, кроме `/pebble`, `/like`, `/remask`, **закрывают** активную сессию; её можно продолжить reply,
потому что полный transcript лежит в `00_raw/sessions`. `/ask` `/echo` `/ucho` `/about` `/requestion` затем открывают новую.
Индикатор «Думаю» (🎰 + текст) показывается только при генерации вопроса (`/ask`) и
портрета (`/about`); реакции в диалоге идут молча. Подсказки команд при наборе `/`
видны **только доверенным** (`BotCommandScopeChat`); админ-блок — только владельцу.

## Модель диалога

Главный вопрос (`/ask`, `/echo`, `/requestion`, дневной таймер) **открывает сессию-обсуждение**:

```
главный вопрос  →  ответ  →  реакция-укол (не вопрос)  →  ответ  →  реакция  →  …
```

- Бот **не задаёт уточняющих вопросов** — на каждый ответ даёт короткую **реакцию от первого лица** и ждёт следующего сообщения.
- Сессия закрывается, только когда задан новый главный вопрос или выполнена **любая команда**, кроме `/pebble`, `/like`, `/remask`. Открыта всегда ≤1 сессия.
- **Reply-resume:** ответив (reply) на любое сообщение старой сессии, можно её продолжить; поиск идёт по `00_raw/sessions`.
- Промпты разнесены: `prompts/iuda.md` (персона — характер, голос от 1-го лица) + `base.md` (домены, концепты, формат JSON) + аддендумы `ask.md` / `process.md` + отдельный `about.md`. Примеры стиля вопросов — `questions_examples.md`.

## Как бот обрабатывает ответ

1. Сначала пишет user-сообщение в `00_raw/sessions/<session_id>.jsonl`, до LLM.
2. LLM (`mode: process`) возвращает только **анализ**: `observations` (атомы `domain/type/name/summary/quote`), `reaction` (реплика от 1-го лица) и опц. `user_delta` (портрет пользователя). Slug/raw/связи модель НЕ присылает.
3. Код пишет дословную Q&A-проекцию в `00_raw/qna/YYYY-MM-DD.md`, выводит slug из имени, через дедуп решает create-vs-update концепта (`status: draft`), применяет `user_delta` к `03_personality/about.md`/`deltas.jsonl`.
4. Связи, реальные противоречия и промоушн `draft → stable` — разбор сильной моделью (скилл `reconcista`); портрет, настроение, психометрика и `03_personality/softskills.md` — скилл `depersonalization`.
5. `reaction` отправляется пользователю и становится якорем следующего хода; сессия остаётся открытой.

## Персистентная сессия

`<vault>/users/<uid>/_session.json` сохраняет только активное runtime-состояние: `session_id`, текущий Q/domain, timestamps и pending event refs. Полная переписка живёт в `00_raw/sessions`, а не в `_session.json`.

`<vault>/users/<uid>/_state.json` — сквозной счётчик `last_q_num` и daily marker. Тоже переживает рестарт.

## Модели AITunnel

Дефолтный live-контур:

```powershell
AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1
AITUNNEL_API_KEY=...
LLM_MODEL_DEFAULT=qwen3-235b-a22b-2507
LLM_MODEL_FALLBACKS=deepseek-v4-flash
```

Локальный LLM-контур удалён из runtime. Если AITunnel-модель недоступна, бот
покажет предупреждение в Telegram и запишет служебное предупреждение в vault log.

## Управление контейнерами

```powershell
docker compose down                          # остановить
docker compose up -d --build bot             # пересобрать только bot после правок
docker compose logs --tail=200 bot
```

## Замечания

- `.env` в `.gitignore`. Не коммить токены.
- Все правки концептов в Obsidian сохраняются — бот при перезаписи читает текущее состояние из frontmatter и тела. **Не меняй `slug` в frontmatter** — иначе ссылки сломаются. Переименовывай только содержание / заголовок.
- Бот никогда не удаляет файлы. Чистить руками в Obsidian, если что-то лишнее.
- Категория домена в сообщении бота выводится **курсивом**, текст вопроса — **моноширинным** (long-press для копирования).
- Спиннер 🎰/🎲/🎯 во время LLM-вызовов автоматически удаляется по готовности ответа.
