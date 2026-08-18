"""Spend-ledger accounting and circuit-breaker paging.
Runs against the real Postgres from docker compose; rows are cleaned up per test."""
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.config import PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M
from app.modules.ai_orchestrator import llm


@pytest.fixture()
def clean_ledger(db):
    db.execute(text("DELETE FROM llm_spend"))
    db.commit()
    yield
    db.execute(text("DELETE FROM llm_spend"))
    db.commit()


def test_spend_today_matches_split_rate_sum(db, clean_ledger):
    """spend_today_usd must equal the hand-computed input/output split, not
    (prompt+completion) priced at one rate. The old messages-based sum overestimated
    ~4x AND missed memory-summarization spend entirely."""
    llm.record_spend(db, 1000, 200)
    llm.record_spend(db, 4000, 500)
    expected = (
        1000 * PRICE_INPUT_PER_M / 1e6 + 200 * PRICE_OUTPUT_PER_M / 1e6
        + 4000 * PRICE_INPUT_PER_M / 1e6 + 500 * PRICE_OUTPUT_PER_M / 1e6
    )
    wrong_single_rate = (1000 + 200 + 4000 + 500) * PRICE_OUTPUT_PER_M / 1e6
    got = llm.spend_today_usd(db)
    assert abs(got - expected) < 1e-9
    assert abs(got - wrong_single_rate) > 1e-6  # proves it is NOT the old math


def test_breaker_pages_once_per_day_not_once_per_request(db, clean_ledger):
    """two consecutive trips on the same day -> exactly one outbound page.
    The breaker itself still raises on every gated call."""
    # Force a trip: one ledger row comfortably above the cap.
    db.execute(text("INSERT INTO llm_spend (cost_usd) VALUES (1000000)"))
    db.commit()
    with patch.object(llm, "ALERT_WEBHOOK_URL", "https://hooks.example/alert"), \
         patch.object(llm, "_alerted_on", None), \
         patch.object(llm.httpx, "post") as mock_post:
        with pytest.raises(llm.SpendCapExceeded):
            llm.check_spend_cap(db)
        with pytest.raises(llm.SpendCapExceeded):
            llm.check_spend_cap(db)
    assert mock_post.call_count == 1


def test_alert_failure_never_breaks_the_breaker(db, clean_ledger):
    """A dead Slack webhook must not turn the breaker into a 500 — the request still
    gets the honest static fallback path (SpendCapExceeded), nothing else."""
    db.execute(text("INSERT INTO llm_spend (cost_usd) VALUES (1000000)"))
    db.commit()
    with patch.object(llm, "ALERT_WEBHOOK_URL", "https://hooks.example/alert"), \
         patch.object(llm, "_alerted_on", None), \
         patch.object(llm.httpx, "post", side_effect=RuntimeError("network down")):
        with pytest.raises(llm.SpendCapExceeded):
            llm.check_spend_cap(db)
