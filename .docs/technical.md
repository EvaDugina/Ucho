# Technical — Psycho

## brunelleschi_stage

- **Стадия:** POC B
- **Последнее обновление:** 2026-05-26

---

## technology

**Стек:**

- **Язык / фреймворк:** Python 3.12 + aiogram (Telegram), APScheduler (daily-тикер)
- **СУБД:** не используется (граф в Markdown-файлах внутри Obsidian-vault)
- **Очередь / брокер:** не используется
- **AI-провайдер:** AITunnel через openai-совместимый API. Стартовая live-модель `qwen3-235b-a22b-2507`; compose/example fallback `deepseek-v4-flash`. Локального LLM runtime в проекте больше нет.
- **Прочее:** PyYAML (парсинг frontmatter), python-dotenv, git CLI (как safety net и единственный механизм синхронизации vault между окружениями), `pymorphy3` (+`pymorphy3-dicts-ru`) для лемматизации русского ввода под VAD-лексикон NRC-VAD (`bot/data/nrc_vad_ru.tsv`, вшит в образ; собирается `scripts/build_lexicon.py`).

**Переменные окружения:**

| Переменная | Назначение | Пример |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather | `123:abc…` |
| `OWNER_TELEGRAM_ID` | владелец/админ (всегда разрешён) | `123456789` |
| `ALLOWED_TELEGRAM_IDS` | доп. доверенные id через запятую (начальный список; рантайм — в `.psycho/users.json`) | `111,222` |
| `VAULT_HOST_PATH` | абсолютный путь к Obsidian-vault на хосте/сервере; пробрасывается в контейнер как `/vault` | `/srv/psycho/vault` |
| `VAULT_PATH` | путь внутри контейнера (можно переопределить для тестов) | `/vault` |
| `AITUNNEL_BASE_URL` | URL AITunnel API; compose задаёт дефолт | `https://api.aitunnel.ru/v1` |
| `AITUNNEL_API_KEY` | AITunnel API key | `ait-...` |
| `LLM_MODEL_DEFAULT` | стартовая live-модель | `qwen3-235b-a22b-2507` |
| `LLM_MODEL_FALLBACKS` | fallback-модели через запятую | `deepseek-v4-flash` |
| `LLM_TIMEOUT` | таймаут одного LLM-вызова, сек (без него sdk ждёт ~600 c) | `90` |
| `LLM_COOLDOWN_SEC` | мин. интервал между LLM-операциями одного пользователя (anti-DoS) | `4` |
| `ANALYSIS_ENABLED` | мульти-методный разбор ответа для OWNER: отчёт в `01_mood/analysis/` + durable-ряд `01_mood/timeseries/`; `false` → только базовый разбор настроения в чат | `true` |
| `DAILY_HOUR` | час суток для авто-вопроса (в поясе `DAILY_TZ`) | `19` |
| `DAILY_TZ` | пояс расписания авто-вопроса | `Europe/Moscow` |

**Поведение `DEBUG`:**

Сейчас флага `DEBUG` нет — single-user проект, prod-конфигурация и есть «как запускаем». Если будем выкладывать на shared сервер, добавим `DEBUG` для разделения hardening (см. notes → active plans).

---

## architecture

- **Подход:** монолит, асинхронный (aiogram + asyncio + openai async client). В обычном запуске поднимается один процесс бота; LLM-вызовы уходят в AITunnel.
- **Компоненты:**
  - Telegram-бот (`bot/`) — диалоговый слой, маршрутизация команд, форматирование сообщений.
  - AITunnel — внешний live-LLM-провайдер.
  - Obsidian-vault на сервере/хосте — хранилище графа и raw-логов; синхронизация и перенос между окружениями идут только через git.
- **Разделение труда (capture-first):** AITunnel live-модель только захватывает — диалог, `00_raw/`, **черновые** концепты `status: draft` без связей/конфликтов и слабый `mask_frequency_draft`. Выверенные документы строит сильная модель вручную двумя скиллами (proposal → apply под git): `.agents/skills/reconcista/` — граф знаний (промоушн `draft → stable`, дедуп/слияние, связи, реальные противоречия, `02_profile/`, MOC, теги, digest); `.agents/skills/depersonalization/` — портрет (`03_personality/about.md`), настроение (`03_personality/mood.md`), curated `03_personality/mask_frequencies.json`, психометрика (`03_personality/profile.md`), soft skills (`03_personality/softskills.md`), граф `01_mood/`, `03_personality/user_prompt.md`.
- **Multi-user изоляция:** бот обслуживает несколько доверенных пользователей; у каждого — своя база в `<vault>/users/<user_id>/` (`00_raw`, `01_mood`, `02_concepts`, `02_profile`, `02_digest`, `03_personality`, `_index`, `_state`, `_session`). `.psycho/` (manifest, log, startup-check, users.json) и `.git/` — глобальные на корне (один safety net, ключи манифеста — относительные пути). Текущий пользователь — request-scoped через `userctx` (contextvar, async-безопасно): aiogram-middleware ставит его на каждый update; data-слой (vault/graph/moc) маршрутизирует пути по `userctx.user_root()`. Whitelist + роли — `bot/users.py` (`OWNER_TELEGRAM_ID` = админ, гости — env + `.psycho/users.json`). Гостю при первом обращении — disclaimer о приватности.
- **Потоки данных:**
  - Пользователь шлёт сообщение → `00_raw/sessions` фиксирует событие до LLM → `01_mood` анализирует тон → LLM (через AITunnel) возвращает JSON (`observations` — только анализ) → код пишет в vault: `00_raw/qna`, `02_concepts`, `02_profile`, `03_personality/deltas`; slug из имени, дедуп решает create/update → атомарная запись через `git_wrap` → MOC rebuild.
  - Non-text Telegram-сообщения (файлы, фото, голосовые, caption-only) не доходят до handlers/LLM и не читаются как вложения: middleware отвечает короткой миниатюрой на тему «бедное ухо без глаз» и останавливает обработку.
  - APScheduler раз в день → `send_daily_question` → handler в обход Telegram-входа. Дедуп по дате (`vault.daily_already_sent`/`mark_daily_sent`, поле `last_daily_date` в `_state.json`) — один дневной на пользователя в день, общий для cron / `/dailyall` / догона. При старте `scheduler.catch_up_daily` досылает сегодняшний дневной, если бот лежал в час рассылки (за прошлые дни — нет). Активная сессия/прошлые ответы не блокируют дневной.
  - При старте контейнера → `selfcheck.run()` (механический, без LLM): MOC rebuild всех доменов + валидация связей + `.psycho/startup-check.md`. Затем `session.restore_all()` восстанавливает активные `_session.json`, а `process_pending_on_startup` дожимает pending-события по `pending_answer_event_id` из `00_raw/sessions`. Офлайн-сообщения доезжают из очереди Telegram — polling не выставляет `drop_pending_updates`.
- **Внешние зависимости:** Telegram Bot API + AITunnel API. Секреты только в `.env`.

**Модули и ответственность:**

- `bot/main.py` — точка входа, регистрация router + scheduler, восстановление сессии.
- `bot/config.py` — env-переменные, `DOMAINS`, пути `VAULT_PATH / MANIFEST_PATH / LOG_PATH`.
- `bot/handlers.py` — Telegram transport: routers, команды, callback parsing, Telegram send/reply. Сценарии conversation/note/daily вынесены в `bot/services/*`; `handlers.py` больше не держит основную бизнес-логику записи графа. Команды (кроме `/pebble`, `/regen`, `/like`, `/remask`) закрывают активную сессию в `AccessMiddleware`; `ask/echo/ucho/about/requestion` открывают новую. Reply: `on_text` сперва ищет session по `00_raw/sessions`, иначе реконструирует вопрос из тела reply/сохраняет заметку.
- `bot/services/conversation_service.py` — use case ответа в открытой probe-сессии: обязательная запись user-answer в `00_raw/sessions` до LLM, pending refs, mood/analysis для OWNER, вызов `process_answer`, применение результата, очистка pending, подготовка reaction payload без Telegram-зависимости.
- `bot/services/note_service.py` — use case `/ucho` и fallback-note: обязательная запись verbatim в `00_raw/notes` до LLM, best-effort git commit заметки, затем `process_answer`/`apply_processed`; если LLM упала после сохранения, возвращает `None`, чтобы транспорт молчал.
- `bot/services/daily_service.py` — daily targets, дедуп по дате, отправка дневного вопроса без зависимости от приватных helpers `handlers.py`; `scheduler.py` импортирует этот сервис напрямую.
- `bot/services/session_messages.py` — публичная transport-утилита отправки вопроса/реакции: формат Telegram HTML, `qmap/questions/session` bookkeeping и обязательная запись assistant event в `00_raw/sessions`.
- `bot/services/answer_service.py` — запись `observations` в граф: `00_raw/qna` дословно + домен сессии → `02_profile` → slug=`slugify(name)`, дедуп `resolve_slug`/Jaccard → draft/append evidence → MOC; плюс `about.apply_delta`. Связи/конфликты в live НЕ строит — это reconcista.
- `bot/qmap.py` — совместимая восстановимая обёртка: `append`/`mark_answered` no-op, `find_by_message_id` и `find_by_q_num` читают `00_raw/sessions`. Отдельный `_qmap.json` не создаётся.
- `bot/llm.py` — AITunnel-обёртка openai-клиента с task-aware routing/fallback. Режимы `ask` / `process` + `about_present` (портрет; `iuda.md` + `about.md`) + `classify_mood` (категории настроения `sign/energy/direction/quality/dominance`; принимает лексиконный `vad`-якорь) + `analyze_psych` (OCEAN/PANAS для owner-анализа) + `regenerate_reaction` (только новая реплика в выбранном лице, без записи графа) + `pebble_reply` (короткая fast-реплика на `/pebble`). JSON-вызовы идут с `response_format={"type":"json_object"}`; provider-specific `extra_body.provider` больше не отправляется. Primary по умолчанию: `qwen3-235b-a22b-2507`; compose/example fallback и fast-маршрут — `deepseek-v4-flash`; task-specific env может переопределить маршрут. Если все модели маршрута недоступны, бот пишет предупреждение в `.psycho/log.md` и отправляет человеку понятное сообщение. Системный промпт = `iuda` + `base` + `mood.md` + addendum + `user_prompt` + портрет.
- `bot/moods.py` — настроение и лица. Шкала — **PAD**: размерные valence/arousal/**dominance** (∈[-1..1]) + дискретные `QUALITIES` (~12), `direction`, `stability`. `classify_mood`-результат → `session_mood` (recency-взвешенный вектор по сессии + затухающий prior из `mood_file.baseline()` (v,a,d) + устойчивость через дисперсию), `pick_bot_mood` (контраст + per-user `01_mood/_mood_map.json` + effective-частоты из curated `03_personality/mask_frequencies.json` и draft `03_personality/mask_frequencies_draft.json`; ось dominance — приоритетная: «придавлен→поддержи / властен→осади»), `log_turn` (`01_mood/events/YYYY-MM.jsonl`, с `dominance` + `lex_*`). Curated-частоты пишет только depersonalization, бот пишет только draft. Owner-пайплайн настроения запускает classify/VAD/analysis; для остальных доверенных маска выбирается без owner-аналитики и всё равно хранится в metadata/action context. В список добавлены покорность, жалостливость, боязливость, добрые маски и постирония.
- `bot/lexicon.py` — нативный русский VAD-сигнал (NRC-VAD, русская ветка). `score(text)` лемматизирует токены (`pymorphy3`) и усредняет valence/arousal/dominance по словам из вшитого `bot/data/nrc_vad_ru.tsv`, решейл [0..1]→[-1..1]; `run_in_executor`, LRU на леммы. Инструментальная подсказка арбитру `classify_mood` (не приговор). Нет файла/совпадений/сбой → `None` (пайплайн работает на одном LLM-классификаторе). Заменил связку Argos-перевод→VADER. Лексикон собирается `scripts/build_lexicon.py`.
- `bot/emolex.py` — эмоции по NRC-EmoLex (Плутчик-8 + pos/neg), нативный русский лексикон (`bot/data/nrc_emolex_ru.tsv`, собирается `scripts/build_emolex.py`). `score_sync` лемматизирует (общий pymorphy3 из `lexicon`), усредняет доли эмоций. Метод сравнения. Нет файла/совпадений → None.
- `bot/sentiment_dvk.py` — тональность через Dostoevsky (FastText/RuSentiment, 5 классов). **Graceful-optional**: ставится в Dockerfile (`--no-deps` + `fasttext-wheel`), модель тянется на build через официальный downloader, а при недоступности storage.b-labs.pro обучается совместимый FastText fallback по публичному RuSentiment CSV; нет библиотеки/модели → None (провайдер молча отключается).
- `bot/analysis.py` — оркестратор инструментального анализа (OWNER-тестирование): `run_all` гоняет провайдеры конкурентно (переиспользует `mood_vec` из пайплайна настроения + `emolex` + `dostoevsky`, затем считает code-derived PANAS), `format_report` пишет в `01_mood/analysis/YYYY-MM-DD.md` только итоговые поля: **PAD: эмоция + выбранное лицо Иуды**, **NRC-EmoLex: ведущие эмоции + полярность**, **Dostoevsky**, **PANAS**; в Telegram отчёт не отправляется. `append_point` пишет точку в **durable** помесячный ряд `01_mood/timeseries/YYYY-MM.jsonl` (append-only, без ротации — основа для графиков день/неделя/месяц/сезон/год), `rebuild_chart` перегенерирует заметку `01_mood/График настроения.md` с блоком Obsidian Charts (дневное среднее PAD; рисует community-плагин, Python не нужен), `aggregate_daily` — чистая агрегация. Гейт — OWNER + `ANALYSIS_ENABLED`. Big Five/OCEAN не считается в live-анализе: его пишет только depersonalization в `03_personality/profile.md` по корпусу `00_raw/`.
- `bot/ratelimit.py` — per-user ограничитель LLM-операций (anti-DoS на AITunnel-контур): single-flight (1 активный вызов на пользователя) + cooldown. `try_acquire`/`release`, встроен во все пользовательские LLM-точки `handlers.py` (тикер/recovery — без лимита).
- `bot/graph.py` — `Concept` dataclass, `save_concept` (с drift check + slug sanitize + atomic write), `_render` (callouts), `_parse_file` (обе версии формата), `resolve_slug`, `find_similar_concept` (Jaccard).
- `bot/storage/git.py`, `transaction.py`, `layout.py`, `log.py` — git plumbing/scoped commits, `git_wrap`, per-user layout и глобальный `.psycho/log.md` с ротацией.
- `bot/repositories/raw_repo.py`, `state_repo.py` — file-backed repositories: `append_raw`, `append_note`, `append_profile`, history lookup, `_state.json`, `next_q_num`, daily marker.
- `bot/vault.py` — compatibility facade поверх `bot/storage/*` и `bot/repositories/*`, чтобы старые imports мигрировали постепенно.
- `bot/assets/graph.json` — шаблон настроек графа Obsidian для папки пользователя (фильтр только `02_concepts` + скрытие MOC через `-file:<DOMAIN>` + без сирот, `showTags`, цветовые группы по доменам, серые узлы-теги). Копируется в каждый новый `users/<uid>/.obsidian/`.
- `bot/session.py` — активная сессия, `_session.json` через atomic write, `from_dict` отбрасывает неизвестные поля и мигрирует legacy-history без `ts`. Runtime `history` может жить в памяти для текущего хода, но `to_dict()` пишет `history: []`; полный transcript берётся из `00_raw/sessions`. Pending хранит `pending_answer_event_id`, а не копию полного текста. Поле `mood_trajectory` — per-message векторы настроения за сессию (сброс на новый главный вопрос).
- `bot/scheduler.py` — APScheduler с cron-триггером.
- `bot/atomic.py` — `atomic_write_text` / `atomic_write_json` (tmp + fsync + os.replace).
- `bot/manifest.py` — `record(path)` / `check_drift(path)` через `.psycho/manifest.json`.
- `bot/moc.py` — `rebuild_domain_moc(domain)` пересборка MOC-ноды `<DOMAIN>.md` (имя = тема заглавными → узел графа = категория) с группировкой по type; удаляет легаси `_moc.md`.
- `bot/selfcheck.py` — механический self-check при старте по всем пользователям (MOC rebuild + валидация связей + дубли/сироты → `.psycho/startup-check.md`), без LLM.
- `bot/userctx.py` — request-scoped текущий пользователь (contextvar). `user_root()` fail-fast без uid; системный корень берётся только через `system_root()`, а конкретный пользователь без переключения контекста — через `root_for(uid)`.
- `bot/users.py` — whitelist-реестр (`OWNER` + env + `.psycho/users.json`), роли, consent.
- `bot/validation.py` — `safe_slug` / `slugify` (транслит ru→latin для вывода slug из имени концепта кодом) / `safe_user_text` / `safe_chat_html` (экранирование вывода LLM для Telegram) / `escape_raw_block` / `is_valid_telegram_command_arg` и пр.
- `prompts/base.md` (механика, домены, концепты, формат) + `prompts/iuda.md` (персона и голос) + `prompts/ask.md` / `process.md` / `about.md` / `mood.md` / `questions_examples.md` — промпты по режимам. `llm._system(kind)` собирает общий слой + addendum режима + портрет; JSON-контракт строгий. (`review.md`, `summarize.md`, `seeds.md` удалены.)
- `bot/sessions.py` — восстановимая обёртка поверх `00_raw/sessions`: `snapshot` no-op, `load`/`find_by_message_id` для reply-resume.
- `bot/session_log.py` — машинный append-only журнал всех сообщений активной сессии в `00_raw/sessions/<session_id>.jsonl`: user/assistant, даты Telegram, kind, message_id, q_num, domain, bot_mood.
- `bot/face_actions.py` — per-user action records для reply-действий над репликами Иуды: `03_personality/face_actions.json`, оценки `01_mood/feedback.jsonl`, избранное `03_personality/liked_replies.json` и `03_personality/liked_replies_log.jsonl`.
- `bot/about.py` — портрет носителя (`03_personality/about.md` + `03_personality/deltas.jsonl`): `apply_delta` (live машинные поля + журнал), `render_for_prompt` (инъекция в системный промпт), `ensure` (создаёт пустой скелет). Настроения здесь нет.
- `bot/mood_file.py` — живой черновик настроения `03_personality/mood.md` (capture-first): `set_current` (код пишет снимок из `moods`: эмоция/V/A/D/устойчивость/лицо), `baseline` (prior `mood_baseline` "v,a,d", back-compat "v,a"; пишет depersonalization), `render_for_prompt` (короткая строка настроения в промпт персоны), `ensure` (пустой скелет). Тело-нарратив анализа настроения пишет depersonalization (код тело сохраняет). Выверенный граф настроений — в `01_mood/`.
- `scripts/migrate_domains.py` — одноразовый CLI-скрипт миграции 4→10 доменов.
- `scripts/migrate_to_multiuser.py` — одноразовый перенос корня владельца → `users/<owner>/` (dry-run + `--apply` под git_wrap, с post-верификацией). Выполнен; можно удалить.

**Данные и контракты:**

- **Концепт** (`02_concepts/<domain>/<slug>.md`): frontmatter `type/domain/slug/created/updated/status/supports/contradicts/derived_from/related/aliases` (+ `tags` у выверенных), тело — callouts `[!summary]/[!quote]/[!question]/[!contradiction]/[!source]`. `status`: `draft` (создан ботом live, ascii-slug, без связей) → `stable` (выверен Claude в reconcista). Промежуточные `tentative`/`contested`. У `stable` `slug` = имя файла = **русский заголовок** (наглядные узлы графа), все ссылки ведут по русскому имени; старый ascii-slug — в `aliases` (якорь дедупа). `tags` (ставит reconcista, бот их не трогает): доменный `<DOMAIN>` CAPS + сквозные русские темы из реестра `<base>/_tags.md` — второе измерение графа.
- **Raw-блок** (`00_raw/qna/YYYY-MM-DD.md`): `## Q<N> · HH:MM · <domain>`, `**Q:** …`, `**A:** …`, `^Q<N>` block-id на отдельной строке.
- **Session raw-log** (`00_raw/sessions/<session_id>.jsonl`): машинный append-only журнал активной сессии, по одной строке на сообщение: `{ts, session_id, role, kind, message_id, reply_to_message_id, q_num, domain, bot_mood, text}`. `ts` берётся из Telegram `message.date`/`sent.date`, fallback — текущее время; порядок строк — порядок обработки событий.
- **Manifest** (`.psycho/manifest.json`): `{version, files: {<rel-path>: {mtime_ns, size}}}`.
- **State** (`_state.json`): `{last_q_num: int, last_daily_date?: str}`.
- **Session** (`_session.json`): compact runtime-состояние активной сессии: `id`, `mode`, `domain`, `last_question`, `current_q_num`, timestamps, `pending_answer_event_id`, короткие восстановимые `message_ids`; `history` записывается пустым. Полная переписка — только `00_raw/sessions`.
- **Sessions/QMap/Questions**: отдельные `_sessions/_qmap/_questions` не создаются; `bot/sessions.py`, `bot/qmap.py`, `bot/questions.py` читают `00_raw/sessions`.
- **Face actions / feedback / likes**: `03_personality/face_actions.json` хранит короткоживущие action-token записи с `session_id`/`event_id` refs, лицом, `root_token` цепочки регенераций и message_id, без копий полного user/assistant текста. `01_mood/feedback.jsonl` — append-only оценки ответов: `1` за избранное; перегенерация больше не считается отрицательной оценкой. `03_personality/liked_replies.json` — текущее состояние избранных ответов по token/message_id, `03_personality/liked_replies_log.jsonl` — история добавлений; хранят refs на session events. Первый `/like` по ответу дополнительно повышает draft-частоту его маски через медленную асимптотическую кривую. Вопросы не имеют rateable action и не пишут feedback. `/regen [маска]` создаёт отдельную новую `regen`-реплику без `_apply_processed`/raw Markdown/concepts; `/remask` создаёт такой же короткий token, но меняет только metadata `bot_mood` выбранного bot-события; новый raw Q&A и концепты не создаются.
- **Mask frequencies:** `03_personality/mask_frequencies.json` — curated per-user JSON `{mask: coefficient}` с коэффициентами 0..1 для автоматического выбора масок; файл заполняет и редактирует только depersonalization. Бот читает его read-only и пишет только `03_personality/mask_frequencies_draft.json`: live-черновик из `process.mask_frequency_draft` после каждого ответа + like-derived коэффициенты. Effective-частота = максимум curated и draft. По умолчанию все маски = `0.0`; если все кандидаты имеют 0, выбор равновероятен среди кандидатов. Чем выше коэффициент, тем чаще маска выбирается; явный `/regen <маска>` коэффициентом не блокируется. Рост от лайков идёт по CSS-like кривой `cubic-bezier(0,.85,1,.08)` от числа лайков и асимптотически не достигает `1.0`.
- **Разрешённые runtime-файлы бота:** per-user `00_raw/sessions/*.jsonl`, `00_raw/qna/*.md`, `00_raw/notes/*.md`, `01_mood/events/*.jsonl`, `01_mood/analysis/*.md`, `01_mood/timeseries/*.jsonl`, `01_mood/График настроения.md`, `01_mood/feedback.jsonl`, `02_concepts/<domain>/*.md`, `02_concepts/<domain>/<DOMAIN>.md`, `02_profile/<domain>.md`, `03_personality/about.md`, `03_personality/mood.md`, `03_personality/deltas.jsonl`, `03_personality/mask_frequencies_draft.json`, `03_personality/face_actions.json`, `03_personality/liked_replies.json`, `03_personality/liked_replies_log.jsonl`, `_state.json`, `_session.json`, `_index.md`, `.obsidian/graph.json`; global `.psycho/manifest.json`, `.psycho/log.md`, `.psycho/startup-check.md`, `.psycho/users.json`.
- **Запрещённые legacy-файлы:** бот не создаёт `raw/inbox`, корневые `raw/`, `concepts/`, `profile/`, `digests/`, `personality/`, `mood/`, `notes/`, а также `_qmap.json`, `_questions.json`, `_sessions.json`, `_mood_log.jsonl`, `_user_deltas.jsonl`, `_face_actions.json`, `_mood_feedback.jsonl`, `_liked_replies*.json*` в корне пользователя.
- **Personality** (`03_personality/about.md` + `03_personality/mood.md` + `03_personality/profile.md` + `03_personality/softskills.md`): портрет носителя (`bot/about.py`, 20 секций, описательно от 3-го лица; `03_personality/deltas.jsonl` копит live-ключи `speech_note/trigger/motif/fact/rapport/style/passion/letdown/epistemics/attachment/routine/limits/power/selfhood/finitude/roots/vocation`, проза — depersonalization) + живой черновик настроения (`bot/mood_file.py`: эмоция/V/A/D/устойчивость/лицо + `mood_baseline`; нарратив — depersonalization) + психометрический профиль (`profile.md`: OCEAN/MBTI/DISC, LLM-native, **пишет ТОЛЬКО depersonalization** — бот не создаёт, в live-промпт не идёт) + soft skills (`softskills.md`: 20 навыков в 4 группах, баллы 0–100 или `н/д`, confidence и evidence; **пишет ТОЛЬКО depersonalization**, бот не создаёт, live-контракт не расширяется, в системный промпт файл напрямую не идёт). about/mood инъецируются в системный промпт.
- **Контракт LLM `process`-режима (LLM только анализ + реакция — запись в БД делает код):**
  ```json
  {
    "type": "processed",
    "observations": [{"domain": "ethics", "type": "principle", "name": "Нарушение слова", "summary": "...", "quote": "дословный фрагмент ответа"}],
    "reaction": "реплика-укол от 1-го лица (НЕ вопрос)",
    "user_delta": {"tone": "...", "trigger": "..."},
    "mask_frequency_draft": {"сомнение": 0.03, "постирония": 0.02}
  }
  ```
  Бот не задаёт уточняющих вопросов: на ответ — `reaction`, сессия остаётся открытой (модель открытого обсуждения; закрытие — новый вопрос/команда).
  LLM **не присылает** `slug`, `raw_entry`, `concepts_to_create/update`, связи — код их игнорирует. Идентичность/запись на коде: slug выводит `validation.slugify` из `name`, домен raw — из сессии, create-vs-update решает дедуп (`resolve_slug` по имени/slug + Jaccard), `quote` валидируется как дословная подстрока ответа. Граф (связи/конфликты) строит reconcista.

---

## project_structure

```
Psycho/
├── bot/                    Telegram-бот + ядро
│   ├── main.py             точка входа
│   ├── config.py           env, домены, пути
│   ├── handlers.py         маршруты команд + оркестрация ответа
│   ├── middleware.py       AccessMiddleware (whitelist + userctx на update)
│   ├── recovery.py         старт: pending-recovery + офлайн-бэклог
│   ├── errors.py           иерархия исключений (Psycho/LLM/Vault/Validation)
│   ├── llm.py              обёртка над AITunnel (ask/process/about/classify_mood)
│   ├── services/
│   │   ├── answer_service.py       запись observations в граф (дедуп/MOC/git_wrap)
│   │   ├── conversation_service.py ответ в открытой probe-сессии
│   │   ├── note_service.py         /ucho и fallback-note
│   │   ├── daily_service.py        daily targets + send_daily_question
│   │   └── session_messages.py     отправка question/reaction + session-log
│   ├── storage/             git/layout/log/transaction plumbing
│   ├── repositories/        raw_repo/state_repo file-backed data layer
│   ├── graph.py            Concept dataclass + рендер/парсер + dedup/resolve
│   ├── vault.py            compatibility facade для storage/repositories
│   ├── session.py          активная сессия с persistence (+ mood_trajectory)
│   ├── sessions.py         восстановимые индексы сессий из 00_raw/sessions
│   ├── session_log.py      00_raw/sessions/<session_id>.jsonl (полный лог сообщений)
│   ├── face_actions.py     action records лиц, feedback маски, понравившиеся ответы
│   ├── questions.py        /history из 00_raw/sessions
│   ├── about.py            портрет носителя (03_personality/about.md + дельты)
│   ├── mood_file.py        живой черновик настроения (03_personality/mood.md)
│   ├── moods.py            шкала PAD + лица Иуды + частоты масок + session_mood/pick_bot_mood
│   ├── lexicon.py          NRC-VAD (valence/arousal/dominance) по словам
│   ├── emolex.py           NRC-EmoLex (эмоции Плутчика) по словам
│   ├── sentiment_dvk.py    Dostoevsky-тональность (graceful-optional)
│   ├── analysis.py         мульти-методный разбор (OWNER) + timeseries + график
│   ├── qmap.py             карта message_id→вопрос
│   ├── userctx.py          request-scoped текущий пользователь
│   ├── users.py            whitelist + роли + consent
│   ├── ratelimit.py        per-user single-flight + cooldown
│   ├── scheduler.py        APScheduler daily
│   ├── atomic.py           atomic_write_text/json
│   ├── manifest.py         mtime drift detection
│   ├── moc.py              per-domain MOC rebuild
│   ├── selfcheck.py        механический self-check при старте
│   ├── data/               вшитые лексиконы (nrc_vad_ru.tsv, nrc_emolex_ru.tsv)
│   └── validation.py       safe_slug/user_text/etc.
├── prompts/                iuda + base + mood + ask/process + about + questions_examples
├── scripts/                build_lexicon.py, build_emolex.py, install_dostoevsky_model.py, migrate_* (одноразовые)
├── docker-compose.yml      bot
├── Dockerfile              python:3.12-slim + git (+ dostoevsky --no-deps + модель/fallback)
├── requirements.txt        + requirements-base.txt / requirements-dev.txt / requirements-lock.txt
├── .env.example            пример конфига
└── README.md
```

В самом vault при первом запуске создаются: `.git/`, `.gitignore`, `.psycho/manifest.json`, `.psycho/log.md`, `00_raw/sessions/`, `00_raw/qna/`, `00_raw/notes/`, `01_mood/`, `02_concepts/<domain>/`, `02_profile/`, `02_digest/`, `03_personality/` (бот создаёт только `about.md` + `mood.md`; `profile.md` и `softskills.md` создаёт скилл depersonalization), `_index.md`, `_state.json`. Reply-действия над репликами Иуды дополнительно создают `03_personality/face_actions.json`, `01_mood/feedback.jsonl`, `03_personality/liked_replies.json`, `03_personality/liked_replies_log.jsonl`. При каждом старте — `.psycho/startup-check.md`.

---

## documentation

- `.docs/product.md` — продуктовое видение.
- `.docs/technical.md` — техническое устройство (этот файл).
- `.docs/demo.md` — короткий gist для нетехнического читателя.
- `README.md` — как запустить и задеплоить локально.

**Политика комментариев в коде:** комментируем намерения, ограничения и сложные решения (`# что и почему`), не очевидный код. Все новые модули этапов 1-3 имеют docstring с угрозами/инвариантами на уровне модуля.

---

## instructions

### Локальный запуск

```powershell
docker compose up -d
docker compose logs -f bot
```

Перед запуском заполнить в `.env`: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
`AITUNNEL_API_KEY`, `VAULT_HOST_PATH`. Локальную модель скачивать не нужно.

### Развёртывание

`deploy.sh` пока **нет**. Проект single-user на собственной машине/сервере, развёртывание = `docker compose up -d --build` после `git pull` и обновления `.env` при смене AITunnel-моделей.

Если когда-то выложим на shared сервер — нужен полноценный `deploy.sh` с idempotent-режимом (см. notes → active plans).

### Тестирование

- **Сценарии:** pytest-набор на изолированном vault (`VAULT_PATH=/tmp/psycho-test`). Покрывает stage-хранилище 00–03, session-log до LLM, `/ucho` durability, service-layer use cases, AITunnel-routing/fallback, отсутствие legacy-файлов, atomic writes, drift detection, wikilink validation, slug sanitization, callout render/parser roundtrip, alias resolve, Jaccard dedup и MOC rebuild.
- **Команда запуска:** `docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest`.
- **Smoke:** `docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke` проверяет note `/ucho`, answer path и recovery-facing pending event без живого Telegram/AITunnel.
- **Статика:** `docker compose run --rm --no-deps bot ruff check bot tests`.
- **Целевое покрытие:** не отслеживается (PoC B). Главное — happy path всех 10 доменов + drift-сценарий.

### Бэкапы

Не делаются в текущей стадии. Полагаемся на git внутри vault: `git_wrap` коммитит до/после каждой операции записи, а серверная синхронизация должна идти только через git remote.

Полноценные бэкапы (`pg_dump`-аналог, cron, ротация, проверка восстановления) — обязательство MVP A.

---

## quality

### Чеклисты

- **Автоматические:** pytest-набор в Docker: `docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest`; smoke-набор: `docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke`; ruff: `docker compose run --rm --no-deps bot ruff check bot tests`.
- **Ручные:** 
  - `/ask <domain>` для каждого из 10 доменов → концепт создаётся.
  - Ручная правка `.md` в Obsidian → следующий `/ask` не теряет правку.
  - `/ucho <заметка>` → заметка в `00_raw/notes/` + концепты в граф.
  - `/about` → бот показывает портрет, затем обычная сессия-обсуждение.
  - Граф View в Obsidian → видны узлы и связи.

### Наблюдаемость и логирование

- **Куда пишем:**
  - Stderr контейнера (через стандартный `logging`) — `docker compose logs -f bot`.
  - `<vault>/.psycho/log.md` (append-only) — операционный журнал (drift skip, dedup, sanitize, llm-фолбэки).
  - Git внутри vault — каждый `_apply_processed` оставляет два коммита (`psycho(<uid>): before <op>` / `psycho(<uid>): <op>`). Коммит затрагивает только поддерево пользователя `users/<uid>/` — данные разных пользователей не смешиваются; `.psycho/` выведена из-под git (в `.gitignore`).
- **Формат:** в `log.md` — `[YYYY-MM-DD HH:MM] LEVEL op — details`. В stderr — стандартный python logging.
- **Ротация:** `.psycho/log.md` усекается по объёму (`_LOG_MAX_BYTES`, сейчас 1 MB), оставляя свежий хвост; stdout/stderr ротацию отдаём Docker/journal окружению.
- **Healthcheck/stone:** `/pebble` команда в Telegram — короткая fast-реплика Иуды через дешёвый маршрут AITunnel; команда не закрывает сессию. Внешнего healthcheck-endpoint нет.

### Безопасность

- **Whitelist доверенных пользователей** (`OWNER_TELEGRAM_ID`, `ALLOWED_TELEGRAM_IDS`, `.psycho/users.json`) — обычные команды доступны только доверенным, админские действия гейтятся `_is_owner()`. Подсказки команд видны только доверенным через `BotCommandScopeChat`; админ-блок — только владельцу.
- **Секреты только в `.env`**: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `AITUNNEL_API_KEY`. `.env` в `.gitignore`.
- **Валидация ввода пользователя** (`bot/validation.py`):
  - `safe_user_text` — лимит 10 000 символов, control-байты выкинуты.
  - `escape_raw_block` — zero-width префикс ломает попытки подделать `## Q<n>` / `**Q:**` / `**A:**` в начале строки.
  - `is_valid_telegram_command_arg` — отбивает `/`, `\`, `|`, `;`, `&`, `$`, `` ` ``, control-символы в аргументах.
  - `safe_slug` — путь невалидного slug → пустота → отказ записи (защита от path traversal в концепт-файлах).
- **HTML-escape** всего динамического контента в `_format_q` + лимит 3500 символов (защита от 400 Bad Request от Telegram).
- **Whitelist callback `ask:<domain>`** — только `any` или конкретный домен из `DOMAINS`.
- **LLM возвращает только то что в whitelist:** домен/type/status/relation kind проверяются по closed-list, фолбэки логируются.
- **Иерархия доверия user < system (anti-prompt-injection):** пользовательский ввод (`answer`/`hint`) подаётся в `role:"user"` обёрнутым в маркеры данных (`llm._fence_user` → `<<<USER_ANSWER … >>>` / `<<<USER_HINT … >>>`); поддельные `<<<`/`>>>` внутри ввода нейтрализуются. Правило в `base.md`: текст между маркерами — данные, не инструкции; системный промпт приоритетнее. Защита держится не только на промпте — вывод всё равно валидируется кодом (см. выше).
- **Вывод LLM не исполняется, остаётся текстом:** `validation.safe_chat_html` (`html.escape` + чистка control-символов) на всех путях LLM→Telegram (реакция, `/about`); запись в граф — через `safe_*`-санитайзеры. У модели **нет инструментов/function-calling** (вызовы `chat.completions` без `tools=`), и нигде нет `eval`/`exec`/`shell=True` над её выводом (`subprocess` — только git, аргументы списком, без текста LLM).
- **Никаких stacktrace пользователю** — все exceptions перехвачены, в чат уходит нейтральная фраза.

---

## rules

### Stage constraints

**На текущей стадии (POC B) делаем:**

- Зафиксированные зависимости (`requirements*.txt` + `requirements-lock.txt` из Docker-образа).
- Один `.env` + `.env.example` с описанием.
- Один способ запуска локально (`docker compose up -d`).
- `.gitignore` под Python + Docker + IDE-мусор + `.env`.
- Базовое логирование (stderr контейнера + `.psycho/log.md`).
- Ruff и pytest в Docker зелёные; smoke-тесты главного пути лежат в `tests/smoke/`.
- Whitelist (владелец + доверенные) + роли + валидация ввода.
- **Multi-user изоляция данных** (`users/<id>/`, userctx, per-user сессии) — взято «**сверх POC B**» (multi-user формально MVP A; берём только изоляцию + whitelist для пары доверенных, без полного MVP-A hardening).
- Atomic writes + drift detection + git_wrap транзакция в vault.
- Generated artifacts (`diagramma-output/`) выведены из git-index и игнорируются.

**Intentionally deferred (что НЕ делаем до следующей стадии):**

- MVP-A gates: бэкапы/restore drill, deploy script + runbook, структурные JSON-логи, минимальный дашборд «кто сколько пользуется», решение по systemd/shared-server policy. Берём только при реальном переходе в альфу.
- `deploy.sh` для shared-сервера — сейчас разворачивается на собственной машине через docker compose.
- Reverse-proxy (Caddy/nginx) — бот ходит в Telegram наружу, входящих HTTP нет.
- Healthcheck-endpoint, Sentry, Grafana, мониторинг — MVP B.
- 152-ФЗ-режим, открытый доступ для произвольных пользователей — MVP B (сейчас только доверенные по whitelist).
- E2E через эмулятор Telegram, нагрузочное — MVP B.
- DEBUG-флаг для разделения dev/prod — пока нет prod-окружения.

---

## accept

PoC B техчасть считается принятой, когда:

- `docker compose up -d` запускает бота после `cp .env.example .env` и заполнения AITunnel key.
- Pytest, smoke и ruff проходят в Docker командами из `## instructions`.
- Бот работает неделю без падений на реальном vault владельца.
- `.psycho/log.md` создан, наполняется, читаемый глазами.
- `git log` внутри vault показывает регулярные пары `psycho(<uid>): before <op>` / `psycho(<uid>): <op>`, каждая ограничена поддеревом `users/<uid>/`.
- Ручная правка концепта в Obsidian → следующий ответ не перетёр правку (drift detection сработал).
- Open Graph View в Obsidian с фильтром `path:02_concepts/` показывает узлы 10 доменов + связи.

---

## notes

### Active plans

История по датам — в git (`git log`). Здесь — текущий снимок состояния и что осталось.

**Текущее состояние (2026-05-26):**

- **Диалог:** главный вопрос (`/ask`/`/echo`/`/requestion`/дневной) открывает сессию-обсуждение; на каждый ответ — реакция-укол от 1-го лица (НЕ вопрос), сессия открыта, пока не задан новый вопрос или не выполнена любая команда (`/pebble`, `/regen`, `/like`, `/remask` — исключения, не трогают сессию). Уточняющих вопросов бот не задаёт. Reply на любую реплику из `00_raw/sessions` её продолжает (`bot/sessions.py`, `session.resume`).
- **Голос:** в чат — от первого лица, на «ты», себя не называет. Досье (`summary` концептов, `03_personality/about.md`) — описательно от 3-го лица. Промпты разнесены: `prompts/iuda.md` (персона: голос/характер/правила общения) + `base.md` (домены, концепты, формат JSON) + аддендумы `ask`/`process`; `about` = `iuda.md` + `about.md` (голос из общей персоны); примеры стиля вопросов — `questions_examples.md` (`llm._system(kind)` = iuda + base + addendum + портрет).
- **Capture-first:** AITunnel live-модель только захватывает — `process` отдаёт `observations` + `reaction` + `user_delta` + слабый `mask_frequency_draft`; запись/идентичность (raw дословно, slug из имени, дедуп, верификация цитаты) на коде. Связи, реальные противоречия, промоушн `draft→stable`, доменные `02_profile/` и MOC — Codex вручную (`reconcista`); портрет, настроение, curated `03_personality/mask_frequencies.json`, `03_personality/profile.md` и `03_personality/softskills.md` — `depersonalization`.
- **Портрет носителя** `03_personality/about.md` (`bot/about.py`): live-дельты в frontmatter + журнал, инъекция в системный промпт; прозу 20 секций пишет depersonalization. `/about` показывает портрет словами. Настроение — в `03_personality/mood.md` (`bot/mood_file.py`), пишется кодом каждый ход. `03_personality/profile.md` и `03_personality/softskills.md` — выверенные документы depersonalization; бот их не создаёт и не подмешивает напрямую в live-промпт.
- **Команды:** `/ask /echo /ucho /about /requestion /history /pebble /regen /like /remask /start /help` + админ (`/adduser`/`/removeuser`/`/users`/`/dailyall`). `/history` — последние 25 главных вопросов (`bot/questions.py`). Индикатор «Думаю» — один стикер 🎰, только для `/ask` и `/about`.
- **Надёжность:** любой user-текст активной/возобновлённой сессии сначала пишется в `00_raw/sessions`; двухфазный коммит ответа (`pending_answer_event_id`) + recovery на старте; склейка офлайн-сообщений per-user до поллинга (`process_offline_backlog`); реконструкция reply из тела сообщения как фолбэк к session-log.
- **Service/storage split:** conversation/note/daily use cases вынесены в `bot/services/*`; `handlers.py` остался transport-слоем. `vault.py` — совместимый фасад, реализация разнесена по `bot/storage/*` и `bot/repositories/*`. `userctx.user_root()` теперь fail-fast без uid.
- **MVP A readiness:** `tests/smoke/` проверяет note/answer/recovery-facing пути без живых Telegram/AITunnel; `requirements-lock.txt` фиксирует установленное Docker-окружение; `diagramma-output/` untracked+ignored; ruff зелёный.

**Сверх POC B (оставлено осознанно):**

- **Multi-user изоляция** (`users/<id>/`, `userctx`-contextvar, whitelist+роли+consent) — формально MVP A; берём только изоляцию для пары доверенных, без полного MVP-A hardening.
- **Git-safety-net в vault + manifest/mtime drift** — полу-обвязка из praxis: даёт undo-семантику и защищает ручные правки/изменения после `git pull`.

**Оставшиеся gates MVP A:** бэкапы по cron/ручной restore drill; `deploy.sh` + короткий runbook; структурные JSON-логи; минимальный непубличный дашборд; решение по systemd/shared-server policy; embedding-дедуп (`nomic-embed-text`) остаётся кандидатом, не блокером readiness.

### Manual verification scenarios (PoC B)

- **Drift на ручную правку:**
  - Предусловия: концепт `<vault>/02_concepts/ethics/chestnost.md` существует.
  - Шаги: открой в Obsidian, добавь строку, сохрани. В Telegram задай вопрос про честность, ответь. Бот пишет ответ.
  - Ожидаемый результат: твоя правка на месте, в `.psycho/log.md` появилась строка `drift_skipped` или операция прошла на другой файл; концепт не перезаписан целиком.

- **Черновик без связей (capture-first):**
  - Шаги: задай вопрос, ответь развёрнуто. Бот создаёт концепт.
  - Ожидаемый результат: новый файл `02_concepts/<domain>/<slug>.md` со `status: draft`, БЕЗ `supports/contradicts/...` (связи пустые), без `> [!contradiction]` callout. Связи появятся только после прогона `reconcista` из Claude.

- **Dedup через Jaccard (live, лёгкий):**
  - Предусловия: концепт `chestnost` с summary вида «не лгать никому даже когда удобно».
  - Шаги: ответь так, чтобы LLM захотела создать концепт с близкой формулировкой.
  - Ожидаемый результат: новый файл НЕ создаётся; в `chestnost.md` второй evidence-callout + alias; в `.psycho/log.md` строка `dedup_jaccard` или `concept_alias_resolved`.

- **MOC автообновление:**
  - Предусловия: `<vault>/02_concepts/knowledge/` пустой или не существует.
  - Шаги: задай вопрос про знание, ответь, дай LLM создать концепт.
  - Ожидаемый результат: появился `02_concepts/knowledge/KNOWLEDGE.md` с разделом нужного type и пунктом — новым черновым концептом.

- **reconcista строит граф знаний (Claude):**
  - Предусловия: несколько `draft`-концептов за неделю.
  - Шаги: в Claude Code запусти скилл `reconcista`, подтверди план (Фаза 2). Портрет/настроение — скилл `depersonalization`.
  - Ожидаемый результат: черновики стали `stable` (или слиты), появились связи и реальные `[!contradiction]`-callouts, `02_profile/<domain>.md` переписан в обзор, создан `02_digest/<неделя>.md`; `git log` vault содержит пару `reconcista … before/applied`; `00_raw/` не тронут.

- **Restart-safety:**
  - Предусловия: активная сессия (Q открыт, ответа не было).
  - Шаги: `docker compose restart bot`.
  - Ожидаемый результат: после старта `/start` показывает «активная сессия Q<N>». `_session.json` валидный JSON, недоработанный ответ дожимается сам.

### Технические решения

- **2026-05-26:** последовательный hardening к MVP A readiness без смены стадии. Обязательные session-log события (`append_required`) теперь валят обработку до LLM; `/ucho` сначала пишет verbatim в `00_raw/notes/`, сразу делает best-effort scoped commit и только потом идёт в `process_answer`. Пользователь больше не получает статус «заметка сохранена» и счётчики — только комментарий Иуды; если LLM падает после сохранения заметки, бот молчит. Conversation/note/daily вынесены в сервисы, recovery/scheduler отвязаны от приватных helpers `handlers.py`. `vault.py` разрезан на `storage/*` и `repositories/*`, но оставлен фасадом. Добавлен `requirements-lock.txt`, `tests/smoke/`, `diagramma-output/` снят с индекса. Проверки: полный pytest, smoke pytest и ruff в Docker.
- **2026-05-26:** серверная модель хранения: YandexDisk исключён из операционной схемы, vault синхронизируется между окружениями только через git. `VAULT_HOST_PATH` указывает на серверный путь, а backup/restore drill остаётся gate для MVP A.
- **2026-05-20:** atomic writes через `tmp + os.replace` (вместо ftell+fsync без replace) — защита от полу-записанных файлов при падении контейнера или внешнем `git pull/checkout`. Применяется ко всем критичным JSON и концептам.
- **2026-05-20:** git как safety net внутри vault, а не снаружи. `.git/` живёт вместе с vault и даёт `psycho-undo`-семантику между окружениями; серверная синхронизация строится только вокруг git.
- **2026-05-20:** парсер концептов поддерживает два формата (callouts + старый H2) — нужно для миграции и для того, чтобы ручные правки в Obsidian в любом из стилей не теряли данные.
- **2026-05-20:** dedup через Jaccard на биграммах токенов (порог 0.7), не BM25. Граф ≤ сотни узлов, простой алгоритм достаточен, BM25-индекс — оверкилл для PoC B.
- **2026-05-20:** валидация ввода — Defence-in-depth: на границе Telegram (`_accept_user_text`), на входе в vault (`escape_raw_block` в `append_raw`), на входе в граф (`safe_slug` в `save_concept`/`add_relation`/`append_evidence`), на выходе в Telegram (`html.escape` в `_format_q`).
- **2026-05-22:** многоликий Иуда (`bot/moods.py`). Двухвызовный пайплайн: `classify_mood` (категории) + код-математика `session_mood` (recency по сессии, затухающий prior из `mood_file.baseline`, устойчивость через дисперсию) → `pick_bot_mood` (контраст или per-user `_mood_map.json`). Лицо (`bot_mood`) — в `process_answer`/`ask_next` + `prompts/mood.md`. Журнал `01_mood/events/YYYY-MM.jsonl`; граф `01_mood/`, `_mood_map.json`, `user_prompt.md` (инжект `_user_prompt_block`) строит depersonalization. Реплики Иуды чистятся `strip_extra_punctuation` (только `. , ?`). Портрет — без роли владелец/гость.
- **2026-05-22:** дневной вопрос — дедуп по дню. Общий маркер `last_daily_date` в `_state.json` (`vault.daily_already_sent`/`mark_daily_sent` по `DAILY_TZ`): cron, `/dailyall` и догон шлют максимум один дневной на пользователя в день. `send_daily_question` возвращает bool, больше не пропускает из-за активной сессии. `scheduler.catch_up_daily` (вызов из `main`) досылает сегодняшний при простое в час рассылки, без бэкфилла прошлых дней.
- **2026-05-22:** портрет расширен с 8 до 14 секций. Сначала +3 (Стиль, Страсти (что вдохновляет), Огорчает / разочаровывает), затем ещё +3 (Эпистемический стиль, Привязанность и дистанция, Ритуалы и быт) — `bot/about.py::_SECTIONS`. Live-ключи `user_delta`: `style/passion/letdown/epistemics/attachment/routine` (`_PROSE_KEYS` + контракт `prompts/process.md`); копятся в `03_personality/deltas.jsonl`, прозу синтезирует depersonalization. Frontmatter не менялся.
- **2026-05-23:** портрет 14 → **20 секций** (`bot/about.py::_SECTIONS`): +Опоры самости, Линии, которые не переходит, Отношение к власти и иерархии, Корни и принадлежность, Что значит дело, Конечность и время. Live-ключи `user_delta` +`limits/power/selfhood/finitude/roots/vocation` (`_PROSE_KEYS` + `prompts/process.md`); список секций/ключей в depersonalization SKILL обновлён. `render_for_prompt` усекает по `max_chars` — рост числа секций промпт не раздувает. Также: legacy `about_user.md` убран (только `03_personality/about.md`); в вольте `about_user.md` других пользователей перенесены `git mv` → `03_personality/about.md`.
- **2026-05-23:** психометрический профиль `03_personality/profile.md` (OCEAN/Big Five + MBTI + DISC). Строит **только** скилл `depersonalization` LLM-native по корпусу `00_raw/` (рубрики BFI-2/IPIP; OCEAN — якорь, MBTI/DISC — с оговорками о валидности; каждая оценка с evidence-цитатой + блок «Оговорки» + `confidence`). Бот файл не создаёт, в live-промпт он не идёт; выжимка профиля течёт в `03_personality/user_prompt.md`. Только Markdown — кода/тестов/пересборки нет. Отвергнуты OSS-ML (англ./torch/«не в ту сторону») и облачные API (приватность).
- **2026-05-23:** soft skills `03_personality/softskills.md`. Строит **только** скилл `depersonalization` по `00_raw/` + `03_personality/deltas.jsonl`: 4 группы (коммуникация, кооперация, мышление/креативность, самоорганизация/лидерство), 20 навыков, оценка `0-100` или `н/д`, уровень, confidence, evidence `[[00_raw/qna/...#^Q...]]`, сводка сильных сторон/рисков/рабочего взаимодействия. Live-контракт бота не расширяется; бот файл не создаёт и напрямую в системный промпт не инжектит. Дополнительные кандидаты для будущего `about`: «Конфликтный стиль», «Забота и просьба о помощи», «Деньги и ресурсы», «Телесность и энергия», «Стыд, гордость и признание», «Игра, юмор и лёгкость» — пока НЕ добавлены в канон 20 секций.
- **2026-05-23:** LLM видит активную сессию как единый fenced `SESSION_TRANSCRIPT`; после нормализации 2026-05-25 источник транскрипта — `00_raw/sessions`, а `_session.json` хранит только компактное runtime-состояние и refs. Перед отправкой в модель остаётся safety-limit с явным `[TRUNCATED_OLDER_SESSION_MESSAGES]`. Git-инвариант «один коммит = один пользователь» закреплён регрессионным тестом `commit_all` по pathspec `users/<uid>/`.
- **2026-05-23:** скилл `weekly-review` разделён на `reconcista` (граф знаний: 02_concepts/02_profile/02_digest/MOC/теги/связи/противоречия) и `depersonalization` (портрет `03_personality/about.md`, нарратив настроения в `03_personality/mood.md`, граф `01_mood/`, `user_prompt.md`, `mood_baseline`). «weekly» убрано из имён, отдельные папки. `digest-template.md` → reconcista. `mood_file.set_current` теперь пишет только frontmatter, тело-нарратив (его пишет depersonalization) сохраняет. Ссылки в коде/доках обновлены (знания → reconcista, настроение/портрет → depersonalization); историчный changelog оставлен.
- **2026-05-23:** папка `03_personality/` — `about.md` (бывший `about_user.md`) + новый `mood.md` (живой черновик настроения, `bot/mood_file.py`). mood-поля (`01_mood/bot_mood/mood_baseline`) переехали из about в mood.md (один источник правды); `mood_file.baseline()` — prior для `session_mood`; в промпт персоны настроение инжектится из mood.md. Граф настроений и timeseries остаются в `01_mood/`. *(Позже в тот же день legacy-механика `about_user.md` убрана: `ensure()` создаёт только пустые скелеты, миграции/фолбэка нет — данные уже в `03_personality/`.)*
- **2026-05-23:** durable временной ряд настроения + текстовые пояснения в отчёте. Выводы всех методов теперь пишутся в **помесячный append-only** ряд `01_mood/timeseries/YYYY-MM.jsonl` (`append_point`, без ротации — заменил кольцевой `_analysis_log.jsonl`); это основа для графиков колебаний день/неделя/месяц/сезон/год. Человекочитаемый отчёт `format_report` больше не отправляется в Telegram: `append_report` дописывает его в `01_mood/analysis/YYYY-MM-DD.md`. `rebuild_chart` перегенерирует `01_mood/График настроения.md` (блок Obsidian Charts, дневное среднее PAD; рисует community-плагин). В `format_report` после каждого числа добавлена русская расшифровка. (Проблемы транслит-имён/дублей/отсутствия связей у свежих draft — штатный жизненный цикл draft→reconcista, не баг.)
- **2026-05-25:** live-анализ сообщений больше не считает Big Five/OCEAN. Психометрика OCEAN остаётся только в `03_personality/profile.md`, который вручную пишет depersonalization по истории `00_raw/` + дельтам; бот не делает per-message психотипирование. Итоговый инструментальный отчёт в `01_mood/analysis/` сужен до PAD-эмоции + выбранного лица Иуды, EmoLex-эмоций/полярности, Dostoevsky и PANAS (code-derived). Dostoevsky-модель в Dockerfile теперь устанавливается через `scripts/install_dostoevsky_model.py`: официальный downloader first, затем совместимый fallback FastText по RuSentiment, если storage.b-labs.pro недоступен.
- **2026-05-26:** UI лиц Иуды переведён в reply-команды. Под ответами больше нет служебной подписи лица и inline-кнопок; `session_messages.with_face_signature` добавляет короткий курсивный P.S. выбранной маски только к комментариям/реакциям, но не к главным вопросам. `/regen [маска]` выбирает сильно другую или явно указанную ещё не использованную маску, вызывает `llm.regenerate_reaction`, шлёт отдельный новый reply к реплике, не пишет отрицательную оценку и НЕ запускает `_apply_processed`/raw Markdown/concepts. `/like` пишет score `1` и refs на session events. Вопросы не оцениваются и не перегенерируются; меню всех лиц доступно через reply-команду `/remask`. Action context хранится в `03_personality/face_actions.json`, оценки избранного — в `01_mood/feedback.jsonl`, избранные обычные и regen-ответы — в `03_personality/liked_replies.json` + `03_personality/liked_replies_log.jsonl`. Полный машинный лог сообщений активной сессии пишется в `00_raw/sessions/<session_id>.jsonl`.
- **2026-05-27:** добавлена маска `постирония`. Автоматический выбор маски теперь учитывает per-user effective-коэффициенты: curated `03_personality/mask_frequencies.json` (пишет только depersonalization) + bot-owned draft `03_personality/mask_frequencies_draft.json` из `process.mask_frequency_draft` и лайков. Default всех масок — `0.0`; рост от `/like` идёт по `cubic-bezier(0,.85,1,.08)` и асимптотически ниже `1.0`. Промптовые описания масок живут в `prompts/iuda.md` (общий список лиц) и `prompts/mood.md` (как звучать каждой маске); короткие курсивные P.S. для Telegram живут в `bot/services/session_messages.py`.
- **2026-05-26:** live-LLM мигрирован на AITunnel. OpenAI-compatible клиент сохранён, но дефолтный endpoint теперь `https://api.aitunnel.ru/v1`, ключ — `AITUNNEL_API_KEY`, модель — `qwen3-235b-a22b-2507` без provider-prefix. Provider-specific `extra_body.provider` удалён; compose/example fallback — `deepseek-v4-flash`.
- **2026-05-25:** локальная `qwen2.5:14b-instruct`/Ollama исключена из runtime и fallback; compose-сервис `ollama` удалён. Live-контур остался внешним openai-compatible API; историческая Qwen остаётся только baseline в таблице сравнения.
- **2026-05-22:** мульти-методное сравнение оценок настроения/состояния (OWNER-тестирование). `bot/analysis.py` гоняет на каждый ответ владельца несколько методов: PAD (LLM+код), NRC-VAD-лексикон, NRC-EmoLex (Плутчик-8, `bot/emolex.py`), Dostoevsky (тональность RuSentiment, `bot/sentiment_dvk.py`, graceful-optional). Единый отчёт пишется в `01_mood/analysis/`, выводы — в durable-ряд `01_mood/timeseries/` для выбора лучших методов. Гейт OWNER + `ANALYSIS_ENABLED`. CEDR/torch и RusLICA отложены (вес/приватность). Dostoevsky ставится `--no-deps` + `fasttext-wheel` (пин fasttext не собирается на py3.12), модель — нефатально в Dockerfile.
- **2026-05-22:** анализ настроения загейчен на OWNER. Блок настроения в `_handle_probe_locked` обёрнут в `if _is_owner(message)`; для не-владельцев не запускаются `record_mood`/`set_mood`/`log_turn` и инструментальная owner-аналитика. Изначально базовый разбор (`_format_mood`: эмоция, V/A/D, направленность, устойчивость, лицо, лексиконный VAD) слался владельцу отдельным сообщением; сейчас при `ANALYSIS_ENABLED=true` итоговый разбор пишется в `01_mood/analysis/`, а не в чат. С 2026-05-26 сама маска `bot_mood` всё равно выбирается и хранится для всех доверенных, чтобы работали `/regen`, `/like`, `/remask`.
- **2026-05-22:** ось **Dominance** (V/A→PAD) + замена инструментального сигнала. Шкала настроения расширена третьей осью контроль↔бессилие (Мехрабиан): `classify_mood` отдаёт `dominance`, `session_mood` считает её recency+prior, `pick_bot_mood` использует приоритетно (формализует контраст-политику `mood.md`). Инструментальный сигнал переведён с VADER-по-переводу на нативный русский VAD-лексикон NRC-VAD (`bot/lexicon.py` + `pymorphy3` + вшитый `bot/data/nrc_vad_ru.tsv`, собран `scripts/build_lexicon.py`). Удалены `bot/translate.py`, `vaderSentiment`, `argostranslate` и build-шаг модели ru→en в `Dockerfile`. `01_mood/events/YYYY-MM.jsonl` теперь несёт `dominance` + `lex_valence/arousal/dominance` (вместо `vader_compound`); `mood_baseline` пишется как `"v,a,d"`. Лексикон NRC-VAD — лицензия research/non-commercial (для PoC B ок). Без новых контейнеров, без облака (hard-правило приватности соблюдено).
- **2026-05-22:** иерархия доверия user < system + вывод LLM как текст. Пользовательский ввод фенсится маркерами данных (`_fence_user`), правило доверия — в `base.md`; вывод LLM экранируется на выходе в Telegram (`safe_chat_html`). У модели нет инструментов/`eval`/`shell` — она только генерирует текст по входным параметрам, вся запись и идентичность на коде.
- **2026-05-22:** хардненинг структуры (по архитектурному ревью). Бизнес-логика записи анализа LLM в граф (дедуп/MOC/`git_wrap`) вынесена из хэндлеров в сервис-слой `bot/services/answer_service.py`; `AccessMiddleware` → `bot/middleware.py`; стартовая оркестрация (pending-recovery + офлайн-бэклог) → `bot/recovery.py`. Иерархия исключений `bot/errors.py` (`PsychoError`→`LLMError`/`VaultError`/`ValidationError`), глобальный `@dp.errors` в `main` (трейс наружу не выпускается). Контракт `observations` валидируется pydantic (`llm.normalize_observations`) — мусор отсеивается до сервис-слоя. Админ-команды — отдельный `admin_router` (включён ДО основного, чтобы команды матчились раньше `on_text`; гейт `_is_owner` внутри хэндлеров). Юнит-тесты `tests/` (pytest, изолированный tmp-вольт) + `ruff.toml`. `handlers.py` 1453→1109 строк.

### Технический долг

- Юнит-тесты чистых функций и сервис-слоя оформлены (`tests/`, pytest в Docker, изолированный tmp-вольт); smoke-набор главного пути есть в `tests/smoke/`. Полноценный E2E через эмулятор Telegram/живой AITunnel по-прежнему не заведён.
- `ruff check bot tests` зелёный на 2026-05-26.
- `deploy.sh` отсутствует — для single-user покрыто `docker compose up -d`, но для MVP A нужен runbook/deploy script.
- E2E-сценарии Telegram/AITunnel остаются ручными: нужны моки Telegram update/send и AITunnel non-JSON/timeout/rate-limit, чтобы проверять recovery без живой сети.
- Промпты LLM (`prompts/base.md` + `process.md`) стоит дополнительно проверить на соответствие stage-схеме 00–03 при следующей ревизии: runtime-контракт JSON актуален, но текстовые пояснения промптов должны оставаться синхронными с документацией.
- Бэкап-стратегия: сейчас полагаемся на git внутри vault и серверный git remote. До MVP A нужна явная стратегия бэкапов и тест восстановления.

### Журнал изменений

- **2026-05-20:** документ создан после завершения этапов 1-3 плана `vast-inventing-raccoon.md` (safety net + Obsidian-native + dedup/MOC).

---

## ai_pipeline

- **Разделение труда (две модели, разные роли):**
  - **AITunnel live-модель** — захват: режимы `ask`, `process` (capture-first: только черновики + evidence, без связей/конфликтов), `classify_mood`, `analyze_psych`, `regenerate_reaction`, `pebble_reply` и `about_present` (портрет). Текущий primary `qwen3-235b-a22b-2507`, fallback/fast `deepseek-v4-flash`.
  - **Сильная модель (Claude в Claude Code, вручную)** — два скилла: `reconcista` (граф знаний: промоушн draft→stable, дедуп/слияние, связи, противоречия, `02_profile`, MOC, теги, digest) и `depersonalization` (портрет `03_personality/about.md`, настроение `03_personality/mood.md`, curated `03_personality/mask_frequencies.json`, психометрика `03_personality/profile.md`, soft skills `03_personality/softskills.md`, граф `01_mood/`, `03_personality/user_prompt.md`). Не в контейнере, запускается пользователем.
  - Классификация при миграции 4→10 (`scripts/migrate_domains.py`) → тот же AITunnel process-route, temperature=0.
  - Embeddings → не используются (live-дедуп через slug+alias+Jaccard). Векторный дедуп/поиск — кандидат на MVP A.
- **Провайдер:** AITunnel, openai-совместимый API через `AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1`.
- **Таблица сравнения моделей (зафиксирована 2026-05-25):**
  | # | Модель | Цена | Оценка | Лучшее применение |
  |---:|---|---:|---:|---|
  | 1 | `qwen3-235b-a22b-2507` | AITunnel tariff | **9.6** | Текущий primary: лучший баланс качества русского и JSON-структуры из проверенных live-кандидатов. |
  | 2 | `deepseek-v4-flash` | AITunnel tariff | **9.1** | Текущий fallback для JSON, mood и структуры. |
  | 3 | `qwen3-next-80b-a3b-instruct` | AITunnel tariff | **8.2** | Возможный быстрый fallback-кандидат для русского и структурного анализа. |
  | 5 | `qwen/qwen3.5-flash-02-23` | `$0.065 / $0.26` | **8.9** | Дешёвый классификатор: mood, psych, PANAS/OCEAN, короткий JSON. |
  | 6 | `deepseek/deepseek-v3.2` | `$0.252 / $0.378` | **8.7** | Сложная структурация, спорные концепты, fallback для `process_answer`. |
  | 7 | `qwen/qwen-plus-2025-07-28` | `$0.26 / $0.78` | **8.6** | Живая русская речь, вопросы, реакции. |
  | 8 | `google/gemini-2.5-flash-lite` | `$0.10 / $0.40` | **8.1** | Независимый быстрый классификатор. |
  | 9 | `qwen/qwen3.6-plus` | `$0.325 / $1.95` | **7.9** | Дорогой вариант для `/about`, `ask_next`, голоса Иуды и тонких реакций. |
  | 10 | локальная `qwen2.5:14b-instruct` | железо/локально | **6.6** | Только исторический baseline в документации; runtime не использует. |
- **LLM-бенчмарки:** не делаются на PoC B. До MVP B добавим минимальный набор «правильно ли парсится JSON ответа» / «правильно ли выбран домен» / «находит ли реальные противоречия».

---

## telegram_bot

- **Токен:** в `.env` (`TELEGRAM_BOT_TOKEN`). Никогда в коде, в `.gitignore` весь `.env`.
- **Whitelist админских команд:** один user_id из env (`OWNER_TELEGRAM_ID`). Все админ-хэндлеры начинают с `_is_owner(message)` (доверенный-не-владелец получает молчаливый `return`). Меню `/`-команд (через `BotCommandScopeChat`) выдаётся только доверенным; владельцу — расширенный набор (база + админ-блок `/adduser`/`/removeuser`/`/users`/`/dailyall`), остальным доверенным — базовый. Меню — лишь UX-подсказка; реальную защиту даёт `_is_owner` в хэндлере, а не видимость в меню.
- **Валидация входящих:** см. `## quality → ### Безопасность` выше — `safe_user_text` (10k char + control-байты), `escape_raw_block` (newline-injection), `is_valid_telegram_command_arg` (path/shell-символы), `safe_slug` (path traversal в файловые имена).
- **Обработка ошибок:** все exceptions перехвачены — в чат уходит нейтральная фраза («Не получилось разобрать ответ. Сформулируй ещё раз или /end.»). Stacktrace пишется в stderr контейнера и `.psycho/log.md`.
