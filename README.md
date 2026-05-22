# Psycho — Telegram-бот для графа внутреннего мира

Личный AI-бот, который ведёт **граф концептов** твоего психо-философского портрета в Obsidian. На каждый ответ он:
- извлекает концепты (принципы, ценности, убеждения, предпочтения),
- простраивает связи между ними (`supports`, `contradicts`, `derived_from`, `related`),
- ищет противоречия с уже зафиксированным и задаёт уточняющие вопросы,
- ведёт острую (но уважительную) дискуссию.

**Приватность:** LLM работает **локально** через Ollama. Данные не уходят в OpenAI/Anthropic. Бот молчит со всеми, кроме владельца (whitelist по `OWNER_TELEGRAM_ID`).

Стадия: **PoC B**. Граф пишется в папку, заданную `VAULT_HOST_PATH` (по умолчанию `C:\Users\eva\YandexDisk\Obsidian\Psycho`).

## Что появляется в вольте

```
Psycho/
├─ raw/2026-05-18.md              # сырые Q&A дня, каждая запись с Q-номером
├─ concepts/
│   ├─ ethics/                    # узлы графа со связями во frontmatter
│   ├─ aesthetics/
│   ├─ politics/
│   ├─ everyday/
│   ├─ relationships/
│   ├─ identity/
│   ├─ mortality/
│   ├─ nationality/
│   ├─ knowledge/
│   └─ work/
├─ profile/                        # короткие сводки по доменам (10 файлов)
├─ _index.md                       # навигация
├─ _state.json                     # счётчик Q-номеров
└─ _session.json                   # активная сессия (создаётся при /ask)
```

Чтобы увидеть граф: **Obsidian → Graph View → фильтр `path:concepts/`**.

## Установка

### 1. Среда

- Docker Desktop с **WSL 2 based engine**.
- Драйвер NVIDIA 581.x+ для GPU-проброса.
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#installation) — без него Qwen 14B будет на CPU и недопустимо медленной.

### 2. Токены

- Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → токен.
- Свой Telegram user_id: [@userinfobot](https://t.me/userinfobot) → `/start`.
- В @BotFather: `/setjoingroups` → Disable; `/setprivacy` → Enable.

### 3. Конфиг

```powershell
cp .env.example .env
# обязательно заполни:
#   TELEGRAM_BOT_TOKEN
#   OWNER_TELEGRAM_ID
#   VAULT_HOST_PATH  (если хранишь вольт не по дефолтному пути)
```

### 4. Запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

### 5. Скачать модель (первый раз, ~9 GB)

```powershell
docker exec -it psycho-ollama ollama pull qwen2.5:14b-instruct
docker exec -it psycho-ollama nvidia-smi   # проверка GPU-проброса
```

## Команды бота

| Команда | Что делает |
|---|---|
| `/ask` | Главный вопрос. Без аргумента → inline-кнопки выбора домена + случайный домен |
| `/ask <domain>` | Главный вопрос в конкретном домене |
| `/requestion <текст>` | Твой собственный вопрос как главный (категория «пользовательский») |
| `/discuss [slug \| domain]` | Оппонировать по концепту или в домене. Без лимитов, закрывается только `/end` |
| `/review` | Разговор о базе знаний. Может предлагать добавления с подтверждением |
| `/history` | Все вопросы и ответы со сквозными номерами |
| `/retry <N>` | Задать заново вопрос Q\<N\> — с новым номером, как свежий главный |
| `/answer <N> <текст>` | Ответить на Q\<N\> минуя сессию (для случая «бот рестартовал», «хочу дополнить старый ответ») |
| `/ping` | Проверка живости бота и round-trip LLM |
| `/end` / `/skip` | Закрыть текущую сессию |
| `/start` | Подсказка по командам |

Подсказки команд при наборе `/` показываются **только владельцу** (`BotCommandScopeChat`).

## Модель диалога

Главный вопрос (`/ask`, `/echo`, `/requestion`, дневной таймер) **открывает сессию-обсуждение**:

```
главный вопрос  →  ответ  →  реакция-укол (не вопрос)  →  ответ  →  реакция  →  …
```

- Бот **не задаёт уточняющих вопросов** — на каждый ответ даёт короткую **реакцию от первого лица** и ждёт следующего сообщения.
- Сессия закрывается, только когда задан новый главный вопрос или выполнена **любая команда**. Открыта всегда ≤1 сессия.
- **Reply-resume:** ответив (reply) на любое сообщение одной из **последних 25** сессий, можно её продолжить (снапшоты — `_sessions.json`).
- Промпты разнесены: общий `prompts/base.md` (характер, голос от 1-го лица) + `ask.md` / `process.md` / `review.md` / `summarize.md`.

## Как бот обрабатывает ответ

1. Пишет сырое Q&A в `raw/YYYY-MM-DD.md` с заголовком `## Q42 · 14:32 · ethics`.
2. LLM (`mode: process`) возвращает только **анализ**: `observations` (атомы `domain/type/name/summary/quote`), `reaction` (реплика от 1-го лица) и опц. `user_delta` (портрет пользователя). Slug/raw/связи модель НЕ присылает.
3. Код пишет дословный raw, выводит slug из имени, через дедуп решает create-vs-update концепта (`status: draft`), применяет `user_delta` к `about_user.md`.
4. Связи, реальные противоречия и промоушн `draft → stable` — еженедельный разбор сильной моделью (скилл `weekly-review`).
5. `reaction` отправляется пользователю и становится якорем следующего хода; сессия остаётся открытой.

## Персистентная сессия

`<vault>/_session.json` сохраняется на каждое изменение (новый вопрос, ответ пользователя, изменение pending-добавлений). На старте бот её восстанавливает — рестарт контейнера не теряет контекст. `/end` файл удаляет.

`<vault>/_state.json` — сквозной счётчик `last_q_num`. Тоже переживает рестарт.

## Если GPU не подцепилась

Закомментируй блок `deploy.resources` в `docker-compose.yml` для сервиса `ollama` и переключи модель на 7B:

```powershell
# в .env
OPENAI_MODEL=qwen2.5:7b-instruct
```

```powershell
docker compose up -d
docker exec -it psycho-ollama ollama pull qwen2.5:7b-instruct
```

На CPU Ryzen 5 5600 7B-модель отвечает за ~5–15 секунд — терпимо.

## Управление контейнерами

```powershell
docker compose down                          # остановить
docker compose up -d --build bot             # пересобрать только bot после правок
docker compose logs --tail=200 bot
docker exec -it psycho-ollama ollama list    # установленные модели
```

## Замечания

- `.env` в `.gitignore`. Не коммить токены.
- Все правки концептов в Obsidian сохраняются — бот при перезаписи читает текущее состояние из frontmatter и тела. **Не меняй `slug` в frontmatter** — иначе ссылки сломаются. Переименовывай только содержание / заголовок.
- Бот никогда не удаляет файлы. Чистить руками в Obsidian, если что-то лишнее.
- Категория домена в сообщении бота выводится **курсивом**, текст вопроса — **моноширинным** (long-press для копирования).
- Спиннер 🎰/🎲/🎯 во время LLM-вызовов автоматически удаляется по готовности ответа.
