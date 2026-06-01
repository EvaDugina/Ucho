# Сравнение моделей для Psycho

Зафиксировано: 2026-06-01.

Контекст выбора: live-пайплайн бота работает через OpenAI-compatible provider и обрабатывает русский текст в задачах `process_answer`, `classify_mood`, `analyze_psych`, `ask_next`, `about_present` и `regenerate_reaction`. Если `OPENROUTER_API_KEY` непустой, используется OpenRouter; если пустой — AITunnel fallback. Локальная `qwen2.5:14b-instruct` оставлена в таблице только как исторический baseline; runtime её не использует. Цены зависят от выбранного provider и не фиксируются в репозитории.

| # | Модель | Цена | Оценка | Лучшее применение |
|---:|---|---:|---:|---|
| 1 | `qwen/qwen3-235b-a22b-2507` / `qwen3-235b-a22b-2507` | provider tariff | **9.6** | Текущий primary: лучший баланс качества русского и JSON-структуры из проверенных live-кандидатов. |
| 2 | `deepseek/deepseek-v4-flash` / `deepseek-v4-flash` | provider tariff | **9.1** | Текущий fallback для JSON, mood и структуры. |
| 3 | `qwen3.5-flash-02-23` | provider tariff | **8.9** | Дешёвый классификатор: mood, psych, PANAS/OCEAN, короткий JSON, если доступен у provider. |
| 4 | `deepseek-v3.2` | provider tariff | **8.7** | Сложная структурация, спорные концепты, fallback для `process_answer`, если доступен у provider. |
| 5 | `qwen-plus-2025-07-28` | provider tariff | **8.6** | Живая русская речь, вопросы, реакции, если доступен у provider. |
| 6 | `qwen3-next-80b-a3b-instruct` | provider tariff | **8.2** | Возможный быстрый fallback-кандидат для русского и структурного анализа. |
| 7 | `gemini-2.5-flash-lite` | provider tariff | **8.1** | Независимый быстрый классификатор, если доступен у provider. |
| 8 | `qwen3.6-plus` | provider tariff | **7.9** | Дорогой вариант для `/about`, `ask_next`, голоса Иуды и тонких реакций. |
| 9 | локальная `qwen2.5:14b-instruct` | железо/локально | **6.6** | Только исторический baseline в документации; runtime не использует. |

## Текущий live-контур

| Роль | Модель |
|---|---|
| Primary | OpenRouter: `qwen/qwen3-235b-a22b-2507`; AITunnel: `qwen3-235b-a22b-2507` |
| Fallback | OpenRouter: `deepseek/deepseek-v4-flash`; AITunnel: `deepseek-v4-flash` |
| Optional fallback candidates | `qwen3-next-80b-a3b-instruct` |
