"""Check whether NORMAL, correctly-grounded replies are actually correct — not
whether the system refuses collisions (verify_context_guard.py does that).

WHY THIS IS A DIFFERENT CHECK, NOT MORE OF THE SAME
Every eval run this project has produced measures retrieval: does the top chunk's
chapter match gold_chapter. Six full runs converged around 91-92% recall, and
verify_context_guard.py separately confirmed the model correctly declines to
answer from the wrong chapter on seven hard collision cases. Neither of those
checks the thing this script checks: once retrieval finds the RIGHT chapter and
grounding is genuinely "grounded" — the common case, the one that ships to most
students most of the time — does the model's actual explanation get the maths
right? A 92% recall number and a working refusal guard both say nothing about
whether a worked example's arithmetic is correct, or whether a hint actually
moves a stuck student forward. Nothing built this session has tested that.

WHY THIS DOES NOT TRY TO GRADE ITSELF
Judging whether free text "correctly hedged" was already the wrong job for a
boolean assertion (see verify_context_guard.py's docstring). Judging whether an
explanation is mathematically correct is a HARDER version of the same problem —
a second LLM call to grade the first introduces its own error surface, and
regex-matching for expected numbers is fragile against a model that shows its
working differently each run. So each case below carries the CORRECT ANSWER,
computed independently before running this script, printed next to the model's
reply. That turns "eyeball it and hope" into "eyeball it against a written
expectation" — still a human judgment, but an informed one instead of a blind one.

THREE MECHANICAL CHECKS, KEPT DELIBERATELY SMALL
Word count against the prompt's own 50-word cap, whether the raw "[Chapter N]"
bracket leaked verbatim (an explicit rule violation, not a judgment call), and
whether the reply asks a follow-up question (the pedagogy ladder's Step 1
requirement). All three are cheap, deterministic, and check RULES the prompt
already states — they are not a substitute for reading the reply, only a
tripwire for the subset of failures a human does not need to be present for.

WHAT THIS COSTS
One embedding plus one chat completion per case, nine cases below. Needs a real
OPENAI_API_KEY. There is no free version of this check; the generation step is
exactly the thing under test, same as verify_context_guard.py.
"""
import re
import sys

import _bootstrap  # noqa: F401  isort:skip

from app.db.session import SessionLocal
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.orchestrator import _grounding
from app.modules.ai_orchestrator.prompts.tutoring_response import build_tutoring_prompt
from app.modules.curriculum import service as curriculum

# (question, grade, subject, chapter title for the human, correct answer)
#
# Every "correct answer" here was computed independently, not read off a
# retrieved chunk — the point is to have a reference that does not depend on
# the system under test being right. Deliberately ORDINARY questions: no
# collision vocabulary, no puzzle-framing, the kind of thing a student asks on
# a normal Tuesday. Spread across the three grades with real eval coverage.
CASES = [
    ("1/4 aur 1/2 mein kaunsa bada hai?", 5, "Mathematics", "ch.2 Fractions",
     "1/2 is bigger (0.5 vs 0.25)."),
    ("1000 grams kitne kilograms ke barabar hain?", 5, "Mathematics", "ch.8 Weight and Capacity",
     "1000 g = 1 kg."),
    ("ek pura chakkar kitne degree ka hota hai?", 5, "Mathematics", "ch.3 Angles as Turns",
     "360 degrees."),
    ("2 ki power 3 kitna hota hai?", 8, "Mathematics", "ch.2 Power Play",
     "2^3 = 8."),
    ("50% ko fraction mein kaise likhte hain?", 8, "Mathematics", "ch.8 Fractions in Disguise",
     "1/2 (50/100 simplified)."),
    ("ek right-angled triangle mein dono chhoti sides (legs) 3 cm aur 4 cm hain, "
     "hypotenuse kitni hogi?", 8, "Mathematics", "ch.9 Baudhayana-Pythagoras Theorem",
     "5 cm (the 3-4-5 triple: sqrt(3^2 + 4^2) = sqrt(25) = 5)."),
    ("x + 5 = 0 ka solution kya hai?", 9, "Mathematics", "ch.2 Introduction to Linear Polynomials",
     "x = -5."),
    ("(a+b) ka whole square expand karke dikhao", 9, "Mathematics", "ch.4 Exploring Algebraic Identities",
     "a^2 + 2ab + b^2."),
    ("ek sikke ko uchalne par heads aane ka probability kitna hai?", 9, "Mathematics",
     "ch.7 Introduction to Probability",
     "1/2 (one favourable outcome out of two equally likely ones)."),
]

WORD_LIMIT = 50


def checks(reply: str) -> list[str]:
    """Cheap, deterministic tripwires only — never a substitute for reading the
    reply. Returns a list of rule violations; empty means none of these three
    fired, not that the reply is correct."""
    flags = []
    words = len(reply.split())
    if words > WORD_LIMIT:
        flags.append(f"OVER WORD LIMIT: {words}/{WORD_LIMIT}")
    if re.search(r"\[.*[Cc]hapter.*\]", reply):
        flags.append("LEAKED the raw [Chapter N] bracket verbatim")
    if "?" not in reply:
        flags.append("NO follow-up question found (pedagogy Step 1 requires one)")
    return flags


def main() -> None:
    db = SessionLocal()
    try:
        for question, grade, subject, chapter_label, correct_answer in CASES:
            print("=" * 88)
            print(f"Q ({subject} grade {grade}, expected {chapter_label}): {question}")
            print(f"  CORRECT ANSWER (computed independently): {correct_answer}")

            query_emb = llm.embed(question)
            chunks = curriculum.retrieve(db, grade, question, query_emb, subject=subject)
            grounding = _grounding(chunks)
            top = max(chunks, key=lambda c: c.similarity, default=None)

            if top is None:
                print("  retrieved: nothing — cannot check generation quality on empty retrieval")
                continue

            print(f"  retrieved: Class {top.grade} · Ch {top.chapter_no} \"{top.title}\"  "
                  f"score={top.similarity:.3f}  grounding={grounding}")

            if grounding != "grounded":
                print(f"  SKIPPED — classified '{grounding}', not 'grounded'. This script only "
                      f"checks the grounded-and-answering path; retrieval scores move run to "
                      f"run, so this is expected some of the time.")
                continue

            # Print enough of the actual source chunk to judge FAITHFULNESS, not just
            # correctness in the abstract — a reply can be independently true and still
            # be a bad answer if it does not reflect what this student's book says.
            source_preview = top.chunk_text[:200].replace("\n", " ")
            print(f"  source chunk (first 200 chars): {source_preview}…")

            context = curriculum.format_for_prompt(chunks)
            system = build_tutoring_prompt(grade, "NCERT", context, "", "", grounding="grounded")
            reply, _p, _c = llm.chat("gpt-5.6-terra", system, question, 500)

            print(f"  REPLY:\n    {reply}")

            flags = checks(reply)
            if flags:
                print("  MECHANICAL FLAGS (rule violations, not correctness judgments):")
                for f in flags:
                    print(f"    - {f}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    if not sys.flags.interactive:
        print("Real API calls follow — one embedding + one chat completion per "
              f"case, {len(CASES)} total. Ctrl-C now to abort.\n")
    main()