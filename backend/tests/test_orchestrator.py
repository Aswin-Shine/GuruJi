from unittest.mock import patch

from app.modules.ai_orchestrator import orchestrator
from app.modules.ai_orchestrator.llm import LLMUnavailable, SpendCapExceeded
from app.modules.ai_orchestrator.orchestrator import QueryPlan
from tests.conftest import make_student


def _searching(query="photosynthesis"):
    """Planner mock: 'this is a textbook question, search for <query>'."""
    return patch.object(orchestrator, "plan_query", return_value=QueryPlan(True, query))


def _not_searching():
    return patch.object(orchestrator, "plan_query", return_value=QueryPlan(False, ""))


def test_moderation_blocks_before_model(db):
    _, student, _ = make_student(db)
    with patch.object(orchestrator.llm, "moderate", return_value=True), \
         patch.object(orchestrator.llm, "chat") as mock_chat:
        reply, tokens, tag = orchestrator.orchestrate(db, student.id, 8, "NCERT", "bad message", "")
    mock_chat.assert_not_called()  # flagged input NEVER reaches the tutoring model
    assert tag == "moderation_blocked" and tokens == 0


def test_no_context_requires_uncertainty_acknowledgment(db):
    _, student, _ = make_student(db)
    good = "Yeh abhi aapki textbook mein nahi hai, dost. School topic poochho!"
    bad = "Quantum entanglement is when particles..."
    with _searching("quantum entanglement"), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[]), \
         patch.object(orchestrator.llm, "chat", side_effect=[(bad, 100, 50), (good, 100, 50)]):
        reply, _, _ = orchestrator.orchestrate(db, student.id, 8, "NCERT", "quantum kya hai", "")
    assert "textbook" in reply.lower()  # regenerated until acknowledgment present


def test_non_question_never_triggers_retrieval_or_refusal(db, caplog):
    """the exact defect in the 2026-08-12 screenshot. "answer this in
    english" was embedded as a search query, scored below threshold, and got the
    not-in-your-textbook refusal. The planner must skip retrieval entirely, and the
    prompt must carry the not_needed instruction, not the uncertainty one."""
    import json as _json
    _, student, _ = make_student(db)
    reply_text = "Sure! Pressure is force acting on a unit area. Formula: Pressure = Force / Area."
    captured = {}

    def fake_chat(model, system, user, max_tokens, retry=True):
        captured["system"] = system
        return reply_text, 100, 50

    with caplog.at_level("INFO", logger="guruji.orchestrator"), \
         _not_searching(), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed") as mock_embed, \
         patch.object(orchestrator.curriculum, "retrieve") as mock_retrieve, \
         patch.object(orchestrator.llm, "chat", side_effect=fake_chat):
        reply, _, _ = orchestrator.orchestrate(
            db, student.id, 8, "NCERT", "answer this in english",
            "student: pressure kya hotha hai ?\nassistant: Yeh toh easy hai!",
        )

    mock_embed.assert_not_called()      # no embedding paid for a language request
    mock_retrieve.assert_not_called()   # no retrieval attempted
    assert reply == reply_text          # answered, not refused
    assert "NOT a textbook question" in captured["system"]
    assert "No matching content was found" not in captured["system"]
    line = _json.loads(next(r.message for r in caplog.records if '"grounding"' in r.message))
    assert line["grounding"] == "not_needed"


def test_weak_grounding_answers_with_a_hedge_not_a_refusal(db):
    """A borderline score must produce a hedged answer, not a door slam. The old
    binary flag refused everything under one scalar, punishing exactly the curiosity
    the Founder Discovery Workbook says the product must protect."""
    _, student, _ = make_student(db)
    weak_chunk = orchestrator.curriculum.Chunk(1, "loosely related text", 0.30)
    captured = {}

    def fake_chat(model, system, user, max_tokens, retry=True):
        captured["system"] = system
        return "Yeh exactly tumhare chapter mein nahi hai, but simple jawab yeh hai.", 100, 50

    with _searching("tides moon gravity"), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[weak_chunk]), \
         patch.object(orchestrator.llm, "chat", side_effect=fake_chat):
        reply, _, _ = orchestrator.orchestrate(db, student.id, 8, "NCERT", "tides kyun aate hain", "")

    assert "LOOSELY related" in captured["system"]
    # No uncertainty marker required on the weak path — the reply is not a refusal.
    assert reply.startswith("Yeh exactly")


def test_planner_failure_falls_back_to_raw_message(db):
    """plan_query must fail OPEN: a planner outage degrades the product to the old
    raw-message behaviour rather than breaking the request."""
    with patch.object(orchestrator.llm, "chat", side_effect=LLMUnavailable), \
         patch.object(orchestrator.llm, "record_spend"):
        plan = orchestrator.plan_query(db, "pressure kya hota hai", "")
    assert plan.needs_textbook is True and plan.query == "pressure kya hota hai"


def test_planner_ignores_empty_query_from_model(db):
    """A planner that says 'search' but returns no query must not search for ''."""
    with patch.object(orchestrator.llm, "chat",
                      return_value=('{"needs_textbook": true, "query": ""}', 10, 5)), \
         patch.object(orchestrator.llm, "record_spend"):
        plan = orchestrator.plan_query(db, "iska matlab kya", "")
    assert plan.needs_textbook is True and plan.query == "iska matlab kya"


def test_planner_strips_markdown_fences(db):
    fenced = '```json\n{"needs_textbook": false, "query": ""}\n```'
    with patch.object(orchestrator.llm, "chat", return_value=(fenced, 10, 5)), \
         patch.object(orchestrator.llm, "record_spend"):
        plan = orchestrator.plan_query(db, "hi bhaiya", "")
    assert plan.needs_textbook is False


def test_grounding_classification_boundaries():
    from app.config import RAG_THRESHOLD
    C = orchestrator.curriculum.Chunk
    assert orchestrator._grounding([]) == "empty"
    assert orchestrator._grounding([C(1, "t", RAG_THRESHOLD)]) == "grounded"
    assert orchestrator._grounding([C(1, "t", RAG_THRESHOLD - 0.01)]) == "weak"


def test_spend_cap_returns_budget_fallback(db):
    _, student, _ = make_student(db)
    with patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap", side_effect=SpendCapExceeded):
        reply, _, tag = orchestrator.orchestrate(db, student.id, 8, "NCERT", "hi", "")
    assert tag == "circuit_breaker" and reply == orchestrator.FALLBACK_BUDGET


def test_llm_outage_returns_honest_fallback(db):
    _, student, _ = make_student(db)
    with patch.object(orchestrator.llm, "moderate", side_effect=LLMUnavailable):
        reply, _, tag = orchestrator.orchestrate(db, student.id, 8, "NCERT", "hi", "")
    assert tag == "llm_unavailable" and reply == orchestrator.FALLBACK_UNAVAILABLE


def test_cost_log_uses_actual_token_split(db, caplog):
    """logged cost_usd_est must equal p*input_rate + c*output_rate, not a
    mashed total priced at one rate. plan_query is patched out so the planner's own
    paid call does not pollute the split under test."""
    import json as _json
    from app.config import PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M

    _, student, _ = make_student(db)
    good = "Yeh toh easy hai! Photosynthesis mein plant sunlight use karta hai. Kaunsa part involved hai?"
    with caplog.at_level("INFO", logger="guruji.orchestrator"), \
         _searching(), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve",
                      return_value=[orchestrator.curriculum.Chunk(1, "chunk", 0.9)]), \
         patch.object(orchestrator.llm, "chat", return_value=(good, 1000, 200)):
        orchestrator.orchestrate(db, student.id, 8, "NCERT", "photosynthesis", "")

    line = next(r.message for r in caplog.records if '"cost_usd_est"' in r.message)
    logged = _json.loads(line)
    expected = 1000 * PRICE_INPUT_PER_M / 1e6 + 200 * PRICE_OUTPUT_PER_M / 1e6
    assert abs(logged["cost_usd_est"] - round(expected, 6)) < 1e-9
    assert logged["prompt_tokens"] == 1000 and logged["completion_tokens"] == 200
    assert logged["grounding"] == "grounded"


def test_pii_regex_ignores_ncert_large_numbers():
    """population/area/constant style numbers must NOT trip PII validation."""
    ncert_samples = [
        "India ka area lagbhag 3,287,263 sq km hai.",
        "Avogadro number 6.022 x 10^23 hota hai.",
        "Population 1,210,193 thi 2011 mein.",
        "Distance approx 384400 km hai chand tak.",
        "Speed of light 299792458 m/s hai.",
    ]
    for s in ncert_samples:
        assert orchestrator._validate(s, no_context=False), f"false-positive PII on: {s}"


def test_pii_regex_still_catches_real_phone():
    assert not orchestrator._validate("Call me on +919876543210 dost", no_context=False)
    assert not orchestrator._validate("My number is 9876543210", no_context=False)


def test_pii_regex_catches_spaced_and_dashed_phone():
    """the common written formats 98765 43210 / 98765-43210 must trip the
    PII check too — the old regex only matched a contiguous 10-digit run."""
    assert not orchestrator._validate("Mera number 98765 43210 hai", no_context=False)
    assert not orchestrator._validate("Call 98765-43210 anytime", no_context=False)
    assert not orchestrator._validate("WhatsApp karo +91 98765 43210 pe", no_context=False)


def test_uncertainty_accepts_hinglish_marker():
    """a Hindi-phrased 'not in your book' reply using 'kitaab' must pass."""
    hinglish = "Yeh abhi aapki kitaab mein nahi hai, dost. School wala topic poochho!"
    assert orchestrator._validate(hinglish, no_context=True)
    english = "This isn't in your textbook yet — try a chapter topic!"
    assert orchestrator._validate(english, no_context=True)
    # A confident off-book answer with NO marker still fails.
    assert not orchestrator._validate("Quantum entanglement links two particles.", no_context=True)


def test_weak_grounding_ships_no_citation(db):
    """A citation is a claim the answer came from that chapter.

    The client renders a chapter chip identically whatever score sat behind it, so
    attaching one to a weak match puts the hedge in the prompt text and nowhere the
    student can see. Below the grounded floor the honest answer is no chapter at
    all — which is also what makes the client's "related, not from your chapter"
    state reachable rather than dead code.
    """
    _, student, _ = make_student(db)
    weak = orchestrator.curriculum.Chunk(
        1, "Class 8 Science — Chapter 11: Light\n\nloosely related text", 0.30,
        grade=8, subject="Science", chapter_no=11, title="Light",
    )

    with _searching("periodic table elements"), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[weak]), \
         patch.object(orchestrator.llm, "chat", return_value=("Yeh exactly tumhare chapter mein nahi hai.", 10, 5)):
        result = orchestrator.orchestrate(db, student.id, 8, "NCERT", "periodic table mein kitne elements", "")

    assert result.grounding == "weak"
    assert result.citation is None
    # The excerpt goes too: a passage sheet is the same provenance claim with more
    # detail, and offering "see this in your textbook" for a 0.30 match is worse.
    assert result.source_excerpt is None


def test_grounded_answer_still_ships_its_citation(db):
    """The gate must not cost provenance on the path that has earned it."""
    _, student, _ = make_student(db)
    strong = orchestrator.curriculum.Chunk(
        1, "Class 8 Science — Chapter 11: Light\n\nLight travels in straight lines.", 0.82,
        grade=8, subject="Science", chapter_no=11, title="Light",
    )

    with _searching("light straight lines"), \
         patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[strong]), \
         patch.object(orchestrator.llm, "chat", return_value=("Light seedhi line mein chalti hai!", 10, 5)):
        result = orchestrator.orchestrate(db, student.id, 8, "NCERT", "light kaise chalti hai", "")

    assert result.grounding == "grounded"
    assert result.citation and "Chapter 11" in result.citation
    assert result.source_excerpt
    # The ingester's contextual header must not leak into the quoted passage.
    assert not result.source_excerpt.startswith("Class 8 Science")