import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_PRIMARY_MODEL = "qwen/qwen3-235b-a22b-2507"
_OPENROUTER_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"


def _openrouter_base_url(value: str | None) -> str:
    v = (value or _OPENROUTER_BASE_URL).strip().rstrip("/")
    if v != _OPENROUTER_BASE_URL:
        raise RuntimeError("OPENAI_BASE_URL must be https://openrouter.ai/api/v1")
    return v


def _openrouter_model_id(model: str) -> str:
    if "/" not in model:
        raise RuntimeError(f"LLM model must be an OpenRouter id like provider/model: {model!r}")
    return model


def _openrouter_api_key() -> str:
    key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY or OPENROUTER_API_KEY is required for OpenRouter")
    return key


def _model_from_env(value: str | None, default: str = _OPENROUTER_PRIMARY_MODEL) -> str:
    v = (value or "").strip()
    return _openrouter_model_id(v or default)


def _parse_model_list(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    models = tuple(
        _openrouter_model_id(m)
        for m in (part.strip() for part in value.replace(";", ",").split(","))
        if m
    )
    return models if models else default


OPENAI_BASE_URL = _openrouter_base_url(os.getenv("OPENAI_BASE_URL"))
OPENAI_API_KEY = _openrouter_api_key()

LLM_MODEL_DEFAULT = _model_from_env(os.getenv("LLM_MODEL_DEFAULT"))
LLM_MODEL_FALLBACKS = _parse_model_list(
    os.getenv("LLM_MODEL_FALLBACKS"),
    (_OPENROUTER_FALLBACK_MODEL,),
)

LLM_MODEL_PROCESS = _model_from_env(os.getenv("LLM_MODEL_PROCESS"), LLM_MODEL_DEFAULT)
LLM_MODEL_MOOD = _model_from_env(os.getenv("LLM_MODEL_MOOD"), LLM_MODEL_DEFAULT)
LLM_MODEL_PSYCH = _model_from_env(os.getenv("LLM_MODEL_PSYCH"), LLM_MODEL_DEFAULT)
LLM_MODEL_ASK = _model_from_env(os.getenv("LLM_MODEL_ASK"), LLM_MODEL_DEFAULT)
LLM_MODEL_ABOUT = _model_from_env(os.getenv("LLM_MODEL_ABOUT"), LLM_MODEL_DEFAULT)
LLM_MODEL_REACTION = _model_from_env(os.getenv("LLM_MODEL_REACTION"), LLM_MODEL_DEFAULT)

LLM_FALLBACK_PROCESS = _parse_model_list(os.getenv("LLM_FALLBACK_PROCESS"), LLM_MODEL_FALLBACKS)
LLM_FALLBACK_MOOD = _parse_model_list(os.getenv("LLM_FALLBACK_MOOD"), LLM_MODEL_FALLBACKS)
LLM_FALLBACK_PSYCH = _parse_model_list(os.getenv("LLM_FALLBACK_PSYCH"), LLM_MODEL_FALLBACKS)
LLM_FALLBACK_ASK = _parse_model_list(os.getenv("LLM_FALLBACK_ASK"), LLM_MODEL_FALLBACKS)
LLM_FALLBACK_ABOUT = _parse_model_list(os.getenv("LLM_FALLBACK_ABOUT"), LLM_MODEL_FALLBACKS)
LLM_FALLBACK_REACTION = _parse_model_list(os.getenv("LLM_FALLBACK_REACTION"), LLM_MODEL_FALLBACKS)

OPENROUTER_DATA_COLLECTION = os.getenv("OPENROUTER_DATA_COLLECTION", "deny").strip() or "deny"
OPENROUTER_ZDR = os.getenv("OPENROUTER_ZDR", "true").strip().lower() in ("1", "true", "yes")

# Таймаут одного LLM-вызова (сек). Без него openai-sdk ждёт ~600 c — при
# зависшем/недоступном OpenRouter бот висел бы минутами. По истечении —
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

# Уровень логирования (stderr/docker logs). Из env, чтобы поднять до DEBUG без
# пересборки образа. Невалидное значение → INFO (см. main.py).
# ВНИМАНИЕ: на уровне DEBUG в логи попадает персональный контент (например,
# сырой ответ LLM в llm._chat_json). На INFO и выше код контент не пишет —
# только метаданные (uid, q_num, длины). DEBUG включать осознанно и не на проде.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Мульти-методный анализ ответа (сравнение методов оценки настроения/состояния):
# гоняется ТОЛЬКО для владельца, пишет разбор в mood/analysis/ и durable-ряд
# mood/timeseries/. Это экспериментальный режим (OWNER-тестирование) — можно
# выключить без пересборки. false → остаётся только базовый разбор настроения в чат.
ANALYSIS_ENABLED = os.getenv("ANALYSIS_ENABLED", "true").strip().lower() in ("1", "true", "yes")

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
