---
name: weekly-review
description: >-
  Еженедельная ревизия и СБОРКА базы знаний Psycho (Obsidian-вольт психо-
  философского портрета). Локальный бот (Qwen 14B) только захватывает черновики;
  выверенный граф строит этот скилл сильной моделью: промоушн черновиков,
  дедуп/слияние, связи, реальные противоречия, переписывание profile, MOC,
  weekly digest. Запускать вручную раз в неделю. Триггеры: «weekly review»,
  «ревизия базы», «собери граф», «обзор недели», «/weekly-review».
---

# Weekly Review — сборка базы знаний Psycho

Ты — сильная модель. Архитектура проекта построена на разделении труда:

- **Бот (Qwen 14B, live)** только *захватывает*: ведёт диалог, пишет `raw/`,
  создаёт **черновые** концепты `status: draft` — БЕЗ связей и БЕЗ противоречий.
  Он намеренно не строит граф: он в этом слаб (плодит дубли и ложные конфликты).
- **Ты (раз в неделю)** *строишь* выверенный граф из черновиков и сырья:
  промоушн `draft → stable`, дедуп/слияние, связи, реальные противоречия,
  переписывание `profile/`, осмысленный MOC, и наглядный digest.

## Что тебе можно менять, а что нельзя

**Можно (это твоя зона):**
- `concepts/<domain>/<slug>.md` — промоушн, слияние, связи, контр-callouts, статусы.
- `profile/<domain>.md` — переписывать из свалки цитат в связный обзор.
- `concepts/<domain>/_moc.md` — пересобирать по смыслу.
- `digests/<YYYY-WNN>.md` + `digests/_index.md` — создавать.

**Нельзя (территория бота / источники истины):**
- `raw/**` — сырой лог, неприкосновенен (источник правды для evidence).
- `_session.json`, `_state.json`, `.psycho/manifest.json`, `_index.md` — служебное бота.

**Безопасность:** вольт — git-репозиторий. **Перед любыми правками** сделай
checkpoint-коммит, **после** — финальный коммит (см. Фазу 2). Бот защищён
drift-detection: твои правки он потом не перетрёт (увидит изменённый mtime →
пропустит запись, залогирует `drift_skipped`).

## Где вольт

Путь из `VAULT_HOST_PATH` в `<project>/.env` (дефолт
`C:/Users/eva/YandexDisk/Obsidian/Psycho`). Дальше `<vault>` = он.

## Структура и форматы

```
<vault>/
├─ raw/<YYYY-MM-DD>.md           # Q&A дня: «## Q<N> · HH:MM · <domain>», **Q:** / **A:**, ^Q<N>
├─ concepts/<domain>/<slug>.md   # узлы (кроме _*.md). Бот пишет draft, ты доводишь до stable
├─ profile/<domain>.md           # пока свалка цитат — переписываешь в обзор
├─ .psycho/log.md                # операционный лог бота
└─ digests/                      # твои обзоры
```

10 доменов: `ethics, aesthetics, politics, everyday, relationships, identity,
mortality, nationality, knowledge, work`.

Формат концепт-ноты:
```markdown
---
type: principle|value|preference|belief|claim
domain: <один из 10>
slug: <kebab-ascii>
status: draft|tentative|stable|contested
supports: ["[[slug]]"]
contradicts: ["[[slug]]"]
derived_from: ["[[slug]]"]
related: ["[[slug]]"]
aliases: [альтернативные формулировки]
---
# Имя концепта
<summary 1–3 предложения от третьего лица>
## Подтверждения
- <дата> — «<цитата>» — из [[raw/<date>#^Q<N>]]
## Открытые вопросы
- vs [[slug]]: <probe>
```
`status: draft` = создан ботом, не выверен. Твоя работа — довести до `stable`.
Контр-противоречие — Obsidian-callout `> [!contradiction] vs [[slug]]` в ОБА концепта,
плюс симметричная связь `contradicts` во frontmatter обоих.

---

## Процедура: две фазы (proposal → apply)

### Фаза 0 — окно и сбор
1. ISO-неделя: `date +%G-W%V` → `YYYY-WNN`. Окно — последние 7 дней.
2. Прочитай: `raw/` за неделю; ВСЕ `concepts/*/*.md` (кроме `_*.md`) с frontmatter+телом, выдели `status: draft`; `profile/*.md`; `.psycho/log.md`.

### Фаза 1 — PROPOSAL (ничего не пишем в граф)
Проанализируй и составь **план сборки**. Покажи его пользователю в чате (кратко) и запиши в `<vault>/.psycho/weekly-proposal-<YYYY-WNN>.md`. План включает:

1. **Промоушн черновиков.** Для каждого `draft`-концепта реши:
   - **merge** в существующий (дубль/почти-дубль) — указать в какой, перенести evidence + alias;
   - **promote** как новый `stable` (уточнить slug/name/summary/type, если кривые);
   - **drop** как шум (объяснить почему).
2. **Связи.** Какие `supports / derived_from / related` проставить между концептами (с обоснованием в одну строку).
3. **Реальные противоречия.** Только настоящие (не формальное противопоставление слов). **Фильтр:** если в `raw` пользователь явно отверг рамку («не противоречие», «никак не объясняю») — НЕ ставить, отметить как снятое.
4. **Переписать profile/<domain>.md** — для доменов, где profile = свалка цитат: в связный обзор с кластерами и ссылками `[[slug]]`.
5. **MOC** — какие домены пересобрать по смыслу.

Спроси: **«Применить план? да / нет / правки»**. Без «да» — стоп (proposal остаётся для просмотра в Obsidian).

### Фаза 2 — APPLY (по подтверждению)
1. **Checkpoint:** `git -C <vault> add -A && git -C <vault> commit -m "weekly-review <YYYY-WNN>: before apply" --allow-empty`.
2. Применяй план по порядку:
   - merge: перенести evidence/aliases в целевой концепт, обновить summary, удалить лишний файл, **переписать все `[[old-slug]]` → `[[new-slug]]`** в других концептах и profile.
   - promote: выставить `status: stable`, причесать summary/name/slug.
   - связи: добавить симметрично во frontmatter обоих (`supports` A→B и B→A; `derived_from` A→B и `related` B→A).
   - противоречия: `contradicts` в обоих + `> [!contradiction] vs [[…]]` callout в обоих.
   - profile: перезаписать `profile/<domain>.md`.
   - MOC: пересобрать `concepts/<domain>/_moc.md` (можно вызвать `docker compose run --rm bot python -c "from bot import moc; moc.rebuild_domain_moc('<domain>')"`, либо написать MOC руками со смысловыми кластерами — это богаче, чем by-type).
3. **Digest:** создай `digests/<YYYY-WNN>.md` по `digest-template.md`.
4. **Навигация:** дополни `digests/_index.md` (новый сверху).
5. **Финальный коммит:** `git -C <vault> add -A && git -C <vault> commit -m "weekly-review <YYYY-WNN>: applied"`.

### Проверка перед завершением
- Все `[[slug]]` ведут на реальные файлы (нет битых после merge).
- `raw/`, `_session.json`, `_state.json`, `_index.md` не тронуты (`git diff --name-only` checkpoint..HEAD не содержит их).
- Не осталось `status: draft` среди тех, что ты обработал (либо stable, либо merged/dropped).

---

## Digest

`digests/<YYYY-WNN>.md` — наглядный обзор для человека, по `digest-template.md`
(callouts, `[[wikilinks]]`, frontmatter `type: digest`). Секции: что проступило,
напряжения (с пометкой «снято пользователем» где надо), что собрано/слито за
прогон, вопросы-кандидаты на следующую неделю.

## Если запускают «только обзор, без сборки»

Если пользователь просит just digest — выполни Фазу 0 + сразу digest (как
read-only обзор), пропусти Фазы 1–2. По умолчанию же скилл **собирает граф**.
