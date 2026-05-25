# Flowchart обработки сообщения Psycho

## Цель

Показать, как одно входящее сообщение пользователя проходит через обязательные этапы хранения и анализа: `00_raw`, `01_mood`, `02_concepts`, `02_profile`, `02_digest`, `03_personality`.

## Основные элементы

- Access check отделяет доверенных пользователей от остальных.
- Session routing определяет активную, возобновлённую или новую note-session.
- `00_raw/sessions` получает user event до любого LLM-вызова.
- `01_mood` работает только для OWNER и оставляет отдельные mood-артефакты.
- OpenRouter возвращает анализ, reaction и user_delta; при сбое используется fallback.
- `02_concepts`, `02_profile` и `03_personality` получают производные артефакты.
- `02_digest` не является live-записью каждого сообщения: digest собирается позже reconcista по refs.

## Связи

Happy path идёт сверху вниз: входящее сообщение → raw-log → mood → LLM → stage-артефакты → реакция → assistant event. Ошибочный путь LLM уходит через fallback; при полном сбое пользователь получает нейтральное сообщение, а граф не портится.

## Допущения и границы

Диаграмма показывает логический pipeline, а не отдельные Python-модули. Git-транзакции, MOC rebuild и кнопки OWNER сведены к укрупнённым блокам, чтобы сохранить читаемость.

## Легенда

Raw: immutable session capture before LLM; Mood: OWNER-only analysis artifacts; Drafts: concepts/profile/personality writes; Deferred: digest is built later by reconcista; Failure: fallback or neutral error

## Mermaid source

```mermaid
flowchart TD
  Start(["Поступило сообщение пользователя"])
  Access{"Пользователь доверенный?"}
  Ignore(["Молчаливо игнорировать"])
  Session{"Есть активная или возобновляемая сессия?"}
  OpenSession["Открыть или возобновить сессию\n/ask, daily, /echo, /requestion, /about, /ucho, reply, note"]
  Raw["00_raw\nЗаписать user event в sessions до LLM"]
  MoodGate{"OWNER и включён mood pipeline?"}
  Mood["01_mood\nPAD, quality, face, events, analysis, timeseries"]
  LLM["OpenRouter process_answer\nobservations, reaction, user_delta"]
  Valid{"JSON валиден?"}
  Fallback["Fallback model или LLMError\nнейтральное сообщение при полном сбое"]
  QNA["00_raw/qna\nQ&A-проекция с block-id"]
  Concepts["02_concepts\nDraft concepts, dedup, evidence, MOC"]
  Profile["02_profile\nКороткая доменная выжимка"]
  Personality["03_personality\nabout, mood, deltas, action refs"]
  Digest["02_digest\nРучной digest позже через reconcista\nrefs без копии полной переписки"]
  Reply["Telegram\nОтправить reaction, OWNER controls"]
  AssistantRaw["00_raw\nЗаписать assistant event в sessions"]
  Done(["Сессия остаётся открытой"])

  Start --> Access
  Access -->|"Нет"| Ignore
  Access -->|"Да"| Session
  Session -->|"Нет"| OpenSession
  Session -->|"Да"| Raw
  OpenSession --> Raw
  Raw --> MoodGate
  MoodGate -->|"Да"| Mood
  MoodGate -->|"Нет"| LLM
  Mood --> LLM
  LLM --> Valid
  Valid -->|"Нет"| Fallback
  Fallback -->|"fallback ok"| QNA
  Fallback -->|"all failed"| Reply
  Valid -->|"Да"| QNA
  QNA --> Concepts
  Concepts --> Profile
  Profile --> Personality
  Personality --> Digest
  Personality --> Reply
  Reply --> AssistantRaw
  AssistantRaw --> Done
```
