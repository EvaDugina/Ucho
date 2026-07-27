ARG PYTHON_BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

# git нужен как safety-net для записи в vault (см. bot/vault.py:git_wrap).
# openssh-client нужен для push vault-репозитория через SSH deploy key.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
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

# Тестовые инструменты держим в образе: проект по правилам запускает pytest только
# через Docker, а `docker compose run --rm bot pytest` должен быть самодостаточным.
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY bot/ ./bot/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
COPY tests/ ./tests/
COPY pytest.ini ruff.toml ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
