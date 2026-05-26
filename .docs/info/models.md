# Сравнение моделей для Psycho

Зафиксировано: 2026-05-26.

Контекст выбора: live-пайплайн бота работает через AITunnel и обрабатывает русский текст в задачах `process_answer`, `classify_mood`, `analyze_psych`, `ask_next`, `about_present` и `regenerate_reaction`. Локальная `qwen2.5:14b-instruct` оставлена в таблице только как исторический baseline; runtime её не использует. Цены зависят от AITunnel-тарифа и не фиксируются в репозитории.

| # | Модель | Цена | Оценка | Лучшее применение |
|---:|---|---:|---:|---|
| 1 | `qwen3-235b-a22b-2507` | AITunnel tariff | **9.6** | Текущий primary: лучший баланс качества русского и JSON-структуры из проверенных live-кандидатов. |
| 2 | `deepseek-v4-flash` | AITunnel tariff | **9.1** | Текущий fallback для JSON, mood и структуры. |
| 3 | `qwen3.5-flash-02-23` | AITunnel tariff | **8.9** | Дешёвый классификатор: mood, psych, PANAS/OCEAN, короткий JSON. |
| 4 | `deepseek-v3.2` | AITunnel tariff | **8.7** | Сложная структурация, спорные концепты, fallback для `process_answer`. |
| 5 | `qwen-plus-2025-07-28` | AITunnel tariff | **8.6** | Живая русская речь, вопросы, реакции. |
| 6 | `qwen3-next-80b-a3b-instruct` | AITunnel tariff | **8.2** | Возможный быстрый fallback-кандидат для русского и структурного анализа. |
| 7 | `gemini-2.5-flash-lite` | AITunnel tariff | **8.1** | Независимый быстрый классификатор, если доступен в AITunnel. |
| 8 | `qwen3.6-plus` | AITunnel tariff | **7.9** | Дорогой вариант для `/about`, `ask_next`, голоса Иуды и тонких реакций. |
| 9 | локальная `qwen2.5:14b-instruct` | железо/локально | **6.6** | Только исторический baseline в документации; runtime не использует. |

## Текущий live-контур

| Роль | Модель |
|---|---|
| Primary | `qwen3-235b-a22b-2507` |
| Fallback | `deepseek-v4-flash` |
| Optional fallback candidates | `qwen3-next-80b-a3b-instruct` |
