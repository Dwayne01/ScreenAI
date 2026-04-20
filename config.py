import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
AI_PROVIDER: str = os.getenv("AI_PROVIDER", "claude").lower()
AI_PROMPT: str = os.getenv(
    "AI_PROMPT",
    "Describe what you see in this screenshot in detail. Include any text, UI elements, and relevant context.",
)
HOTKEY: str = os.getenv("HOTKEY", "<cmd>+<shift>+s")
AI_MODEL: str = os.getenv("AI_MODEL", "claude-sonnet-4-6")
HOST: str = os.getenv("HOST", "127.0.0.1")
PORT: int = int(os.getenv("PORT", "8765"))
HTTPS_PORT: int = int(os.getenv("HTTPS_PORT", "8766"))
MONITOR_INDEX: int = int(os.getenv("MONITOR_INDEX", "1"))
MAX_DISPLAY_WIDTH: int = int(os.getenv("MAX_DISPLAY_WIDTH", "1920"))
MAX_AI_WIDTH: int = int(os.getenv("MAX_AI_WIDTH", "2048"))
