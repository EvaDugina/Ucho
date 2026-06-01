# Deploy Ucho на Ubuntu 24.04

Инструкция рассчитана на Timeweb/VPS с Ubuntu 24.04. Боту не нужен домен,
nginx или входящий HTTP: Telegram работает через polling. Хранилище/vault
синхронизируется только через git.

## Что положить на сервер

Через WinSCP можно загрузить папку `deploy/` в любую временную директорию,
например:

```bash
/root/ucho-deploy/
  deploy.sh
  update.sh
  stop.sh
  README.md
  .env          # опционально, можно создать позже вручную
```

`.env` не коммитится. Минимальные секреты:

```env
TELEGRAM_BOT_TOKEN=...
OWNER_TELEGRAM_ID=...
OPENROUTER_API_KEY=...   # preferred
# AITUNNEL_API_KEY=...   # fallback, если OpenRouter не используешь
VAULT_HOST_PATH=/srv/psycho/vault
VAULT_PATH=/vault
```

## Первый деплой

Зайти на сервер:

```bash
ssh root@SERVER_IP
cd /root/ucho-deploy
chmod +x deploy.sh update.sh stop.sh
```

Если vault уже лежит в отдельном git-репозитории, передай его URL:

```bash
VAULT_REPO_URL=git@github.com:YOUR_USER/YOUR_VAULT_REPO.git ./deploy.sh
```

Если vault пока не готов, можно запустить без `VAULT_REPO_URL`; скрипт создаст
локальную папку `/srv/psycho/vault`, но для нормальной серверной синхронизации
remote всё равно нужно добавить позже.

```bash
./deploy.sh
```

Что делает `deploy.sh`:

- ставит `git`, Docker Engine и Docker Compose plugin;
- создаёт `/srv/psycho/app` и `/srv/psycho/vault`;
- клонирует код бота из `https://github.com/EvaDugina/Ucho.git`;
- при наличии `VAULT_REPO_URL` клонирует/обновляет vault;
- копирует `.env` из папки запуска, если он лежит рядом со скриптом;
- проверяет, что заполнены `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID` и один
  LLM-ключ: `OPENROUTER_API_KEY` или `AITUNNEL_API_KEY`;
- запускает smoke-тесты;
- пересобирает и поднимает контейнер `psycho-bot`.

Сборка по умолчанию использует `mirror.gcr.io/library/python:3.12-slim` как
публичное зеркало официального Python-образа. Это нужно, чтобы свежий VPS не
падал на anonymous pull limit Docker Hub. Если на сервере сделан `docker login`
и нужен именно Docker Hub, можно переопределить образ:

```bash
PYTHON_BASE_IMAGE=python:3.12-slim VAULT_REPO_URL=git@github.com:YOUR_USER/YOUR_VAULT_REPO.git ./deploy.sh
```

Если `.env` ещё не заполнен, скрипт создаст `/srv/psycho/app/.env` из примера и
остановится. После заполнения повтори:

```bash
nano /srv/psycho/app/.env
/root/ucho-deploy/deploy.sh
```

## Проверка

```bash
cd /srv/psycho/app
docker compose ps bot
docker compose logs --tail=100 bot
```

В Telegram:

```text
/pebble
```

Ожидаемый ответ:

```text
Больно.
```

## Обновление

После первого деплоя основной update-скрипт живёт в репозитории:

```bash
/srv/psycho/app/deploy/update.sh
```

Он делает:

- `git pull --ff-only` кода бота;
- `git fetch --all --prune` + `git pull --ff-only` vault, если путь из
  `VAULT_HOST_PATH` в `.env` является git checkout;
- smoke-тесты;
- `docker compose up -d --build bot`.

Если нужно срочно обновиться без smoke-тестов:

```bash
SKIP_TESTS=1 /srv/psycho/app/deploy/update.sh
```

Если зеркало Docker Official Images недоступно, можно временно вернуться к
Docker Hub после авторизации:

```bash
PYTHON_BASE_IMAGE=python:3.12-slim /srv/psycho/app/deploy/update.sh
```

## Остановка

Остановить только контейнер бота, не трогая файлы app/vault:

```bash
/srv/psycho/app/deploy/stop.sh
```

Если запускаешь `stop.sh` из временной папки WinSCP до первого deploy, он будет
искать приложение в `/srv/psycho/app`. При другом пути передай `APP_DIR`:

```bash
APP_DIR=/path/to/app ./stop.sh
```

## Частые команды

```bash
cd /srv/psycho/app
docker compose logs -f bot
docker compose restart bot
docker compose down
docker compose up -d --build bot
```

Vault:

```bash
git -C /srv/psycho/vault status
git -C /srv/psycho/vault pull --ff-only
git -C /srv/psycho/vault push
```

Перед переносом live-режима на сервер останови локальный контейнер с тем же
Telegram token, иначе два poller'а будут мешать друг другу.
