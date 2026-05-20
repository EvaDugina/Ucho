import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Ollama: API_KEY игнорируется, но клиент openai-sdk требует непустую строку
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ollama") or "ollama"
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://ollama:11434/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen2.5:14b-instruct")

OWNER_TELEGRAM_ID = int(os.environ["OWNER_TELEGRAM_ID"])
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "20"))
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
