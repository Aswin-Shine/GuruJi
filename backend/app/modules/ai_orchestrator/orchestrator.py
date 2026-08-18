"""The Phase 1 AI pipeline. Synchronous by design — async webhook-ack is a
known Phase 2 requirement once this touches a real Meta webhook under load."""
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import CHAT_MODEL, CHEAP_MODEL, MAX_OUTPUT_TOKENS, RAG_THRESHOLD
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.prompts.query_plan import QUERY_PLAN_SYSTEM, build_query_plan_prompt
from app.modules.ai_orchestrator.prompts.tutoring_response import UNCERTAINTY_MARKERS, build_tutoring_prompt
from app.modules.curriculum import service as curriculum
from app.modules.memory import service as memory
from app.modules.safety import service as safety

log = logging.getLogger("guruji.orchestrator")

FALLBACK_UNAVAILABLE = "Arre yaar, GuruJi ka network abhi thoda slow hai. 5 minute baad phir try karo! 🙏"
FALLBACK_MODERATED = "Yeh sawal main answer nahi kar sakta, dost. Chalo padhai ki baat karte hain — kaunsa chapter chal raha hai?"
FALLBACK_UNSAFE = "Hmm, mujhse yeh theek se explain nahi ho paya. Ek baar phir se poochho, thoda alag words mein?"
FALLBACK_BUDGET = "GuruJi aaj ke liye thak gaya hai! Kal phir milte hain, pakka. 📚"

# A cheap tripwire, NOT the safety system — the moderation API is, and it runs on
# output as well as input. Transliterations are included because the product is Hinglish.
BLOCKED_PHRASES = [
    "kill yourself", "suicide method", "porn", "nude",
    "khudkushi", "aatmahatya", "atmahatya", "nanga", "sexy video",
]

# Bounds the worst-case tail a student waits through. Each model call can burn ~21s
# (10s timeout + 1.5s + 10s retry) and a validation failure buys a second one. Past
# this deadline, skip regeneration and return the safe fallback.
REGEN_DEADLINE_S = 8.0

# PII: a phone number is an actual contact number, not any long digit run. NCERT
# answers are full of large numbers (populations, distances, constants) that must
# NOT trip this. So: match a +CC/91-prefixed mobile, or a bare 10-digit Indian
# mobile (starts 6-9) that is NOT part of a comma-grouped figure and NOT glued to a
# unit like km/cm/kg. Email pattern is unchanged.
_PHONE = re.compile(
    r"(?<![\d,])"                      # not mid comma-grouped number (1,210,193)
    r"(?:\+?91[\-\s]?)?"               # optional +91 / 91
    # 10 digits starting 6-9, optionally split 5+5 (98765 43210) — the common
    # Indian written format.
    r"[6-9]\d{4}[\-\s]?\d{5}"
    r"(?![\d,])"                       # not followed by more digits / grouping
    r"(?!\s?(?:km|cm|mm|kg|g|m|ml|l|°c|k|years|sq))",  # not a measurement
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PII_PATTERNS = [_PHONE, _EMAIL]

MAX_RESPONSE_CHARS = 2000  # ~2x the expected under-50-word budget, generous margin

# The planner emits one short JSON object; 120 tokens is roughly 4x the longest
# legitimate output, so a runaway costs pennies rather than dollars.
QUERY_PLAN_MAX_TOKENS = 120


def _has_uncertainty_marker(lowered: str) -> bool:
    return any(m in lowered for m in UNCERTAINTY_MARKERS)


def _validate(text_out: str, no_context: bool) -> bool:
    if len(text_out) > MAX_RESPONSE_CHARS:
        return False
    lowered = text_out.lower()
    if any(p in lowered for p in BLOCKED_PHRASES):
        return False
    if any(pat.search(text_out) for pat in PII_PATTERNS):
        return False
    if no_context and not _has_uncertainty_marker(lowered):
        return False
    return True


def _output_blocked(db: Session, student_id: uuid.UUID, text_out: str, request_id: str) -> bool:
    """Moderate a generated reply. Persists the flag when it fires."""
    if not llm.moderate(text_out, retry=False):
        return False
    log.warning("output_moderation_flagged request_id=%s student_id=%s", request_id, student_id)
    safety.record_flag(db, student_id, "outbound", text_out)
    return True


@dataclass
class TurnResult:
    """The full result of one tutoring turn.

    Grounding state and provenance travel as named fields so the client never has to
    infer system state by string-matching the reply against the fallback constants.

    `citation` is the single best chapter, not a list. Under-50-word replies cite one
    source or none; a list of five would be clutter carrying no more truth.
    """
    reply: str
    tokens: int
    model_used: str
    grounding: str = "n/a"
    citation: str | None = None
    source_excerpt: str | None = None

    def __iter__(self):
        """Back-compat for positional (reply, tokens, model) unpacking.
        Drop once nothing unpacks the result that way."""
        return iter((self.reply, self.tokens, self.model_used))


@dataclass
class QueryPlan:
    needs_textbook: bool
    query: str


def plan_query(
    db: Session,
    message_text: str,
    last_two_turns: str,
    record: bool = True,
    retry: bool = False,
) -> QueryPlan:
    """Decide whether this message needs the textbook, and rewrite it as a query.

    FAILS OPEN, deliberately. If the cheap model errors, times out, or returns
    unparseable JSON, this returns (True, raw message) — which is exactly the
    behaviour the pipeline had before this function existed. A planner outage
    therefore degrades the product to yesterday's quality instead of breaking it.
    That asymmetry is the whole reason this is a separate function with its own
    try/except rather than inline in orchestrate().

    `retry` is False in production and True only in evaluation. In a live turn a
    retry doubles the tail a child waits through for a nicety, and the fail-open path
    already degrades gracefully. In an evaluation nothing is waiting, and a silently
    degraded row corrupts a number someone is about to decide from.

    `record=False` skips the spend ledger write, and only evaluation passes it.
    Evaluation spend is not student spend: a full pass is ~$0.09, and plan_query does
    not itself call check_spend_cap, so without this an eval could push the day over
    budget and the next real student would get the circuit-breaker fallback with
    nothing in the transcript explaining why.
    """
    fallback = QueryPlan(needs_textbook=True, query=message_text)
    try:
        raw, p_tok, c_tok = llm.chat(
            CHEAP_MODEL,
            QUERY_PLAN_SYSTEM,
            build_query_plan_prompt(last_two_turns, message_text),
            QUERY_PLAN_MAX_TOKENS,
            retry=retry,
        )
        if record:
            llm.record_spend(db, p_tok, c_tok)
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(cleaned)
        needs = bool(parsed.get("needs_textbook", True))
        query = str(parsed.get("query") or "").strip()
        # A planner that says "search" but hands back nothing is not a reason to skip
        # retrieval — fall back to the raw message rather than silently searching "".
        if needs and not query:
            query = message_text
        return QueryPlan(needs_textbook=needs, query=query)
    except Exception as exc:
        log.warning("query planner failed, falling back to raw message: %s", exc)
        return fallback


def _grounding(chunks: list) -> str:
    """Classify retrieval strength as grounded / weak / empty.

    The measured gap between on-topic and off-topic score clusters is only ~0.13 wide,
    too narrow for a clean binary decision. Three states mean a borderline score
    produces a hedged answer rather than a coin flip between confidence and refusal.
    """
    if not chunks:
        return "empty"
    # max(), not chunks[0]: results are ordered by FUSED rank, so the top row is not
    # necessarily the highest-cosine row.
    return "grounded" if max(c.similarity for c in chunks) >= RAG_THRESHOLD else "weak"


def orchestrate(
    db: Session,
    student_id: uuid.UUID,
    grade: int,
    board: str,
    message_text: str,
    last_two_turns: str,
    subject: str | None = None,
) -> TurnResult:
    """Returns a TurnResult. Still unpacks as (reply, tokens, model_used)."""
    request_id = str(uuid.uuid4())
    started = time.time()
    prompt_tokens = 0
    completion_tokens = 0
    top_score = 0.0
    grounding = "n/a"
    search_query = ""
    validation_result = "n/a"
    model_used = "fallback"
    citation: str | None = None
    excerpt: str | None = None
    try:
        # 1. Moderation — BLOCKING. Flagged input never reaches the tutoring model.
        if llm.moderate(message_text):
            log.warning("moderation_flagged request_id=%s student_id=%s", request_id, student_id)
            # Persisted, not just logged — the parent-review promise depends on it.
            safety.record_flag(db, student_id, "inbound", message_text)
            return TurnResult(FALLBACK_MODERATED, 0, "moderation_blocked", "blocked")

        # Circuit breaker gates every paid model call.
        llm.check_spend_cap(db)

        # 2. Query planning. Runs BEFORE retrieval because its whole job is deciding
        # whether retrieval should happen at all, and with what text.
        plan = plan_query(db, message_text, last_two_turns)
        search_query = plan.query

        # 3. Student context (memory summary; profile fields passed in by caller).
        memory_summary = json.dumps(memory.get_summary(db, student_id))

        # 4. Retrieve curriculum — hybrid dense+lexical, grade <= student's grade,
        # no subject pre-filter. Skipped entirely when the planner says this message
        # is not a textbook question.
        if plan.needs_textbook:
            chunks = curriculum.retrieve(db, grade, plan.query, llm.embed(plan.query), subject)
            grounding = _grounding(chunks)
        else:
            chunks = []
            grounding = "not_needed"
        top_score = max((c.similarity for c in chunks), default=0.0)

        # 5. Context sufficiency. Only a genuine retrieval MISS forces the refusal
        # instruction — "not_needed" must never produce a not-in-your-textbook reply.
        no_context = grounding == "empty"
        retrieved = curriculum.format_for_prompt(chunks) if chunks else ""
        # The single highest-similarity chapter, not the fused-rank first row: a
        # lexical-only rescue can top the fused order at cosine 0.0, and citing that
        # chapter to a student would be a confident claim built on a term match.
        best = max(chunks, key=lambda c: c.similarity, default=None)
        # Provenance is attached ONLY when grounding is "grounded". A citation is a
        # claim that the answer came from that chapter, and the client renders it
        # identically whatever the score behind it was — so shipping one for a weak
        # match means the hedge exists in the prompt text and nowhere the student
        # can see. Below the grounded floor the honest answer is no chapter at all,
        # which is also what makes the client's "related, not from your chapter"
        # state reachable instead of dead code.
        cite_ok = grounding == "grounded" and best is not None
        citation = best.citation().strip("[]") if cite_ok and best.citation() else None
        # Strip the contextual header the ingester prepended — the student already
        # sees the chapter on the chip, and repeating it inside the quote reads as
        # a machine artifact rather than a line from their book.
        if cite_ok and best is not None:
            body = best.chunk_text.split("\n\n", 1)[-1].strip()
            excerpt = body[:420].rstrip() + ("…" if len(body) > 420 else "")
        else:
            excerpt = None

        # 6. Assemble prompt.
        system = build_tutoring_prompt(grade, board, retrieved, memory_summary, last_two_turns, grounding)

        # 7. Generate (max_tokens hard cap = cost control, not just formatting).
        text_out, p_tok, c_tok = llm.chat(CHAT_MODEL, system, message_text, MAX_OUTPUT_TOKENS)
        llm.record_spend(db, p_tok, c_tok)  # breaker ledger, correct input/output rate split
        prompt_tokens += p_tok
        completion_tokens += c_tok
        model_used = CHAT_MODEL

        # 8a. Moderate the OUTPUT too. No retry and no regeneration on a flag: a model
        # that produced flagged text gets a fallback, not a second roll of the dice.
        if _output_blocked(db, student_id, text_out, request_id):
            validation_result = "output_moderation_blocked"
            return TurnResult(FALLBACK_UNSAFE, prompt_tokens + completion_tokens, model_used, "blocked")

        # 8b. Validate; one regeneration with stricter instruction, then static fallback.
        if not _validate(text_out, no_context):
            if time.time() - started > REGEN_DEADLINE_S:
                # Out of latency budget — a safe answer now beats a better one in 20s.
                validation_result = "failed_regen_skipped_deadline"
                return TurnResult(FALLBACK_UNSAFE, prompt_tokens + completion_tokens, model_used, "blocked")
            stricter = system + "\n\nSTRICT: Previous reply broke the rules. Under 50 words, no personal data, follow the uncertainty instruction exactly."
            text_out, p_tok, c_tok = llm.chat(CHAT_MODEL, stricter, message_text, MAX_OUTPUT_TOKENS)
            llm.record_spend(db, p_tok, c_tok)  # regeneration is paid too
            prompt_tokens += p_tok
            completion_tokens += c_tok
            # The regenerated reply is a second model output — moderate it too.
            if _output_blocked(db, student_id, text_out, request_id):
                validation_result = "output_moderation_blocked"
                return TurnResult(FALLBACK_UNSAFE, prompt_tokens + completion_tokens, model_used, "blocked")
            if not _validate(text_out, no_context):
                validation_result = "failed_twice"
                return TurnResult(FALLBACK_UNSAFE, prompt_tokens + completion_tokens, model_used, "blocked")
            validation_result = "passed_on_retry"
        else:
            validation_result = "passed"
        return TurnResult(
            text_out, prompt_tokens + completion_tokens, model_used,
            grounding, citation, excerpt,
        )

    except llm.SpendCapExceeded:
        model_used = "circuit_breaker"
        return TurnResult(FALLBACK_BUDGET, 0, model_used, "blocked")
    except llm.LLMUnavailable:
        model_used = "llm_unavailable"
        return TurnResult(FALLBACK_UNAVAILABLE, 0, model_used, "blocked")
    finally:
        # 9. Structured log line per request. Cost uses the ACTUAL prompt/completion
        # split (input rate for prompt, output rate for completion) — matches est_cost,
        # not a mashed total priced at one rate.
        # grounding and search_query are logged because without them a bad reply is
        # undiagnosable — a planner misfire and a retrieval miss look identical.
        log.info(json.dumps({
            "request_id": request_id,
            "student_id": str(student_id),
            "tokens": prompt_tokens + completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd_est": round(llm.est_cost(prompt_tokens, completion_tokens), 6),
            "search_query": search_query,
            "grounding": grounding,
            "subject": subject,
            "citation": citation,
            "retrieval_top_score": round(top_score, 4),
            "latency_ms": int((time.time() - started) * 1000),
            "validation_result": validation_result,
            "model_used": model_used,
        }))