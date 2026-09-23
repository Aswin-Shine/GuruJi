"""Outbound WhatsApp delivery via Meta's Cloud API.

One job: put a text reply on a student's phone. No tables.

Its own module rather than a helper inside `conversation`, because `identity` will
need the same sender for real OTP delivery, and identity is the dependency root of
docs/ARCHITECTURE.md §2 — it must never import conversation.

Unconfigured (any of the three WHATSAPP_* outbound keys empty) it degrades to
logging the reply, which is exactly what the webhook did before outbound existed.
That keeps local dev and the whole test suite free of any Meta account.
"""
import logging
import time

import httpx

from app.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_GRAPH_API_VERSION, WHATSAPP_PHONE_NUMBER_ID

log = logging.getLogger("guruji.whatsapp")

GRAPH_BASE = "https://graph.facebook.com"
MAX_TEXT_CHARS = 4096  # Meta's hard cap on a text body; replies are ~2000 at most
SEND_TIMEOUT_S = 10
RETRY_DELAY_S = 1.5

# Named because each one calls for a different fix, and an operator reading the
# log at 2am should not need the Meta error-code reference open beside it.
_ERROR_MEANING = {
    131047: "outside the 24h customer-service window — the student must message first",
    130429: "Meta throughput rate limit",
    131026: "message undeliverable — recipient is not on WhatsApp or cannot receive it",
    131030: "recipient not in the test number's allowed list — add it under Step 1 'Try it out' → To",
    131005: "token lacks permission — needs whatsapp_business_messaging AND the WABA assigned to the System User",
    190: "access token invalid or expired — regenerate the System User token",
}


def configured() -> bool:
    return bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_GRAPH_API_VERSION)


def send_text(to: str, body: str) -> bool:
    """Deliver `body` to `to` (wa_id or E.164; the leading + is stripped).

    Returns True on success. NEVER raises: by the time this runs the reply is
    already persisted in `messages`, so a delivery failure is an operations event
    to log, not a reason to unwind the turn. One retry on a transport error or a
    5xx; none on a 4xx, which fails identically 1.5s later.
    """
    if not configured():
        log.info("outbound_reply (log-only, WhatsApp outbound not configured) to=%s reply=%r", to, body)
        return False

    url = _messages_url()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": body[:MAX_TEXT_CHARS]},
    }
    headers = _auth()

    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=SEND_TIMEOUT_S)
        except Exception as exc:
            if attempt == 1:
                log.warning("whatsapp send transport error, retrying once: to=%s %s", to, exc)
                time.sleep(RETRY_DELAY_S)
                continue
            log.error("whatsapp send failed after retry: to=%s %s", to, exc)
            return False

        if resp.status_code < 300:
            log.info("outbound_reply sent to=%s chars=%d", to, len(body))
            return True
        if resp.status_code >= 500 and attempt == 1:
            log.warning("whatsapp send got %d, retrying once: to=%s", resp.status_code, to)
            time.sleep(RETRY_DELAY_S)
            continue
        code, meaning, detail = _explain(resp)
        log.error(
            "whatsapp send failed to=%s http=%s code=%s meaning=%s detail=%s",
            to, resp.status_code, code, meaning, detail,
        )
        return False
    return False


def mark_read_typing(message_id: str) -> None:
    """Blue ticks plus "typing…" on the student's phone while the turn runs.

    A turn takes 5-8s; without this the student sees nothing and resends. Meta
    clears the indicator when the reply lands, or after 25s. Best effort: one
    try, never raises, and never delays the turn for longer than a short timeout."""
    if not configured() or not message_id:
        return
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    try:
        resp = httpx.post(_messages_url(), json=payload, headers=_auth(), timeout=3)
        if resp.status_code >= 300:
            code, meaning, detail = _explain(resp)
            log.warning("typing indicator failed http=%s code=%s meaning=%s", resp.status_code, code, meaning)
    except Exception as exc:
        log.warning("typing indicator failed: %s", exc)


def _messages_url() -> str:
    return f"{GRAPH_BASE}/{WHATSAPP_GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"


def _auth() -> dict:
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}


def _explain(resp) -> tuple[int | None, str, str]:
    """Pull Meta's error code out of a failed response and name what it means."""
    try:
        err = resp.json().get("error") or {}
    except Exception:
        return None, "non-JSON error body", str(getattr(resp, "text", ""))[:200]
    code = err.get("code")
    detail = (err.get("error_data") or {}).get("details") or err.get("message") or ""
    return code, _ERROR_MEANING.get(code, "see Meta's Cloud API error code reference"), str(detail)[:200]
