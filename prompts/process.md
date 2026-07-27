# Режим разбора ответа

Верни:

1. `reaction` — короткая нейтральная реакция, 1–3 предложения, не вопрос.
2. `personality_delta` — 0–3 новых наблюдения о характере и способе быть. Поля:
   - `aspect`: одно из `character`, `speech`, `emotional_regulation`,
     `relationships`, `values`, `motivation`, `habits`, `self_image`, `triggers`;
   - `summary`: осторожный вывод от третьего лица;
   - `quote`: дословный фрагмент ответа;
   - `confidence`: число 0..1.
Не создавай концепты, мировоззренческие атомы, slug, связи, профили по темам или
следующий вопрос.

```json
{
  "reaction": "...",
  "personality_delta": [
    {
      "aspect": "character",
      "summary": "Склонен скрывать уязвимость за резкостью.",
      "quote": "дословный фрагмент",
      "confidence": 0.7
    }
  ]
}
```
