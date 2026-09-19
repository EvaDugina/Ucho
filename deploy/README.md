# Развёртывание «Уха» на Ubuntu

Бот работает через Telegram polling. Данные остаются обычными файлами на хосте;
Git используется только для обновления кода приложения. Для личных данных и
книг Git-репозиторий не нужен.

## Первый запуск

Разместите `deploy/` на сервере и запустите `./deploy.sh`. По умолчанию скрипт
использует:

```text
/srv/ucho/app      код бота
/srv/ucho/vault    рабочие данные и папка для Obsidian
/srv/ucho/backups  снимки данных
```

Скрипт устанавливает Docker при необходимости, клонирует код и создаёт
`app/.env` из примера, если файла ещё нет. В этом случае он остановится:
заполните `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID` и один LLM-ключ
(`OPENROUTER_API_KEY` или `AITUNNEL_API_KEY`), затем запустите `./deploy.sh` снова.
Пути `VAULT_HOST_PATH` и `BACKUP_HOST_PATH` скрипт выставляет автоматически,
если они пусты.

Если старые данные уже существуют, сначала остановите бот и поместите их в
каталог из `VAULT_HOST_PATH`. Перед первым запуском с новым кодом сохраните
отдельную копию этой папки. Скрипт не клонирует и не обновляет репозиторий
пользовательских данных.

Для прежней установки в `/srv/psycho` задайте `BASE_DIR=/srv/psycho` при
запуске скрипта обновления; без этого новый default указывает на `/srv/ucho`.

## Обновление и остановка

```bash
bash /srv/ucho/app/deploy/update.sh
bash /srv/ucho/app/deploy/stop.sh
```

`update.sh` делает `git pull --ff-only` только для кода, проверяет `.env`,
запускает smoke-тесты в Docker и пересобирает контейнер. `SKIP_TESTS=1`
допускается для срочного обновления. Для старого пути передайте `BASE_DIR`.

## Резервные копии

При старте без снимка за текущую неделю и раз в неделю в `BACKUP_WEEKDAY` и
`BACKUP_HOUR` бот создаёт каталог `backup-...` внутри `BACKUP_HOST_PATH`.
По умолчанию это понедельник в 03:00 по `DAILY_TZ`, хранение — не более четырёх
готовых снимков всей папки. Каждый содержит `data/` и
`manifest.json` с SHA-256. Старые снимки удаляются после успешной новой копии.
`.git`, `.obsidian`, ключи и незавершённые `.tmp` в копию не входят.

Проверка выбранного снимка:

```bash
cd /srv/ucho/app
docker compose run --rm bot python -m bot.backup verify "/backups/backup-ИМЯ"
```

Восстановление выполняйте при остановленном боте в **новую** папку. Команда
`restore` проверяет манифест и откажется перезаписать существующий каталог:

```bash
docker compose stop bot
docker compose run --rm -v "/srv/ucho:/restore-parent" bot \
  python -m bot.backup restore "/backups/backup-ИМЯ" /restore-parent/ucho-restored
```

После сверки укажите `/srv/ucho/ucho-restored` как `VAULT_HOST_PATH` в закрытом
`.env` и запустите `docker compose up -d bot`. Эти копии лежат на том же сервере;
копирование на другой хост настраивается отдельно.

## Проверка работы

```bash
cd /srv/ucho/app
docker compose ps bot
docker compose logs --tail=100 bot
```

В Telegram команда `/pebble` отвечает `Бот работает.`. При изменениях
`bot/`, `prompts/`, `scripts/` или `tests/` пересоберите image.
