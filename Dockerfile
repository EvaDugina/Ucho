FROM python:3.12-slim

WORKDIR /app

# git нужен как safety-net для записи в vault (см. bot/vault.py:git_wrap).
# Без него бот работает, но без отката изменений при ошибке.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

# Базовые тяжёлые зависимости — отдельным слоем РАНЬШЕ requirements.txt: пока
# requirements-base.txt не меняется, этот слой берётся из кэша и не перекачивается
# при добавлении новых либ в requirements.txt.
COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Argos Translate: ставим модель ru→en на build-стадии (нужен интернет ОДИН раз при
# сборке) → рантайм полностью офлайн, текст пользователя в сеть не уходит.
RUN python -c "import argostranslate.package as p; p.update_package_index(); pk=next(x for x in p.get_available_packages() if x.from_code=='ru' and x.to_code=='en'); p.install_from_path(pk.download())"

COPY bot/ ./bot/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
