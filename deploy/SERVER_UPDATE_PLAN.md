# План обновления сервера, если `update.sh` отсутствует

Документ описывает безопасный ручной порядок для сервера `72.56.235.190`.
Секреты из `.env` не выводить в терминал и не копировать в чат.

## 1. Подключиться и найти приложение

```bash
ssh root@72.56.235.190
```

Проверить стандартный путь:

```bash
test -d /srv/psycho/app/.git && echo "app repo ok" || echo "app repo missing"
test -f /srv/psycho/app/docker-compose.yml && echo "compose ok" || echo "compose missing"
```

Если `/srv/psycho/app` отсутствует, остановиться и сначала понять, где реально
лежит рабочая копия. Не создавать второй экземпляр бота с тем же Telegram token.

## 2. Подтянуть код без `update.sh`

```bash
cd /srv/psycho/app
git status --short --branch
git log -1 --oneline
```

Если `git status` показывает локальные изменения, не делать `reset --hard`.
Сначала сохранить вывод и разобрать, что изменено.

Если рабочая копия чистая:

```bash
git fetch origin main
git pull --ff-only origin main
git log -3 --oneline
```

Ожидаемо после pull в репозитории должен появиться файл:

```bash
test -f deploy/update.sh && echo "update.sh present"
```

## 3. Проверить и запустить новый `update.sh`

```bash
cd /srv/psycho/app
chmod +x deploy/update.sh
bash -n deploy/update.sh
./deploy/update.sh
```

`update.sh` должен выполнить:

- `git pull --ff-only` кода;
- обновление vault, если путь из `VAULT_HOST_PATH` является git checkout;
- smoke-тесты в Docker;
- пересборку и запуск `docker compose up -d --build bot`;
- вывод статуса и последних логов контейнера.

Не использовать `SKIP_TESTS=1`, кроме аварийного случая.

## 4. Ручной fallback, если `update.sh` всё ещё не появился

Выполнять только если `git pull` прошёл, но `deploy/update.sh` всё равно
отсутствует.

```bash
cd /srv/psycho/app

VAULT_DIR="$(grep -E '^VAULT_HOST_PATH=' .env | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
if [ -n "$VAULT_DIR" ] && [ -d "$VAULT_DIR/.git" ]; then
  git -C "$VAULT_DIR" fetch --all --prune
  git -C "$VAULT_DIR" pull --ff-only
fi

export PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-mirror.gcr.io/library/python:3.12-slim}"
docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke
docker compose up -d --build bot
docker compose ps bot
docker compose logs --tail=120 bot
```

Если smoke-тесты падают, остановиться и смотреть ошибку. Не переподнимать бота
вслепую поверх красных тестов.

## 5. Проверить результат

```bash
cd /srv/psycho/app
git status --short --branch
git log -3 --oneline
docker compose ps bot
docker compose logs --tail=120 bot
```

Ожидаемо:

- ветка без незакоммиченных изменений;
- `psycho-bot` в состоянии `Up`;
- в логах нет traceback и циклических рестартов;
- в Telegram команда `/pebble` отвечает `Больно.`

## 6. Если контейнер не стартует

```bash
cd /srv/psycho/app
docker compose config --quiet
docker compose ps bot
docker compose logs --tail=200 bot
```

Если проблема в `.env`, проверять только наличие нужных ключей, не печатая их
значения:

```bash
grep -E '^(TELEGRAM_BOT_TOKEN|OWNER_TELEGRAM_ID|OPENROUTER_API_KEY|AITUNNEL_API_KEY|VAULT_HOST_PATH|VAULT_PATH)=' .env | cut -d= -f1
```

