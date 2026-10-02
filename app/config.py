import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://runner:runner@postgres:5432/runner")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

MAX_STEPS = int(os.environ.get("MAX_STEPS", "10"))
TOOL_TIMEOUT_SECONDS = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "30"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("RETRY_BACKOFF_SECONDS", "1"))

HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "10"))
HEARTBEAT_STALE_SECONDS = float(os.environ.get("HEARTBEAT_STALE_SECONDS", "60"))
REAPER_INTERVAL_SECONDS = int(os.environ.get("REAPER_INTERVAL_SECONDS", "30"))

# "mock" needs no API key; "claude" reads ANTHROPIC_API_KEY from the environment;
# "ollama" talks to any OpenAI-compatible endpoint
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://ollama:11434/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "qwen2.5:3b")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ollama")
MOCK_LLM_DELAY_SECONDS = float(os.environ.get("MOCK_LLM_DELAY_SECONDS", "0"))
