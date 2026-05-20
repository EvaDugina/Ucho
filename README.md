# Psycho — Telegram-бот для графа внутреннего мира

Личный AI-бот, который ведёт **граф концептов** твоего психо-философского портрета в Obsidian. На каждый ответ он:
- извлекает концепты (принципы, ценности, убеждения, предпочтения),
- простраивает связи между ними (`supports`, `contradicts`, `derived_from`, `related`),
- ищет противоречия с уже зафиксированным и задаёт уточняющие вопросы,
- ведёт острую (но уважительную) дискуссию.

**Приватность:** LLM работает **локально** через Ollama. Данные не уходят в OpenAI/Anthropic. Бот молчит со всеми, кроме владельца (whitelist по `OWNER_TELEGRAM_ID`).

Стадия: **PoC B**. Граф пишется в `C:\Users\eva\YandexDisk\Obsidian\Psycho`.

## Что появляется в вольте

```
Psycho/
├─ raw/2026-05-18.md              # сырые Q&A за день
├─ concepts/
│   ├─ ethics/
│   │   ├─ chestnost.md           # узел графа со связями во frontmatter
│   │   └─ ...
│   ├─ aesthetics/
│   ├─ politics/
│   └─ everyday/
├─ profile/                        # короткие сводки по доменам
└─ _index.md
```

Чтобы увидеть граф: **Obsidian → Graph View → фильтр `path:concepts/`**.

## Установка

### 1. Подготовь среду

- Установи Docker Desktop с включённым **WSL 2 based engine**.
- Драйвер NVIDIA — актуальный (581.x+).
- Желательно — [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#installation) для проброса GPU в Docker. Без него Ollama пойдёт на CPU и 14B-модель будет недопустимо медленной.

### 2. Получи токены

- Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → скопируй токен.
- Свой Telegram user_id: [@userinfobot](https://t.me/userinfobot) → `/start`.
- В @BotFather также выполни:
  - `/setjoingroups` → выбери бота → **Disable** (чтобы не добавляли в группы).
  - `/setprivacy` → **Enable** (бот не видит чужие сообщения).

### 3. Конфиг

```powershell
cp .env.example .env
# заполни TELEGRAM_BOT_TOKEN и OWNER_TELEGRAM_ID
```

### 4. Запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

### 5. Скачай модель (первый раз, ~9 GB)

```powershell
docker exec -it psycho-ollama ollama pull qwen2.5:14b-instruct
```

Проверь, что Ollama видит GPU:
```powershell
docker exec -it psycho-ollama nvidia-smi
```
Если выводит таблицу с RTX 3060 — всё ок. Если нет — Ollama пойдёт на CPU; см. секцию ниже.

### 6. Проверка

В Telegram напиши боту `/start`. Должно прийти приветствие. Дальше:

| Команда | Что делает |
|---|---|
| `/ask` | Открытый вопрос, бот выберет домен сам |
| `/ask ethics` | Вопрос в конкретном домене (`ethics` / `aesthetics` / `politics` / `everyday`) |
| `/discuss` | Бот возьмёт самый «жирный» концепт и будет оппонировать |
| `/discuss chestnost` | Оппонировать по конкретному концепту (slug, как имя файла) |
| `/review` | Поговорить про базу. Можно подтверждать добавление новых концептов |
| `/end` | Закрыть текущую сессию |

После каждого ответа бот:
1. Пишет сырое Q&A в `raw/YYYY-MM-DD.md`.
2. Создаёт/расширяет концепты в `concepts/<domain>/<slug>.md`.
3. Если нашёл противоречие — записывает в обе ноты в `## Открытые вопросы` и задаёт probe в чате.
4. Шлёт острый follow-up, пока сессия не закрыта.

## Если GPU не подцепилась

Закомментируй блок `deploy.resources` в `docker-compose.yml` для сервиса `ollama` и переключи модель на 7B в `.env`:

```
OPENAI_MODEL=qwen2.5:7b-instruct
```

```powershell
docker compose up -d
docker exec -it psycho-ollama ollama pull qwen2.5:7b-instruct
```

На CPU Ryzen 5 5600 7B-модель отвечает за ~5–15 секунд — терпимо.

## Управление контейнерами

```powershell
docker compose down                  # остановить
docker compose up -d --build         # пересобрать bot после правок
docker compose logs --tail=200 bot
docker exec -it psycho-ollama ollama list  # установленные модели
```

## Замечания

- `.env` в `.gitignore`. Не коммить токены.
- Все правки концептов в Obsidian сохраняются — бот при перезаписи читает текущее состояние из frontmatter и тела. **Не меняй имена slug-ов** в frontmatter, иначе ссылки сломаются — переименовывай только содержание.
- Бот никогда не удаляет файлы. Чистить руками в Obsidian, если что-то лишнее.
