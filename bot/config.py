import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_PROXY_URL = (os.getenv("TELEGRAM_PROXY_URL") or "").strip()

_AITUNNEL_BASE_URL = "https://api.aitunnel.ru/v1"
_AITUNNEL_PRIMARY_MODEL = "qwen3-235b-a22b-2507"
_AITUNNEL_FALLBACK_MODELS = ("deepseek-v4-flash",)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_PRIMARY_MODEL = "qwen/qwen3-235b-a22b-2507"
_OPENROUTER_FALLBACK_MODELS = ("deepseek/deepseek-v4-flash",)


def _aitunnel_base_url(value: str | None) -> str:
    v = (value or _AITUNNEL_BASE_URL).strip().rstrip("/")
    if v != _AITUNNEL_BASE_URL:
        raise RuntimeError("AITUNNEL_BASE_URL must be https://api.aitunnel.ru/v1")
    return v


def _openrouter_base_url(value: str | None) -> str:
    v = (value or _OPENROUTER_BASE_URL).strip().rstrip("/")
    if not v:
        raise RuntimeError("OPENROUTER_BASE_URL must not be empty")
    return v


def _aitunnel_model_id(model: str) -> str:
    m = (model or "").strip()
    if not m or "/" in m:
        raise RuntimeError(f"LLM model must be an AITunnel model id without provider prefix: {model!r}")
    return m


def _openrouter_model_id(model: str) -> str:
    m = (model or "").strip()
    if not m:
        raise RuntimeError(f"OpenRouter model id must not be empty: {model!r}")
    return m


def _model_id(model: str, provider: str) -> str:
    if provider == "openrouter":
        return _openrouter_model_id(model)
    return _aitunnel_model_id(model)


def _aitunnel_api_key(*, required: bool = True) -> str:
    key = (os.getenv("AITUNNEL_API_KEY") or "").strip()
    if required and not key:
        raise RuntimeError("AITUNNEL_API_KEY is required for AITunnel")
    return key


def _model_from_env(
    value: str | None,
    default: str = _AITUNNEL_PRIMARY_MODEL,
    *,
    provider: str = "aitunnel",
) -> str:
    v = (value or "").strip()
    return _model_id(v or default, provider)


def _parse_model_list(
    value: str | None,
    default: tuple[str, ...] = (),
    *,
    provider: str = "aitunnel",
) -> tuple[str, ...]:
    if value is None:
        return default
    models = tuple(
        _model_id(m, provider)
        for m in (part.strip() for part in value.replace(";", ",").split(","))
        if m
    )
    return models if models else default


OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()
_USE_OPENROUTER = bool(OPENROUTER_API_KEY)

AITUNNEL_BASE_URL = _aitunnel_base_url(None if _USE_OPENROUTER else os.getenv("AITUNNEL_BASE_URL"))
OPENROUTER_BASE_URL = _openrouter_base_url(os.getenv("OPENROUTER_BASE_URL"))
AITUNNEL_API_KEY = _aitunnel_api_key(required=not _USE_OPENROUTER)

LLM_PROVIDER_NAME = "OpenRouter" if _USE_OPENROUTER else "AITunnel"
LLM_BASE_URL = OPENROUTER_BASE_URL if _USE_OPENROUTER else AITUNNEL_BASE_URL
LLM_API_KEY = OPENROUTER_API_KEY if _USE_OPENROUTER else AITUNNEL_API_KEY
_MODEL_PROVIDER = "openrouter" if _USE_OPENROUTER else "aitunnel"
_MODEL_DEFAULT = _OPENROUTER_PRIMARY_MODEL if _USE_OPENROUTER else _AITUNNEL_PRIMARY_MODEL
_MODEL_FALLBACKS = _OPENROUTER_FALLBACK_MODELS if _USE_OPENROUTER else _AITUNNEL_FALLBACK_MODELS


def _model_env(name: str) -> str | None:
    prefix = "OPENROUTER_MODEL" if _USE_OPENROUTER else "LLM_MODEL"
    return os.getenv(f"{prefix}_{name}")


def _fallback_env(name: str) -> str | None:
    if _USE_OPENROUTER:
        return os.getenv(f"OPENROUTER_FALLBACK_{name}") or os.getenv(f"OPENROUTER_MODEL_FALLBACK_{name}")
    return os.getenv(f"LLM_FALLBACK_{name}")


def _openrouter_headers() -> dict[str, str]:
    if not _USE_OPENROUTER:
        return {}
    out: dict[str, str] = {}
    referer = (os.getenv("OPENROUTER_HTTP_REFERER") or "").strip()
    title = (os.getenv("OPENROUTER_APP_TITLE") or "").strip()
    if referer:
        out["HTTP-Referer"] = referer
    if title:
        out["X-OpenRouter-Title"] = title
    return out


LLM_DEFAULT_HEADERS = _openrouter_headers()

LLM_MODEL_DEFAULT = _model_from_env(
    _model_env("DEFAULT"),
    _MODEL_DEFAULT,
    provider=_MODEL_PROVIDER,
)
LLM_MODEL_FALLBACKS = _parse_model_list(
    _model_env("FALLBACKS"),
    _MODEL_FALLBACKS,
    provider=_MODEL_PROVIDER,
)

LLM_MODEL_PROCESS = _model_from_env(_model_env("PROCESS"), LLM_MODEL_DEFAULT, provider=_MODEL_PROVIDER)
LLM_MODEL_MOOD = _model_from_env(_model_env("MOOD"), LLM_MODEL_DEFAULT, provider=_MODEL_PROVIDER)
LLM_MODEL_ASK = _model_from_env(_model_env("ASK"), LLM_MODEL_DEFAULT, provider=_MODEL_PROVIDER)
LLM_MODEL_ABOUT = _model_from_env(_model_env("ABOUT"), LLM_MODEL_DEFAULT, provider=_MODEL_PROVIDER)

LLM_FALLBACK_PROCESS = _parse_model_list(_fallback_env("PROCESS"), LLM_MODEL_FALLBACKS, provider=_MODEL_PROVIDER)
LLM_FALLBACK_MOOD = _parse_model_list(_fallback_env("MOOD"), LLM_MODEL_FALLBACKS, provider=_MODEL_PROVIDER)
LLM_FALLBACK_ASK = _parse_model_list(_fallback_env("ASK"), LLM_MODEL_FALLBACKS, provider=_MODEL_PROVIDER)
LLM_FALLBACK_ABOUT = _parse_model_list(_fallback_env("ABOUT"), LLM_MODEL_FALLBACKS, provider=_MODEL_PROVIDER)

# Таймаут одного LLM-вызова (сек). Без него openai-sdk ждёт ~600 c — при
# зависшем/недоступном provider бот висел бы минутами. По истечении —
# APITimeoutError, хэндлер ловит и отвечает общим сообщением.
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
# Анти-DoS/анти-спам на внешний LLM: минимальный интервал (сек) между
# LLM-операциями одного пользователя. Вместе с single-flight (1 активный вызов
# на пользователя) не даёт одному гостю спамом запросов занять весь контур.
LLM_COOLDOWN_SEC = float(os.getenv("LLM_COOLDOWN_SEC", "4"))

OWNER_TELEGRAM_ID = int(os.environ["OWNER_TELEGRAM_ID"])

# Дополнительные доверенные пользователи (multi-user). Через запятую, например
# "111,222". Владелец добавляется автоматически. Рантайм-добавления — в
# <vault>/.psycho/users.json (см. bot/users.py), env — лишь начальный список.
ALLOWED_TELEGRAM_IDS = tuple(
    int(x) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").replace(" ", "").split(",") if x
)

# Dev/prod режим. DEBUG=true оставляет ручной Telegram-путь рабочим, но по
# умолчанию выключает тяжёлую/фонующую обвязку: git-vault и startup jobs.
DEBUG = _env_bool("DEBUG", False)
VAULT_GIT_ENABLED = _env_bool("VAULT_GIT_ENABLED", not DEBUG)
BACKGROUND_JOBS_ENABLED = _env_bool("BACKGROUND_JOBS_ENABLED", not DEBUG)
STARTUP_RECOVERY_ENABLED = _env_bool("STARTUP_RECOVERY_ENABLED", not DEBUG)

# Уровень логирования (stderr/docker logs). Из env, чтобы поднять до DEBUG без
# пересборки образа. Невалидное значение → INFO (см. main.py).
# ВНИМАНИЕ: на уровне DEBUG в логи попадает персональный контент (например,
# сырой ответ LLM в llm._chat_json). На INFO и выше код контент не пишет —
# только метаданные (uid, q_num, длины). LOG_LEVEL=DEBUG включать осознанно и не на проде.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DAILY_HOUR = int(os.getenv("DAILY_HOUR", "19"))
# Часовой пояс расписания дневного вопроса. По умолчанию МСК (UTC+3, без DST).
DAILY_TZ = os.getenv("DAILY_TZ", "Europe/Moscow")
# Окно вечернего напоминания по сегодняшнему daily-вопросу. Если конец меньше
# старта, окно считается переходящим через полночь (`23:00` → `01:00`).
DAILY_REMINDER_START = os.getenv("DAILY_REMINDER_START", "23:00")
DAILY_REMINDER_END = os.getenv("DAILY_REMINDER_END", "01:00")
VAULT_PATH = Path(os.getenv("VAULT_PATH", "/vault"))
BOOKS_PATH = VAULT_PATH / "books"
BOOK_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
BOOK_EXTRACTED_MAX_BYTES = 60 * 1024 * 1024
UPLOAD_PENDING_SECONDS = 10 * 60

# Темы используются только для навигации `/ask` и metadata raw-события.
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

# Служебная папка внутри vault: whitelist и нейтральный технический лог.
PSYCHO_META_DIR = VAULT_PATH / ".psycho"
LOG_PATH = PSYCHO_META_DIR / "log.md"
