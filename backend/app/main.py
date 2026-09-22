import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import (
    ALLOWED_PHONE_NUMBERS,
    ALLOWED_WEB_ORIGINS,
    APP_ENV,
    DEFAULT_SECRET_KEY,
    DEFAULT_WHATSAPP_APP_SECRET,
    DEFAULT_WHATSAPP_VERIFY_TOKEN,
    DEV_OTP_BYPASS,
    SECRET_KEY,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_GRAPH_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
)
from app.db.session import get_db, init_db
from app.modules.conversation.router import router as conversation_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.identity.router import router as identity_router
from app.modules.student_profile.router import router as student_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("guruji.boot")


def _check_otp_bypass() -> None:
    """Fail closed: refuse to boot with the universal-code OTP bypass on outside a
    local dev environment. On local dev with bypass on, warn loudly every boot."""
    if DEV_OTP_BYPASS and APP_ENV != "local":
        raise RuntimeError(
            f"DEV_OTP_BYPASS=1 is forbidden when APP_ENV={APP_ENV!r} (not 'local'). "
            "The bypass accepts a fixed universal OTP and must never run outside local dev."
        )
    if DEV_OTP_BYPASS:
        log.warning("SECURITY: DEV_OTP_BYPASS is ON — auth accepts the fixed dev OTP. Local dev only.")


def _check_phone_allowlist() -> None:
    """Warn when the pilot allow-list is empty outside local dev.

    Empty means anyone who finds the number gets billed tutoring. A warning rather
    than a hard refusal, because an open pilot is a legitimate choice."""
    if not ALLOWED_PHONE_NUMBERS and APP_ENV != "local":
        log.warning(
            "COST: ALLOWED_PHONE_NUMBERS is empty and APP_ENV=%s — any phone number that "
            "messages this bot gets provisioned and billed. Set it for a closed pilot.", APP_ENV
        )


def _check_whatsapp_outbound() -> None:
    """Warn — never refuse — when outbound WhatsApp configuration is off for the env.

    Unconfigured outside local dev means every WhatsApp student gets silence: the
    message is processed, the reply is persisted, and nothing reaches the phone.
    That is a legitimate choice for a web-only pilot, so it is a warning, but it
    has to be visible at boot rather than discovered in a transcript review.
    Configured INSIDE local dev is the opposite hazard: a developer's test webhook
    sends a real message to whatever number they typed."""
    keys = (
        ("WHATSAPP_ACCESS_TOKEN", WHATSAPP_ACCESS_TOKEN),
        ("WHATSAPP_PHONE_NUMBER_ID", WHATSAPP_PHONE_NUMBER_ID),
        ("WHATSAPP_GRAPH_API_VERSION", WHATSAPP_GRAPH_API_VERSION),
    )
    missing = [name for name, value in keys if not value]
    if missing and len(missing) < len(keys):
        log.warning(
            "WHATSAPP: %s empty while the other outbound key(s) are set — outbound is "
            "DISABLED until all three are present; replies will only be logged.",
            ", ".join(missing),
        )
    elif missing and APP_ENV != "local":
        log.warning(
            "WHATSAPP: outbound not configured (%s empty) and APP_ENV=%s — inbound "
            "messages are processed but replies are only logged, never delivered.",
            ", ".join(missing), APP_ENV,
        )
    elif not missing and APP_ENV == "local":
        log.warning(
            "WHATSAPP: outbound is configured while APP_ENV=local — this dev instance "
            "will send REAL WhatsApp messages to every number it replies to."
        )


def _check_secrets() -> None:
    """Refuse to boot on a placeholder secret outside local dev.

    Same fail-closed shape as the OTP bypass above. These two got only a comment
    in .env.example before, which is a warning nobody reads at 2am while shipping.

    What each default costs if it reaches a reachable deployment:
      SECRET_KEY           signs every session token AND derives every parent-link
                           PIN, so the default means any user_id is impersonable
                           and any PIN is derivable.
      WHATSAPP_APP_SECRET  the only thing authenticating the webhook, so the
                           default means anyone can sign a payload and send
                           messages as any phone number — which provisions an
                           account and bills tutoring on first contact.
      WHATSAPP_VERIFY_TOKEN lets a stranger complete Meta's subscription handshake.

    All three defaults are published in a public repository, so "obscure" is not a
    property any of them has. A hard refusal, not a warning, because unlike an open
    allow-list there is no legitimate reason to run this way outside local dev.
    """
    placeholders = [
        name
        for name, value, default in (
            ("SECRET_KEY", SECRET_KEY, DEFAULT_SECRET_KEY),
            ("WHATSAPP_APP_SECRET", WHATSAPP_APP_SECRET, DEFAULT_WHATSAPP_APP_SECRET),
            ("WHATSAPP_VERIFY_TOKEN", WHATSAPP_VERIFY_TOKEN, DEFAULT_WHATSAPP_VERIFY_TOKEN),
        )
        if value == default
    ]
    if placeholders and APP_ENV != "local":
        raise RuntimeError(
            f"{', '.join(placeholders)} still at the built-in default while "
            f"APP_ENV={APP_ENV!r} (not 'local'). These defaults are published in a public "
            "repository. Generate each independently with: "
            'python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if placeholders:
        log.warning(
            "SECURITY: %s at built-in default(s) — local dev only, never a deployment.",
            ", ".join(placeholders),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_otp_bypass()
    _check_secrets()
    _check_phone_allowlist()
    _check_whatsapp_outbound()
    init_db()
    yield


app = FastAPI(title="GuruJi API", lifespan=lifespan)
# Closed by default: ALLOWED_WEB_ORIGINS is empty until a real frontend origin is
# configured. Never a wildcard — bearer tokens + "*" with credentials is a real
# vulnerability, not a formality.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(identity_router)
app.include_router(student_router)
app.include_router(conversation_router)
app.include_router(curriculum_router)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness check that actually touches the database.

    A hardcoded 'ok' would let Compose (and later an orchestrator) keep a dead app in
    service. SELECT 1 is the cheapest query there is."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}