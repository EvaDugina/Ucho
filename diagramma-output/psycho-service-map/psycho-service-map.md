# Service-map Psycho

## Цель

Показать приложение Psycho целиком на уровне сервисов: кто взаимодействует с ботом, какие внешние системы участвуют, где живёт live-пайплайн и какие stage-артефакты остаются в Obsidian vault.

## Основные элементы

- Владелец и доверенные пользователи общаются с ботом через Telegram.
- `Psycho bot container` принимает updates, управляет сессиями, вызывает OpenRouter и пишет в vault.
- OpenRouter используется как внешний LLM-контур: primary Qwen и fallback DeepSeek.
- Obsidian vault хранит пользовательские stage-артефакты `00_raw`, `01_mood`, `02_*`, `03_personality`.
- Git safety net и manifest защищают данные от частичных записей и потери ручных правок.
- `reconcista` и `depersonalization` — ручные сильные проходы, которые выверяют граф, digest и портрет.

## Связи

Пользователь пишет в Telegram, Telegram доставляет update в bot container, бот сначала фиксирует raw-событие, затем обращается к OpenRouter и сохраняет производные артефакты. Obsidian читает тот же vault, а ручные скиллы работают поверх накопленных данных.

## Допущения и границы

Схема намеренно не показывает секреты, env-переменные, конкретные Docker-порты и внутренние функции модулей. Это container/service-level карта, не code-level view.

## Легенда

Client: Telegram/Obsidian users; Service: bot and live pipeline; Storage: vault stage artifacts and git safety net; External: Telegram and OpenRouter; Manual: strong-model skills

## Mermaid source

```mermaid
flowchart LR
  subgraph Client["Client"]
    Owner["Владелец в Telegram"]
    Trusted["Доверенный пользователь"]
    Obsidian["Obsidian UI и Graph View"]
  end

  subgraph External["External"]
    Telegram["Telegram Bot API"]
    OpenRouter["OpenRouter API\nQwen primary, DeepSeek fallback"]
  end

  subgraph App["Application"]
    Bot["Psycho bot container\naiogram handlers"]
    Router["LLM routing\nJSON/text calls, fallback, warnings"]
    Capture["Capture-first pipeline\n00_raw -> 01_mood -> 02/03"]
    Skills["Manual strong-model skills\nreconcista, depersonalization"]
  end

  subgraph Data["Data"]
    Vault[("Obsidian vault\nusers/<uid>")]
    Raw[("00_raw\nsessions, qna, notes")]
    Mood[("01_mood\nevents, analysis, timeseries")]
    Graph[("02_concepts, 02_profile, 02_digest")]
    Persona[("03_personality\nabout, mood, deltas")]
    Safety[("Git safety net\nmanifest, log, before/after commits")]
  end

  Owner -->|"пишет, отвечает, управляет"| Telegram
  Trusted -->|"пишет и отвечает"| Telegram
  Telegram -->|"updates"| Bot
  Bot -->|"sendMessage"| Telegram
  Bot --> Router
  Router -->|"chat/completions"| OpenRouter
  OpenRouter -->|"JSON или текст"| Router
  Router --> Capture
  Capture -->|"raw до LLM"| Raw
  Capture -->|"mood только OWNER"| Mood
  Capture -->|"draft concepts, qna, profile"| Graph
  Capture -->|"about, mood, deltas"| Persona
  Raw --> Vault
  Mood --> Vault
  Graph --> Vault
  Persona --> Vault
  Bot -->|"git_wrap"| Safety
  Safety --> Vault
  Obsidian <-->|"ручное чтение и правки"| Vault
  Skills -->|"промоушн draft, digest, portrait"| Vault
```
