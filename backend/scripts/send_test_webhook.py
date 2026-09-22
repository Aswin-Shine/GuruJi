"""Local harness: POSTs a mock WhatsApp-shaped payload with a valid HMAC signature.
Full loop testable without any live Meta integration.

Usage: python scripts/send_test_webhook.py "+919999900001" "Photosynthesis kya hota hai?"

The response body is an ACK — {"status": "accepted"} — not the reply. The reply is
computed after the ack and delivered through Meta's Cloud API. To read it:

  no outbound configured   docker compose logs api | grep outbound_reply
  outbound configured      it arrives on the phone you passed as `phone`

With WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / GRAPH_API_VERSION set, this script
therefore sends a REAL WhatsApp message to `phone`. Use one of the test number's
verified recipients, and remember the 24h window: that phone must have messaged
the business number first, or Meta rejects the send with error 131047.

Env: BASE_URL (default http://localhost:8000), WHATSAPP_APP_SECRET, WAMID (pin it
to a fixed value and re-run to see {"status": "duplicate"}).
"""
import hashlib
import hmac
import json
import os
import sys
import time

import httpx

APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "dev-app-secret")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


def send(phone: str, text: str) -> None:
    payload = {
        "entry": [{"changes": [{"value": {"messages": [
            # An id is present so the harness exercises the dedupe path rather than
            # the missing-id fallback. Re-run with the same id to see "duplicate".
            {"id": os.environ.get("WAMID", f"wamid.local.{int(time.time())}"),
             "from": phone, "type": "text", "text": {"body": text}}
        ]}}]}]
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = httpx.post(
        f"{BASE_URL}/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        timeout=60,
    )
    print(resp.status_code, json.dumps(resp.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    phone = sys.argv[1] if len(sys.argv) > 1 else "+919999900001"
    text = sys.argv[2] if len(sys.argv) > 2 else "Photosynthesis kya hota hai?"
    send(phone, text)
