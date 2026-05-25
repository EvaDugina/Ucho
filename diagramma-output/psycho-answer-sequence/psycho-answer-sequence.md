# Sequence одного ответа Psycho

## Цель

Показать последовательность одного пользовательского ответа: от Telegram update до raw-log, OpenRouter-вызова, сохранения артефактов и реакции бота.

## Участники

- Пользователь пишет ответ в Telegram.
- Telegram доставляет update и получает ответное сообщение.
- Psycho bot управляет доступом, сессией, LLM routing и сохранением.
- `00_raw/sessions` фиксирует полный transcript сессии.
- `01_mood` получает mood-события OWNER.
- OpenRouter выполняет primary/fallback LLM-вызовы.
- `00_raw/qna + 02/03` — укрупнённый блок stage-записей.
- `git_wrap` защищает запись before/after commit и rollback.

## Связи

Сначала бот пишет user event в raw-log, затем при необходимости классифицирует настроение, вызывает `process_answer`, сохраняет производные артефакты в git-транзакции и отправляет reaction в Telegram. После отправки reaction дописывается assistant event.

## Допущения и границы

Схема показывает один happy path и основные LLM-fallback ветки. Внутренние функции, конкретные классы и тексты prompt-файлов не раскрываются.

## Легенда

Actor: Telegram user; Bot: application orchestration; Raw: canonical session transcript; OpenRouter: primary/fallback LLM; Store: stage artifacts under git_wrap

## Mermaid source

```mermaid
sequenceDiagram
  actor User as Пользователь
  participant TG as Telegram
  participant Bot as Psycho bot
  participant Raw as 00_raw/sessions
  participant Mood as 01_mood
  participant OR as OpenRouter
  participant Store as 00_raw/qna + 02/03
  participant Git as git_wrap

  User->>TG: Ответ на вопрос или reply в сессии
  TG->>Bot: Update message
  Bot->>Bot: Access check и session routing
  Bot->>Raw: Append user event до LLM

  opt OWNER mood pipeline
    Bot->>OR: classify_mood JSON
    OR-->>Bot: PAD, quality, bot_mood
    Bot->>Mood: Append mood event, analysis, timeseries
  end

  Bot->>OR: process_answer JSON primary
  alt Primary доступен и JSON валиден
    OR-->>Bot: observations, reaction, user_delta
  else Primary недоступен или JSON сломан
    Bot->>OR: process_answer JSON fallback
    alt Fallback успешен
      OR-->>Bot: observations, reaction, user_delta
    else Все модели недоступны
      Bot-->>TG: Нейтральная ошибка без stacktrace
      TG-->>User: Короткое сообщение о сбое
    end
  end

  Bot->>Git: before commit
  Bot->>Store: Append qna, draft concepts, profile, personality deltas
  Bot->>Git: after commit или rollback on error
  Bot->>TG: sendMessage reaction
  TG-->>User: Reaction, для OWNER кнопки лица
  Bot->>Raw: Append assistant event
```
