"""Central config. Every value from env, with dev-safe defaults where harmless."""
import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
DATABASE_URL: str = os.environ.get("DATABASE_URL", "postgresql+psycopg2://postgres:localdevonly@localhost:5432/guruji")

# Explicit model strings, never a floating alias — a silent provider-side default
# change must not alter product behaviour without a deliberate redeploy.
CHAT_MODEL: str = os.environ.get("CHAT_MODEL", "gpt-5.6-terra")
CHEAP_MODEL: str = os.environ.get("CHEAP_MODEL", "gpt-5.6-luna")
EMBEDDING_MODEL: str = "text-embedding-3-small"  # 1536-dim; SAME model at ingest + query time
MODERATION_MODEL: str = "omni-moderation-latest"

# $/1M tokens, for cost logging and the circuit breaker. Planning numbers; re-verify.
PRICE_INPUT_PER_M: float = 2.50
PRICE_OUTPUT_PER_M: float = 15.00

# Sized for current pilot scale (~20 students), not target scale.
DAILY_SPEND_CAP_USD: float = float(os.environ.get("DAILY_SPEND_CAP_USD", "5"))
# Empty = alerting off. The breaker still trips and blocks; alerting is additive.
ALERT_WEBHOOK_URL: str = os.environ.get("ALERT_WEBHOOK_URL", "")
MAX_OUTPUT_TOKENS: int = 500

# ---------------------------------------------------------------------------
# Retrieval tuning. See docs/EVALUATION.md before changing any of these.
# ---------------------------------------------------------------------------
RAG_TOP_K: int = int(os.environ.get("RAG_TOP_K", "5"))

# Floor for the widened second retrieval pass. Matches the schema's own
# CHECK (grade BETWEEN 5 AND 10), so pass 2 spans the whole product range without
# needing to know which grades happen to be ingested yet.
LOWEST_GRADE: int = int(os.environ.get("LOWEST_GRADE", "5"))

# Over-fetch factor before threshold filtering. Without it, a threshold rejection
# leaves fewer than top_k results even when qualifying chunks existed further down.
RAG_CANDIDATE_MULTIPLIER: int = int(os.environ.get("RAG_CANDIDATE_MULTIPLIER", "3"))

# GROUNDED floor: at or above this, answer normally from the textbook.
RAG_THRESHOLD: float = float(os.environ.get("RAG_THRESHOLD", "0.35"))

# WEAK floor: between this and RAG_THRESHOLD, answer briefly with an honest
# "not exactly your chapter" hedge instead of refusing. Set equal to RAG_THRESHOLD
# to disable the weak band and restore binary grounded/refuse behaviour.
RAG_WEAK_THRESHOLD: float = float(os.environ.get("RAG_WEAK_THRESHOLD", "0.28"))

# Keep a chunk the LEXICAL leg matched even when its cosine score is below the weak
# floor: an exact-term match ("cyclone", "electromagnet") is real evidence the dense
# score can miss, and the weak band hedges rather than asserts, so the downside is a
# cautious reply rather than a fabricated one. It is a switch because the eval has
# not settled the question — set to 0 if refusal accuracy drops below ~90%.
RAG_LEXICAL_RESCUE: bool = os.environ.get("RAG_LEXICAL_RESCUE", "1") == "1"

RATE_LIMIT_PER_MIN: int = 20
CONVERSATION_GAP_HOURS: int = 4

# Named, not inlined, because main.py's boot guard compares against these. A
# duplicated string literal would let someone change a default here and silently
# disarm the guard that exists to catch it.
DEFAULT_SECRET_KEY = "dev-only-change-me"
DEFAULT_WHATSAPP_APP_SECRET = "dev-app-secret"
DEFAULT_WHATSAPP_VERIFY_TOKEN = "dev-verify-token"

SECRET_KEY: str = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
WHATSAPP_APP_SECRET: str = os.environ.get("WHATSAPP_APP_SECRET", DEFAULT_WHATSAPP_APP_SECRET)
WHATSAPP_VERIFY_TOKEN: str = os.environ.get("WHATSAPP_VERIFY_TOKEN", DEFAULT_WHATSAPP_VERIFY_TOKEN)

# Fails closed: with the bypass off, otp/verify rejects everything, because real OTP
# delivery does not exist yet. Set DEV_OTP_BYPASS=1 for local dev only.
DEV_OTP_BYPASS: bool = os.environ.get("DEV_OTP_BYPASS", "0") == "1"
# Booting with the bypass on while this is anything but "local" is refused.
APP_ENV: str = os.environ.get("APP_ENV", "local")

# Pilot allow-list. get_or_create_user() provisions a student on first contact and
# rate limiting is per-user, so without this anyone who learns the number gets billed
# tutoring. Empty = open. Replace with invite codes before public launch.
ALLOWED_PHONE_NUMBERS: list[str] = [
    p.strip() for p in os.environ.get("ALLOWED_PHONE_NUMBERS", "").split(",") if p.strip()
]

# Comma-separated exact origins. Empty (default) = closed.
# NEVER "*" — bearer tokens are involved; wildcard plus credentials is a real vulnerability.
ALLOWED_WEB_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("ALLOWED_WEB_ORIGINS", "").split(",") if o.strip()
]