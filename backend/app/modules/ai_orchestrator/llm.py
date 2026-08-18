"""Thin OpenAI wrapper: retry-once, cost math, daily circuit breaker.
Single provider by design — no abstraction layer for a provider we don't have."""
import datetime
import logging
import time

import httpx

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import (
    ALERT_WEBHOOK_URL,
    DAILY_SPEND_CAP_USD,
    EMBEDDING_MODEL,
    MODERATION_MODEL,
    OPENAI_API_KEY,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_PER_M,
)

log = logging.getLogger("guruji.llm")
client = OpenAI(api_key=OPENAI_API_KEY, timeout=10)


class LLMUnavailable(Exception):
    pass


class SpendCapExceeded(Exception):
    pass


def est_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens * PRICE_INPUT_PER_M / 1e6 + completion_tokens * PRICE_OUTPUT_PER_M / 1e6


def record_spend(db: Session, prompt_tokens: int, completion_tokens: int) -> None:
    """Append one row per paid chat call, priced with the correct input/output split.

    Called by both orchestrator chat calls and the memory summarisation call, so no
    paid call is invisible to the breaker. Cheap-model calls are priced at the default
    model's rates — a small overestimate, which is the safe direction for a spend cap."""
    db.execute(
        text("INSERT INTO llm_spend (cost_usd) VALUES (:c)"),
        {"c": est_cost(prompt_tokens, completion_tokens)},
    )
    db.commit()


def spend_today_usd(db: Session) -> float:
    """Sums the llm_spend ledger, not messages.tokens_used.
    Migration caveat (accepted): on the deploy day of this change the running total
    restarts from zero — one day of undercounting, bounded by the cap itself."""
    return db.execute(
        text("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_spend WHERE created_at::date = now()::date")
    ).scalar_one()


# In-process once-per-day alert dedupe. Resets on restart, so worst case is one
# duplicate page per day. A module-level str rather than a table; fine for one container.
_alerted_on: str | None = None


def _page_human(spend: float) -> None:
    """Fire the alert webhook once per calendar day. Never blocks or fails the request."""
    global _alerted_on
    today = datetime.date.today().isoformat()
    if _alerted_on == today or not ALERT_WEBHOOK_URL:
        return
    _alerted_on = today
    try:
        httpx.post(
            ALERT_WEBHOOK_URL,
            json={"text": f"GuruJi CIRCUIT BREAKER TRIPPED: daily spend ${spend:.2f} >= cap ${DAILY_SPEND_CAP_USD:.2f}. Tutoring replies degraded to static fallback until midnight or cap raise."},
            timeout=5,
        )
    except Exception as exc:
        log.error("alert webhook failed (breaker still tripped): %s", exc)


def check_spend_cap(db: Session) -> None:
    spend = spend_today_usd(db)
    if spend >= DAILY_SPEND_CAP_USD:
        # Logged distinctly so a tripped breaker is visible, not buried in normal fallbacks.
        log.error("CIRCUIT_BREAKER_TRIPPED daily_spend_usd=%.2f cap=%.2f", spend, DAILY_SPEND_CAP_USD)
        _page_human(spend)
        raise SpendCapExceeded()


def _retry_once(fn, retry: bool = True):
    """Call `fn`, retrying once unless the caller opts out.

    Retry is opt-out because every retry doubles the worst-case tail (10s timeout +
    1.5s sleep + 10s), and not every call is worth that: a background memory summary is
    better skipped than paid for in latency the student feels."""
    try:
        return fn()
    except Exception as first:
        if not retry:
            log.warning("openai call failed, no retry requested: %s", first)
            raise LLMUnavailable() from first
        log.warning("openai call failed, retrying once: %s", first)
        time.sleep(1.5)
        try:
            return fn()
        except Exception as second:
            log.error("openai call failed after retry: %s", second)
            raise LLMUnavailable() from second


def moderate(text_in: str, retry: bool = True) -> bool:
    """True = flagged. BLOCKING and MANDATORY before any tutoring call.
    If moderation itself is unavailable, fail closed (treat as flagged is too harsh
    for kids asking homework — instead raise, caller returns honest 'try later')."""
    result = _retry_once(lambda: client.moderations.create(model=MODERATION_MODEL, input=text_in), retry=retry)
    return result.results[0].flagged


def chat(model: str, system: str, user: str, max_tokens: int, retry: bool = True) -> tuple[str, int, int]:
    """Returns (text, prompt_tokens, completion_tokens).

    Note: current OpenAI models reject the legacy `max_tokens` param and require
    `max_completion_tokens`. Keeping the arg name `max_tokens` internally so callers
    (orchestrator, memory) are unchanged."""
    resp = _retry_once(
        lambda: client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=max_tokens,
        ),
        retry=retry,
    )
    return (
        resp.choices[0].message.content or "",
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
    )


def embed(text_in: str) -> list[float]:
    resp = _retry_once(lambda: client.embeddings.create(model=EMBEDDING_MODEL, input=text_in))
    return resp.data[0].embedding
