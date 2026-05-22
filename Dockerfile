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

# Dostoevsky ставим с --no-deps (его пин fasttext==0.9.2 не собирается на py3.12;
# бинарники даёт fasttext-wheel из requirements.txt). Модель тянем на build —
# НЕФАТАЛЬНО: если хост модели недоступен, провайдер тональности просто отключится
# в рантайме (bot/sentiment_dvk.py), сборка и бот не падают.
RUN pip install --no-cache-dir --no-deps dostoevsky==0.6.0 \
    && (python -m dostoevsky download fasttext-social-network-model \
        || echo "WARN: dostoevsky model not downloaded — sentiment provider disabled")

COPY bot/ ./bot/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
