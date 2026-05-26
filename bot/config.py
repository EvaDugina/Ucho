import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_AITUNNEL_BASE_URL = "https://api.aitunnel.ru/v1"
_AITUNNEL_PRIMARY_MODEL = "qwen3-235b-a22b-2507"
_AITUNNEL_FALLBACK_MODELS = ("deepseek-v4-flash",)


def _aitunnel_base_url(value: str | None) -> str:
    v = (value or _AITUNNEL_BASE_URL).strip().rstrip("/")
    if v != _AITUNNEL_BASE_URL:
        raise RuntimeError("AITUNNEL_BASE_URL must be https://api.aitunnel.ru/v1")
    return v


def _aitunnel_model_id(model: str) -> str:
    m = (model or "").strip()
    if not m or "/" in m:
        raise RuntimeError(f"LLM model must be an AITunnel model id without provider prefix: {model!r}")
    return m


def _aitunnel_api_key() -> str:
    key = (os.getenv("AITUNNEL_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("AITUNNEL_API_KEY is required for AITunnel")
    return key


def _model_from_env(value: str | None, default: str = _AITUNNEL_PRIMARY_MODEL) -> str:
    v = (value or "").strip()
    return _aitunnel_model_id(v or default)


def _parse_model_list(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    models = tuple(
        _aitunnel_model_id(m)
        for m in (part.strip() for part in value.replace(";", ",").split(","))
        if m
    )
    return models if models else default


AITUNNEL_BASE_URL = _aitunnel_base_url(os.getenv("AITUNNEL_BASE_URL"))
AITUNNEL_API_KEY = _aitunnel_api_key()

LLM_MODEL_DEFAULT = _model_from_env(os.getenv("LLM_MODEL_DEFAULT"))
LLM_MODEL_FALLBACKS = _parse_model_list(
    os.getenv("LLM_MODEL_FALLBACKS"),
    _AITUNNEL_FALLBACK_MODELS,
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

# Таймаут одного LLM-вызова (сек). Без него openai-sdk ждёт ~600 c — при
# зависшем/недоступном AITunnel бот висел бы минутами. По истечении —
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
# гоняется ТОЛЬКО для владельца, пишет разбор в 01_mood/analysis/ и durable-ряд
# 01_mood/timeseries/. Это экспериментальный режим (OWNER-тестирование) — можно
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
