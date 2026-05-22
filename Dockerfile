FROM python:3.12-slim

WORKDIR /app

# git нужен как safety-net для записи в vault (см. bot/vault.py:git_wrap).
# Без него бот работает, но без отката изменений при ошибке.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

# Базовые тяжёлые зависимости (вкл. argostranslate) — отдельным слоем РАНЬШЕ
# requirements.txt: пока requirements-base.txt не меняется, этот слой и модель ниже
# берутся из кэша и не перекачиваются при добавлении новых либ в requirements.txt.
COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt

# Argos Translate: модель ru→en на build-стадии, сразу после base (нужен интернет
# ОДИН раз при сборке) → рантайм офлайн, текст пользователя в сеть не уходит. Слой
# кэшируется вместе с base.
RUN python -c "import argostranslate.package as p; p.update_package_index(); pk=next(x for x in p.get_available_packages() if x.from_code=='ru' and x.to_code=='en'); p.install_from_path(pk.download())"

# Лёгкие/часто меняемые зависимости — последним pip-слоем.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
