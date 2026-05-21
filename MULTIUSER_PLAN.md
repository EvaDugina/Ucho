# План: multi-user с изоляцией данных (POC B + доверенные)

> Рабочий планировочный документ. После реализации — сложить в
> `.docs/technical.md → ## notes → ### Active plans` и удалить отсюда
> (не захламляем корень, правило brunelleschi).

## Контекст и стадия

Запрос: добавлять пользователей по `user_id`, у каждого — своя изолированная
база знаний; бот безопасный/надёжный/отказоустойчивый.

Контроль сложности: multi-user формально — обязательство **MVP A** (в текущем
`product.md` он записан в out-of-scope как «никогда»). **Решение пользователя:**
остаёмся **POC B по духу** — берём только изоляцию данных + whitelist нескольких
доверенных id, **без** полного MVP-A hardening (rate limiting, бэкапы по cron,
структурные логи, дашборд). Это «**сверх POC B**» — зафиксировать в
`technical.md → notes` как сознательно ограниченный объём.

Аудитория: несколько доверенных (друзья), согласие устное.
Раскладка: **все в `users/<id>/`** (включая владельца → нужна миграция).
Подход к прокидыванию пути: **вариант A — contextvars** (request-scoped корень).

---

## 1. Раскладка вольта и миграция

Новая раскладка (каждый пользователь — полная копия структуры):
```
<vault>/
├─ users/
│  └─ <user_id>/
│     ├─ concepts/<domain>/<slug>.md
│     ├─ raw/<YYYY-MM-DD>.md
│     ├─ profile/<domain>.md
│     ├─ notes/<YYYY-MM-DD>.md
│     ├─ .psycho/{manifest.json, log.md, startup-check.md}
│     ├─ _index.md
│     ├─ _state.json
│     └─ _session.json
├─ .psycho/users.json        # реестр whitelist (общий, см. §3)
├─ .gitignore
└─ .git/                     # ОДИН git-репо на весь вольт (общий safety net)
```

- Один git-репозиторий на весь вольт (история и `git_wrap` общие).
- Drift-manifest — **per-user** внутри `users/<id>/.psycho/manifest.json`.
- digests/ — тоже переезжает в `users/<id>/digests/` (это персональный обзор).

### Уточнение раскладки (снижение churn)
`.psycho/` (manifest, log, startup-check, users.json) и `.git/` — **глобальные**
на корне вольта (ключи манифеста — относительные пути, уже покрывают
`users/<id>/...`; один git-репо = общий safety net). **Per-user** переезжают
только данные: `concepts/`, `raw/`, `profile/`, `notes/`, `digests/`,
`_index.md`, `_state.json`, `_session.json`.

### Миграция владельца
`scripts/migrate_to_multiuser.py` — одноразовый, двухфазный (как
`migrate_domains.py`):
- **dry-run** (без аргументов): печатает план переноса корневых
  `concepts/raw/profile/notes/digests/_index.md/_state.json/_session.json`
  → `users/<OWNER_ID>/...`, ничего не двигает. `.psycho/` и `.git/` НЕ трогает.
- **`--apply`**: внутри `git_wrap("migrate_to_multiuser")` через `git mv`
  переносит файлы; raw-ref-ы остаются относительными внутри user-root.
- Запуск только в Docker: `docker compose run --rm bot python scripts/migrate_to_multiuser.py [--apply]`.
- После apply удалить скрипт (одноразовый).

### Проверка успешности миграции (post-apply)
Скрипт после `--apply` сам прогоняет верификацию и печатает PASS/FAIL:
1. **Целевые файлы на месте:** `users/<OWNER_ID>/concepts|raw|profile|...`
   существуют и непусты там, где в источнике было непусто.
2. **Источник пуст:** в корне не осталось перенесённых `concepts/raw/profile/
   notes/digests/_index.md/_state.json/_session.json`.
3. **Счёт совпадает:** число `.md` в `concepts/**` до и после равно (ничего не
   потеряно/не задвоено).
4. **Парсится:** `graph.find_concepts()` под `set_user(OWNER)` возвращает столько
   же концептов, сколько было до миграции.
5. **Git чист:** `git status --porcelain` после коммита миграции пуст (всё
   закоммичено, нет потерянных untracked).
6. **Бот видит данные:** `_state.json`/`_session.json` (если были) читаются под
   user-root владельца.
   Любой FAIL → `git reset --hard` на pre-коммит (откат через `git_wrap`),
   сообщение что миграция отменена.

---

## 2. Per-user корень через contextvars (вариант A)

Сейчас пути — модульные константы (`RAW_DIR = VAULT_PATH / "raw"` и т.п.).
Делаем их **функциями**, читающими request-scoped contextvar.

### `bot/userctx.py` (новый)
```python
import contextvars
from pathlib import Path
from .config import VAULT_PATH

_current_user_root: contextvars.ContextVar[Path] = contextvars.ContextVar(
    "current_user_root", default=VAULT_PATH  # фолбэк — корень (для тестов/совместимости)
)

def set_user_root(user_id: int) -> Path:
    root = VAULT_PATH / "users" / str(user_id)
    _current_user_root.set(root)
    return root

def user_root() -> Path:
    return _current_user_root.get()
```
contextvars **async-безопасны**: каждый aiogram-хэндлер исполняется отдельной
задачей, значение изолировано per-task. Никакой утечки между одновременными
пользователями.

### Превратить константы в функции
- `bot/vault.py`: `RAW_DIR/PROFILE_DIR/NOTES_DIR/INDEX_FILE/STATE_FILE` →
  `raw_dir()/profile_dir()/...` от `userctx.user_root()`. `.psycho` пути тоже.
- `bot/graph.py`: `CONCEPTS_DIR` → `concepts_dir()`.
- `bot/moc.py`, `bot/selfcheck.py`: используют те же функции.
- `bot/config.py`: `PSYCHO_META_DIR/MANIFEST_PATH/LOG_PATH` — сделать функциями
  от user_root (или перенести в vault.py как `meta_dir()` и т.д.).
- `bot/manifest.py`: путь манифеста = `user_root()/.psycho/manifest.json`.

Диф механический, но широкий (каждое использование константы → вызов функции).

### Где обязательно выставить contextvar
- **Каждый Telegram-хэндлер/callback** — в самом начале, после проверки
  whitelist: `userctx.set_user_root(message.from_user.id)`. Чище — сделать
  middleware aiogram, который ставит root на каждый update (один раз, для всех
  хэндлеров).
- **Daily-тикер** (`scheduler` → `send_daily_question`) — цикл по allowed
  пользователям, перед каждым `set_user_root(uid)`.
- **Startup self-check** — цикл по `users/<id>/`, `set_user_root` на каждого.
- **pending recovery** — по каждому пользователю с активной сессией.
- **weekly-review** (Claude) — вне процесса, см. §5.

**Рекомендация:** aiogram middleware (`BaseMiddleware`) — единая точка, ставит
root + проверяет whitelist до любого хэндлера. Меньше шансов забыть.

---

## 3. Whitelist + роли

- `.env`: `OWNER_TELEGRAM_ID` (админ, как сейчас) + опц. `ALLOWED_TELEGRAM_IDS`
  (через запятую, начальный список).
- Рантайм-реестр: `<vault>/.psycho/users.json` —
  `{"users": [{"id": 123, "added": "2026-05-21", "by": <owner>}], ...}`.
  Позволяет добавлять без правки `.env` и рестарта.
- `_is_owner(uid)` остаётся; добавляется `_is_allowed(uid)` =
  `uid == OWNER or uid in registry`.
- Middleware: не-allowed → молчание (как сейчас не-owner).
- Админ-команды (гейт `_is_owner`):
  - `/adduser <id>` — добавить в реестр (+ `set_user_root` создаст структуру при
    первом обращении).
  - `/removeuser <id>` — убрать из реестра (данные НЕ удаляем — бот не удаляет).
  - `/users` — список.
- Подсказки команд (`set_my_commands`) — для каждого allowed через
  `BotCommandScopeChat`. Админ-команды показываем только владельцу.

---

## 4. Сессии / состояние / тикер / recovery — per-user

- `bot/session.py`: глобальный `_active: Optional[Session]` →
  `_active: dict[int, Session]` (ключ — user_id). API (`get/start/clear/
  set_question/persist`) принимает/получает user_id из contextvar.
  Персист — `users/<id>/_session.json`.
- `_state.json` (счётчик Q) — per-user (через `state_file()`).
- `pending_answer` — в per-user сессии (уже поле Session, просто per-user файл).
- Daily-тикер — рассылка всем allowed (у кого нет активной сессии).
- Startup self-check — по всем `users/<id>/`.

---

## 5. weekly-review (Claude, вручную)

- SKILL.md: добавить аргумент «для какого пользователя» — работает в
  `<vault>/users/<id>/` вместо корня. По умолчанию — владелец.
- `digest-template.md` и пути — относительно user-root.
- Безопасность та же: checkpoint-коммит до/после, drift-detection.

---

## 6. Приватность (дёшево, снимает этический долг)

- При первом обращении нового пользователя — disclaimer-онбординг: «бот строит
  твой психо-портрет в локальной базе владельца; данные не уходят в облако;
  продолжая, ты соглашаешься». Одно сообщение + флаг `consent` в `users.json`.
- Изоляция = свойство безопасности: один пользователь не видит базу другого
  (физически разные папки + contextvar-маршрутизация).

---

## 7. Что СОЗНАТЕЛЬНО НЕ берём (deferred, «сверх POC B»)

Зафиксировать в `technical.md → notes → active plans` как отложенное до MVP A:
- Rate limiting на пользователя.
- Структурные JSON-логи (остаётся текущий stderr + `.psycho/log.md`).
- Бэкапы по cron с ротацией и тестом восстановления (полагаемся на git внутри
  вольта + YandexDisk-history).
- Мини-дашборд «кто сколько пользуется».
- 152-ФЗ-режим (только если выйдем за круг доверенных → MVP B).

---

## 8. Порядок реализации

1. `bot/userctx.py` + middleware (whitelist + set_user_root).
2. Превратить пути-константы в функции (vault/graph/moc/selfcheck/config/manifest).
3. `session.py` → per-user dict.
4. `scripts/migrate_to_multiuser.py` (dry-run + apply), прогнать на текущем вольте.
5. Whitelist-реестр `users.json` + `/adduser`/`/removeuser`/`/users` + consent-онбординг.
6. Тикер/self-check/recovery — циклы по пользователям.
7. weekly-review SKILL.md — per-user.
8. Пересборка, smoke: владелец работает как раньше (после миграции),
   второй id получает свою пустую базу, данные не пересекаются.
9. Обновить `.docs/*`: audience (роли owner/гость), architecture (per-user root,
   contextvars, users/<id>/), rules → stage constraints (взято / deferred),
   notes. Сложить этот план в notes и удалить `MULTIUSER_PLAN.md`.

## Проверка (acceptance)

- После миграции: владелец — `/ask`, ответ пишется в `users/<owner>/concepts/...`,
  старые данные на месте.
- Второй allowed id: `/start`/`/ask` — создаётся `users/<id2>/`, его концепты
  отдельно; владелец не видит их, второй не видит владельца.
- Не-allowed id — молчание.
- Одновременные сообщения двух пользователей не путают сессии (contextvar
  per-task) и не пишут в чужой root.
- `/adduser`/`/removeuser` — только владелец; реестр персистится.
- git-история вольта цела; drift-detection per-user работает.
