# Сравнение моделей для Psycho

Зафиксировано: 2026-05-25.

Контекст выбора: live-пайплайн бота работает через OpenRouter и обрабатывает русский текст в задачах `process_answer`, `classify_mood`, `analyze_psych`, `ask_next`, `about_present` и `regenerate_reaction`. Локальная `qwen2.5:14b-instruct` оставлена в таблице только как исторический baseline; runtime её не использует.

| # | Модель | Цена | Оценка | Лучшее применение |
|---:|---|---:|---:|---|
| 1 | `nvidia/nemotron-3-super-120b-a12b:free` | free | **8.4** | Сильный free-кандидат для JSON и длинного контекста, но не default из-за OpenRouter privacy/data-policy ограничений. |
| 2 | `qwen/qwen3-next-80b-a3b-instruct:free` | free | **8.2** | Free fallback-кандидат для русского и структурного анализа, но нестабилен по upstream rate-limit. |
| 3 | `qwen/qwen3-235b-a22b-2507` | `$0.071 / $0.10` | **9.6** | Текущий primary: лучший баланс качества, русского и цены при сохранении privacy-policy. |
| 4 | `deepseek/deepseek-v4-flash` | `$0.10 / $0.20` | **9.1** | Текущий fallback для JSON, mood и структуры. |
| 5 | `qwen/qwen3.5-flash-02-23` | `$0.065 / $0.26` | **8.9** | Дешёвый классификатор: mood, psych, PANAS/OCEAN, короткий JSON. |
| 6 | `deepseek/deepseek-v3.2` | `$0.252 / $0.378` | **8.7** | Сложная структурация, спорные концепты, fallback для `process_answer`. |
| 7 | `qwen/qwen-plus-2025-07-28` | `$0.26 / $0.78` | **8.6** | Живая русская речь, вопросы, реакции. |
| 8 | `google/gemini-2.5-flash-lite` | `$0.10 / $0.40` | **8.1** | Независимый быстрый классификатор. |
| 9 | `qwen/qwen3.6-plus` | `$0.325 / $1.95` | **7.9** | Дорогой вариант для `/about`, `ask_next`, голоса Иуды и тонких реакций. |
| 10 | локальная `qwen2.5:14b-instruct` | железо/локально | **6.6** | Только исторический baseline в документации; runtime не использует. |

## Текущий live-контур

| Роль | Модель |
|---|---|
| Primary | `qwen/qwen3-235b-a22b-2507` |
| Fallback | `deepseek/deepseek-v4-flash` |
| Former free candidates | `nvidia/nemotron-3-super-120b-a12b:free`, `qwen/qwen3-next-80b-a3b-instruct:free` |
