FROM python:3.12-slim

WORKDIR /app

# git нужен как safety-net для записи в vault (см. bot/vault.py:git_wrap).
# Без него бот работает, но без отката изменений при ошибке.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

# Базовые тяжёлые зависимости — отдельным слоем РАНЬШЕ requirements.txt: пока
# requirements-base.txt не меняется, слой берётся из кэша и не перекачивается при
# добавлении новых либ в requirements.txt.
COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt

# Лёгкие/часто меняемые зависимости — последним pip-слоем.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
