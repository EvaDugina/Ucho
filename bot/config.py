import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Ollama: API_KEY игнорируется, но клиент openai-sdk требует непустую строку
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ollama") or "ollama"
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://ollama:11434/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen2.5:14b-instruct")

# Таймаут одного LLM-вызова (сек). Без него openai-sdk ждёт ~600 c — при
# зависшей/упавшей Ollama бот висел бы минутами. По истечении — APITimeoutError,
# хэндлер ловит и отвечает общим сообщением. Подобран с запасом под локальную
# 14B на RTX 3060 (генерация JSON концептов).
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
# Анти-DoS на общий GPU: минимальный интервал (сек) между LLM-операциями одного
# пользователя. Вместе с single-flight (1 активный вызов на пользователя) не даёт
# одному гостю спамом вопросов занять видеокарту и заблокировать остальных.
LLM_COOLDOWN_SEC = float(os.getenv("LLM_COOLDOWN_SEC", "4"))

OWNER_TELEGRAM_ID = int(os.environ["OWNER_TELEGRAM_ID"])

# Дополнительные доверенные пользователи (multi-user). Через запятую, например
# "111,222". Владелец добавляется автоматически. Рантайм-добавления — в
# <vault>/.psycho/users.json (см. bot/users.py), env — лишь начальный список.
ALLOWED_TELEGRAM_IDS = tuple(
    int(x) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").replace(" ", "").split(",") if x
)

# Уровень логирования (stderr/docker logs). Из env, чтобы поднять до DEBUG без
# пересборки образа. Невалидное значение → INFO (см. main.py).
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DAILY_HOUR = int(os.getenv("DAILY_HOUR", "19"))
# Часовой пояс расписания дневного вопроса. По умолчанию МСК (UTC+3, без DST).
DAILY_TZ = os.getenv("DAILY_TZ", "Europe/Moscow")
VAULT_PATH = Path(os.getenv("VAULT_PATH", "/vault"))

DOMAINS = (
    "ethics",
    "aesthetics",
    "politics",
    "everyday",
    "relationships",
    "identity",
    "mortality",
    "nationality",
    "knowledge",
    "work",
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Служебная папка внутри vault — наша «приватная» зона.
# Юзер не должен в ней копаться в Obsidian; это manifest + лог + (на будущее)
# migration-proposal.
PSYCHO_META_DIR = VAULT_PATH / ".psycho"
MANIFEST_PATH = PSYCHO_META_DIR / "manifest.json"
LOG_PATH = PSYCHO_META_DIR / "log.md"
