"""Verify the context-guard fix against the confirmed cross-domain collisions.

WHY THIS SCRIPT EXISTS, AND WHY eval_retrieval.py CANNOT DO THIS JOB
eval_retrieval.py scores retrieval: does a cosine/lexical score cross
RAG_THRESHOLD. It never calls build_tutoring_prompt() or an LLM — "false
grounding" in that harness means a SCORE crossed 0.40, nothing about what the
model would actually say. The context-guard fix changes the GENERATION step,
one layer downstream of anything eval_retrieval.py touches. Re-running that
harness after this fix will report identical false-grounding scores, by
construction — that is not the fix failing, it is the wrong instrument.

This script calls the real pipeline instead: embed -> retrieve (subject-scoped,
matching production) -> classify grounding -> format_for_prompt -> build the
real system prompt -> a real chat() call -> the actual reply text a student
would see. It costs real money (one embedding + one chat completion per case)
and needs a real OPENAI_API_KEY. There is no way to check this for free; the
generation step is exactly the thing under test.

THE SEVEN CASES
Pulled directly from the confirmed false-grounding rows in the Mathematics eval
runs (docs/evaluation-runs.md), not invented. Four are clean cross-domain
collisions the guard's own examples name directly (matrix/distributive-law,
quadratic/square-cube, complex/real, trig/pythagoras). Three are HARDER, more
genuinely adjacent cases the guard was not written to reliably catch — marked
below so a pass or fail on those specifically is informative, not noise:
rational-number (retrieves proportional-reasoning/fractions content that is
real, adjacent number-sense, not a coincidental word match), and the vector/
dot-product row, whose collision mechanism was never fully diagnosed.

READING THE OUTPUT
Each case prints the retrieved chapter, its score, and the actual reply. A
correct reply says plainly that the topic is not in the student's book — using
"textbook" or "kitaab", the same markers UNCERTAINTY_INSTRUCTION checks for —
without going on to explain the wrong concept using the retrieved chunk. A
failing reply explains matrices using distributive-law content, or explains
complex numbers using real-number classification, wearing the cited chapter's
authority for a topic that chapter does not cover.

This is a read-it-yourself check, not a pass/fail assertion. Grading whether a
free-text reply "correctly hedged" is exactly the kind of judgment call that
does not belong in an automated boolean.
"""
import sys

import _bootstrap  # noqa: F401  isort:skip

from app.db.session import SessionLocal
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.orchestrator import _grounding
from app.modules.ai_orchestrator.prompts.tutoring_response import build_tutoring_prompt
from app.modules.curriculum import service as curriculum

CASES = [
    # (question, grade, subject, note)
    ("matrix multiplication kaise karte hain", 8, "Mathematics",
     "clean collision — guard names this exact pair"),
    ("quadratic equation solve kaise karte hain", 8, "Mathematics",
     "clean collision — guard names this exact pair"),
    ("complex number ka matlab kya hota hai", 9, "Mathematics",
     "clean collision — guard names this exact pair"),
    ("trigonometry ke ratios kya hain — sin cos tan", 8, "Mathematics",
     "clean collision — guard names this exact pair"),
    ("trigonometry ke ratios kya hain — sin cos tan", 9, "Mathematics",
     "clean collision, same pair, other grade"),
    ("rational number kya hota hai?", 8, "Mathematics",
     "HARDER — retrieves genuinely adjacent content (fractions/proportional "
     "reasoning), not a coincidental word match. The guard's examples do not "
     "cover this shape; a pass here would be a bonus, not something to expect."),
    ("vector ka dot product kaise nikalte hain", 9, "Mathematics",
     "HARDER — collision mechanism against ch.2 (Linear Polynomials) was never "
     "fully diagnosed. Read this one's retrieved chunk, not just the reply."),
]


def main() -> None:
    db = SessionLocal()
    try:
        for question, grade, subject, note in CASES:
            print("=" * 88)
            print(f"Q ({subject} grade {grade}): {question}")
            print(f"  note: {note}")

            query_emb = llm.embed(question)
            chunks = curriculum.retrieve(db, grade, question, query_emb, subject=subject)
            grounding = _grounding(chunks)
            top = max(chunks, key=lambda c: c.similarity, default=None)

            if top is None:
                print("  retrieved: nothing")
                continue

            print(f"  retrieved: Class {top.grade} · Ch {top.chapter_no}  "
                  f"score={top.similarity:.3f}  grounding={grounding}")

            if grounding != "grounded":
                print(f"  SKIPPED — this run classified it '{grounding}', not "
                      f"'grounded'. The guard only applies to grounded replies; "
                      f"retrieval scores move run to run (see evaluation-runs.md), "
                      f"so this is expected some of the time, not a bug.")
                continue

            context = curriculum.format_for_prompt(chunks)
            system = build_tutoring_prompt(grade, "NCERT", context, "", "", grounding="grounded")
            reply, _p, _c = llm.chat("gpt-5.6-terra", system, question, 500)

            print(f"  REPLY:\n    {reply}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    if not sys.flags.interactive:
        print("Real API calls follow — one embedding + one chat completion per "
              "case, ~7 total. Ctrl-C now to abort.\n")
    main()