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
# TELEGRAM_PROXY_URL=http://proxy-host:3128  # optional, если Telegram доступен только через proxy
OWNER_TELEGRAM_ID=...
OPENROUTER_API_KEY=...   # preferred
# AITUNNEL_API_KEY=...   # fallback, если OpenRouter не используешь
VAULT_HOST_PATH=/srv/psycho/vault
VAULT_PATH=/vault
VAULT_GIT_USER_NAME=Psycho Bot
VAULT_GIT_USER_EMAIL=psycho-bot@local
# VAULT_GIT_SSH_KEY_HOST_PATH=/root/.ssh/YOUR_VAULT_DEPLOY_KEY
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
VAULT_REPO_URL=git@github.com:EvaDugina/UchoVault.git ./deploy.sh
```

Если на сервере используется SSH-alias из `~/.ssh/config`, можно передать его
вместо прямого GitHub URL, например `git@github-ucho-vault:EvaDugina/UchoVault.git`.

Если vault пока не готов, можно запустить без `VAULT_REPO_URL`; скрипт создаст
локальную папку `/srv/psycho/vault`, но для нормальной серверной синхронизации
remote всё равно нужно добавить позже.

```bash
./deploy.sh
```

Что делает `deploy.sh`:

- ставит `git`, `openssh-client`, Docker Engine и Docker Compose plugin;
- создаёт `/srv/psycho/app` и `/srv/psycho/vault`;
- клонирует код бота из `https://github.com/EvaDugina/Ucho.git`;
- при наличии `VAULT_REPO_URL` клонирует/обновляет vault;
- копирует `.env` из папки запуска, если он лежит рядом со скриптом;
- проверяет, что заполнены `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID` и один
  LLM-ключ: `OPENROUTER_API_KEY` или `AITUNNEL_API_KEY`;
- если задан `VAULT_GIT_SSH_KEY_HOST_PATH`, монтирует только этот deploy key
  read-only в контейнер, использует его для host-side pull/clone vault и для
  push vault-коммитов из контейнера;
- запускает smoke-тесты;
- пересобирает и поднимает контейнер `psycho-bot`.

Сборка по умолчанию использует `mirror.gcr.io/library/python:3.12-slim` как
публичное зеркало официального Python-образа. Это нужно, чтобы свежий VPS не
падал на anonymous pull limit Docker Hub. Если на сервере сделан `docker login`
и нужен именно Docker Hub, можно переопределить образ:

```bash
PYTHON_BASE_IMAGE=python:3.12-slim VAULT_REPO_URL=git@github.com:EvaDugina/UchoVault.git ./deploy.sh
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
tail -n 100 .logs/bot.log
```

`docker compose logs` остаётся основным быстрым просмотром stdout/stderr. Тот
же app-log бот дополнительно пишет в `/srv/psycho/app/.logs/bot.log` с ротацией
по умолчанию `10 MB x 5`. Не выводи `.env` в терминал для диагностики: в логах
должны быть только runtime-сообщения, без секретов.

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
bash /srv/psycho/app/deploy/update.sh
```

Он делает:

- `git pull --ff-only` кода бота;
- `git fetch --all --prune` + `git pull --ff-only` vault, если путь из
  `VAULT_HOST_PATH` в `.env` является git checkout;
- если задано `SKIP_VAULT_PULL=1`, пропускает pull vault и обновляет только код
  бота/контейнер;
- перед любым build проверяет, что `.env` содержит `TELEGRAM_BOT_TOKEN`,
  `OWNER_TELEGRAM_ID`, `VAULT_HOST_PATH`, один LLM-ключ и существующий
  `VAULT_GIT_SSH_KEY_HOST_PATH`, если он задан;
- при непустом `VAULT_GIT_SSH_KEY_HOST_PATH` host-side pull vault тоже идёт
  через `GIT_SSH_COMMAND` с этим deploy key;
- при непустом `VAULT_GIT_SSH_KEY_HOST_PATH` запускает compose вместе с
  `docker-compose.ssh.yml`, чтобы контейнер видел deploy key как
  `/run/secrets/vault_git_ssh_key`;
- smoke-тесты;
- `docker compose up -d --build bot`.

### Push vault по SSH

На сервере remote у vault должен быть SSH-URL:

```bash
cd /srv/psycho/app
VAULT_DIR="$(grep -E '^VAULT_HOST_PATH=' .env | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
git -C "$VAULT_DIR" remote -v
git -C "$VAULT_DIR" remote set-url origin git@github.com:EvaDugina/UchoVault.git
```

В `.env` укажи путь к deploy key на хосте, не содержимое ключа:

```env
VAULT_GIT_USER_NAME=Psycho Bot
VAULT_GIT_USER_EMAIL=psycho-bot@local
VAULT_GIT_SSH_KEY_HOST_PATH=/root/.ssh/YOUR_VAULT_DEPLOY_KEY
```

Ключ должен быть добавлен в git-хостинг с write-доступом к vault-репозиторию.
Проверка host-side pull до Docker:

```bash
cd /srv/psycho/app
VAULT_DIR="$(grep -E '^VAULT_HOST_PATH=' .env | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
GIT_SSH_COMMAND="ssh -i /root/.ssh/YOUR_VAULT_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" git -C "$VAULT_DIR" fetch --dry-run origin
```

Быстрая проверка container-side identity/push после обновления:

```bash
cd /srv/psycho/app
docker compose -f docker-compose.yml -f docker-compose.ssh.yml run --rm --no-deps bot git -C /vault config --get user.email
docker compose -f docker-compose.yml -f docker-compose.ssh.yml run --rm --no-deps bot git -C /vault push --dry-run
```

Если нужно срочно обновиться без smoke-тестов:

```bash
SKIP_TESTS=1 bash /srv/psycho/app/deploy/update.sh
```

Если pull vault зависает из-за SSH/host-key/passphrase, можно срочно обновить
только код бота и контейнер:

```bash
SKIP_VAULT_PULL=1 bash /srv/psycho/app/deploy/update.sh
```

Если зеркало Docker Official Images недоступно, можно временно вернуться к
Docker Hub после авторизации:

```bash
PYTHON_BASE_IMAGE=python:3.12-slim /srv/psycho/app/deploy/update.sh
```

Если `curl` с хоста ходит наружу через proxy, а Docker build падает на timeout
к registry/mirror, настрой proxy для Docker daemon отдельно. Значения proxy не
публикуй в чат и не коммить:

```bash
PROXY_URL="${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}"
[ -n "$PROXY_URL" ] || { echo "No proxy env found in current shell"; exit 1; }
mkdir -p /etc/systemd/system/docker.service.d
cat >/etc/systemd/system/docker.service.d/proxy.conf <<EOF
[Service]
Environment="HTTP_PROXY=$PROXY_URL"
Environment="HTTPS_PROXY=$PROXY_URL"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
EOF
systemctl daemon-reload
systemctl restart docker
```

Если Telegram polling из контейнера падает на timeout, но хостовый `curl
https://api.telegram.org` проходит через proxy, добавь тот же proxy в
`/srv/psycho/app/.env` как `TELEGRAM_PROXY_URL=...` и пересобери контейнер.

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
tail -f .logs/bot.log
docker compose restart bot
docker compose down
docker compose up -d --build bot
```

Vault:

```bash
git -C /srv/psycho/vault status
GIT_SSH_COMMAND="ssh -i /root/.ssh/YOUR_VAULT_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" git -C /srv/psycho/vault pull --ff-only
GIT_SSH_COMMAND="ssh -i /root/.ssh/YOUR_VAULT_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" git -C /srv/psycho/vault push
```

Перед переносом live-режима на сервер останови локальный контейнер с тем же
Telegram token, иначе два poller'а будут мешать друг другу.
