# Ucho — Telegram-бот для графа внутреннего мира

Личный AI-бот, который ведёт capture-first базу психо-философского портрета в Obsidian. Live-контур на каждый ответ:
- сохраняет полный session event-log до LLM-разбора,
- извлекает черновые worldview-атомы (`status: draft`) с evidence,
- обновляет Q&A-проекцию, заметки, mood и дельты общего портрета,
- отвечает короткой реакцией от лица Иуды.

Связи между атомами, реальные противоречия, промоушн `draft → stable` и выверенная проза портрета собираются отдельными ручными проходами сильной моделью (`reconcista`, `depersonalization`), а не live-ботом.

**Приватность:** live-LLM работает через внешний OpenAI-compatible provider:
OpenRouter при непустом `OPENROUTER_API_KEY`, иначе AITunnel. Тексты диалога уходят во внешний
AI-провайдер; бот по-прежнему молчит со всеми, кроме доверенных
пользователей (whitelist по `OWNER_TELEGRAM_ID` + `ALLOWED_TELEGRAM_IDS`).

Стадия: **POC B**. Граф пишется в папку, заданную `VAULT_HOST_PATH`; при серверном запуске это путь к vault на сервере (например `/srv/psycho/vault`). Синхронизация хранилища между машинами — только через git.

## Что появляется в вольте

```
Psycho/
├─ 00_raw/
│  ├─ sessions/<session_id>.jsonl # полный event-log: bot/user/comment
│  ├─ qna/2026-05-18.md           # Q&A-проекция дня с block-id ^Q<n>
│  └─ notes/                      # свободные заметки
├─ 01_Мироощущение/
│  ├─ atoms/                      # эмоции, фон, тон мира, телесность
│  ├─ mood/                       # mood events, reports, timeseries, feedback
│  └─ MOC.md
├─ 02_Миропонимание/
│  ├─ atoms/                      # убеждения, принципы, причинность, неопределённость
│  └─ MOC.md
├─ 03_Ценностно-нормативная подсистема/
│  ├─ atoms/                      # ценности, идеалы, нормы, табу, иерархии
│  └─ MOC.md
├─ 04_Практический уровень/
│  ├─ atoms/                      # воля, стиль жизни, поступки, стратегии
│  └─ MOC.md
├─ 05_Общее/                      # about, summary, profile, softskills, user_prompt, deltas
├─ _index.md                       # навигация
├─ _state.json                     # счётчик Q-номеров
└─ _session.json                   # только runtime active-session refs
```

Чтобы увидеть граф мировоззрения: **Obsidian → Graph View → фильтр `path:atoms/`**.

## Установка

### 1. Среда

- Docker Desktop с **WSL 2 based engine**.
- OpenRouter API key (preferred) или AITunnel API key (fallback).

### 2. Токены

- Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → токен.
- OpenRouter: создай ключ и положи его в `OPENROUTER_API_KEY`.
- AITunnel fallback: если OpenRouter не используешь, положи ключ в `AITUNNEL_API_KEY`.
- Свой Telegram user_id: [@userinfobot](https://t.me/userinfobot) → `/start`.
- В @BotFather: `/setjoingroups` → Disable; `/setprivacy` → Enable.

### 3. Конфиг

```powershell
cp .env.example .env
# обязательно заполни:
#   TELEGRAM_BOT_TOKEN
#   OPENROUTER_API_KEY  (или AITUNNEL_API_KEY)
#   OWNER_TELEGRAM_ID
#   VAULT_HOST_PATH  (если хранишь вольт не по дефолтному пути)
```

`DAILY_HOUR` — час ежедневного вопроса в зоне `DAILY_TZ`, не UTC.
`DAILY_REMINDER_START` / `DAILY_REMINDER_END` — окно вечернего напоминания:
в `23:00` бот собирает тех, кто не ответил на сегодняшний daily-вопрос, и
выбирает одно случайное время отправки до `01:00`.

### 4. Запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

Compose поднимает только `bot`; локального LLM-сервиса в проекте больше нет.
Тот же app-log дополнительно пишется в `.logs/bot.log` рядом с `docker-compose.yml`
и ротируется локально (`10 MB x 5` по умолчанию).

### 5. Серверный deploy

Для Ubuntu 24.04/Timeweb есть готовый runbook и скрипты в `deploy/`:

```bash
deploy/deploy.sh   # первый запуск на сервере
deploy/update.sh   # git pull кода + git pull vault + rebuild/restart
deploy/stop.sh     # остановить контейнер bot
```

Docker build берёт базовый образ Python через `PYTHON_BASE_IMAGE`; дефолт в
deploy-скриптах и compose — `mirror.gcr.io/library/python:3.12-slim`, чтобы не
упираться в anonymous pull limit Docker Hub на свежем VPS.
Vault-коммиты бот делает внутри контейнера. Для push по SSH на сервере укажи в
`.env` путь к deploy key на хосте: `VAULT_GIT_SSH_KEY_HOST_PATH=/root/.ssh/<key>`;
compose смонтирует только этот файл read-only.

Подробная инструкция: `deploy/README.md`.

## Команды бота

Источник правды по командам — `bot/main.py::BOT_COMMANDS` и хэндлеры в `bot/handlers.py`.

| Команда | Что делает |
|---|---|
| `/ask [тема]` | Главный вопрос; без темы → inline-кнопки выбора области или случайная область/категория/тема. Открывает сессию |
| `/echo <вопрос>` | Твой собственный вопрос как главный |
| `/ucho <текст>` | Свободная заметка → в граф; открывает сессию |
| `/about` | Каким я тебя вижу — отформатированный портрет, затем обычная сессия |
| `/pebble` | Бросить камень → статичное «Больно.». Прозрачен: не трогает активную сессию |
| `/regen [маска]` | Reply на комментарий Иуды: новая реплика в другой/выбранной маске |
| `/like` | Reply на реплику Иуды: добавить её в избранное |
| `/remask` | Reply на вопрос или комментарий Иуды: открыть меню смены лица |
| `/start` | Смыв: закрыть сессию и убрать отложенный ответ, если он ещё не ушёл в LLM |
| `/leta` | Омыть водами реки забвения черты своего лица; подтверждённое удаление рабочей базы |
| `/help` | Список команд |
| `/adduser` `/removeuser` `/users` | Админ (только владелец) |

Команды, кроме `/pebble`, `/regen`, `/like`, `/remask`, `/leta`, **закрывают** активную сессию; её можно продолжить reply,
потому что полный transcript лежит в `00_raw/sessions`. `/ask` `/echo` `/ucho` `/about` затем открывают новую.
Индикатор «Думаю» (🎰 + текст) показывается только при генерации вопроса (`/ask`) и
портрета (`/about`); реакции в диалоге идут молча. Подсказки команд при наборе `/`
видны **только доверенным** (`BotCommandScopeChat`); админ-блок — только владельцу.

Если человек пишет новый текст, пока предыдущий ответ уже обрабатывается, бот
склеивает эти сообщения в один отложенный ответ через пустую строку и отвечает
`Ещё думаю.`. `/echo <текст>` в этот момент тоже добавляет текст в очередь, а
другие команды только получают `Ещё думаю.`. `/start` удаляет очередь, если она
ещё не ушла в LLM, но не прерывает уже начатую генерацию.

## Модель диалога

Главный вопрос (`/ask`, `/echo`, дневной таймер) **открывает сессию-обсуждение**:

```
главный вопрос  →  ответ  →  реакция-укол (не вопрос)  →  ответ  →  реакция  →  …
```

- Бот **не задаёт уточняющих вопросов** — на каждый ответ даёт короткую **реакцию от первого лица** и ждёт следующего сообщения.
- Сессия закрывается, только когда задан новый главный вопрос или выполнена **любая команда**, кроме `/pebble`, `/regen`, `/like`, `/remask`, `/leta`. Открыта всегда ≤1 сессия.
- **Reply-resume:** ответив (reply) на любое сообщение старой сессии, можно её продолжить; поиск идёт по `00_raw/sessions`.
- Промпты разнесены: `prompts/iuda.md` (персона — характер, голос от 1-го лица) + `base.md` (таксономия мировоззрения и формат JSON) + аддендумы `ask.md` / `process.md` + отдельный `about.md`. Примеры стиля вопросов — `questions_examples.md`.

## Как бот обрабатывает ответ

1. Сначала пишет user-сообщение в `00_raw/sessions/<session_id>.jsonl`, до LLM.
2. LLM (`mode: process`) возвращает только **анализ**: `worldview_observations` (атомы `area/category/theme/type/name/summary/quote/confidence`), `reaction` (реплика от 1-го лица) и опц. `user_delta` (портрет пользователя). Slug/raw/stable-связи модель НЕ присылает.
3. Код пишет дословную Q&A-проекцию в `00_raw/qna/YYYY-MM-DD.md`, выводит slug из имени, через дедуп по всему worldview-графу решает create-vs-update атома (`status: draft`), применяет `user_delta` к `05_Общее/about.md`/`deltas.jsonl`.
4. Связи, реальные противоречия и промоушн `draft → stable` — разбор сильной моделью (скилл `reconcista`); общий портрет, настроение, психометрика и `05_Общее/softskills.md` — скилл `depersonalization`.
5. `reaction` отправляется пользователю и становится якорем следующего хода; сессия остаётся открытой.

## Персистентная сессия

`<vault>/users/<uid>/_session.json` сохраняет только активное runtime-состояние: `session_id`, текущий Q и выбранную тройку `area/category/theme`, timestamps и pending event refs. Полная переписка живёт в `00_raw/sessions`, а не в `_session.json`.

`<vault>/users/<uid>/_state.json` — сквозной счётчик `last_q_num`, daily marker
и план вечернего reminder по неотвеченному daily-вопросу. Тоже переживает рестарт.

## Live-LLM provider

OpenRouter включается с приоритетом, если `OPENROUTER_API_KEY` непустой:

```powershell
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL_DEFAULT=qwen/qwen3-235b-a22b-2507
OPENROUTER_MODEL_FALLBACKS=deepseek/deepseek-v4-flash
```

Если `OPENROUTER_API_KEY` пустой, используется AITunnel fallback:

```powershell
AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1
AITUNNEL_API_KEY=...
LLM_MODEL_DEFAULT=qwen3-235b-a22b-2507
LLM_MODEL_FALLBACKS=deepseek-v4-flash
```

Локальный LLM-контур удалён из runtime. Если модель провайдера недоступна, бот
не сообщает об этом пользователю; при сбое комментария к ответу он берёт короткую
заготовленную реплику, остальные LLM-сбои логируются служебно.

## Управление контейнерами

```powershell
docker compose down                          # остановить
docker compose up -d --build bot             # пересобрать только bot после правок
docker compose logs --tail=200 bot
```

Файловый app-log контейнера:

```powershell
Get-Content .logs\bot.log -Tail 100
```

На сервере путь такой же относительно app-директории: `/srv/psycho/app/.logs/bot.log`.

## Замечания

- `.env` в `.gitignore`. Не коммить токены.
- `.logs/` тоже не коммитится: это runtime-логи контейнера, не часть графа.
- Все правки атомов в Obsidian сохраняются — бот при перезаписи читает текущее состояние из frontmatter и тела. **Не меняй `slug` в frontmatter** — иначе ссылки сломаются. Переименовывай только содержание / заголовок.
- Бот никогда не удаляет файлы. Чистить руками в Obsidian, если что-то лишнее.
- Тема вопроса в сообщении бота выводится **курсивом** как `область / категория / тема`, текст вопроса — **моноширинным** (long-press для копирования).
- Спиннер 🎰/🎲/🎯 во время LLM-вызовов автоматически удаляется по готовности ответа.
