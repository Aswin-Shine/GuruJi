"""Generation faithfulness harness. NOT part of the app — never imported by anything.

eval_retrieval.py measures retrieval: did the right chapter come back. It never runs
generation, so it cannot see the failure this product calls out as its worst one —
a "grounded" citation attached to a reply whose actual claims did not come from the
retrieved chunk. orchestrator.py attaches that citation whenever the top cosine score
clears RAG_THRESHOLD, independent of what the model then says; the only defense today
is a single prompt clause ("STAY INSIDE THE CONTEXT"). This harness is the missing
check: it runs real generation, then asks a second model to judge the reply against
the context it was supposedly grounded in.

Two things are measured per evaluated row:

  FAITHFUL     did every claim in the reply actually appear in the retrieved context?
  (judge)      Vague teaching language ("let's think about this") is not a claim.

This is deliberately the smallest useful instrument, not a RAGAS-style framework: one
judge call per row, one boolean, one optional reason string. Extend it only once this
minimal version has run and produced a number worth acting on.

WHICH ROWS ARE EVALUATED, by default:
  - every out_of_corpus row (36 in the current set) — the worst-case surface, because
    a false grounding there means textbook authority asserted for content that
    genuinely is not in the textbook.
  - a capped sample of searchable rows that retrieval marks "grounded" (--sample,
    default 20) — a general faithfulness baseline, since a false grounding is not
    only possible on out-of-corpus questions.
Rows where retrieval does not reach "grounded" (weak/empty/not_needed) are skipped:
no citation is shown to the student in those states, so there is nothing to check
faithfulness against.

Usage (from backend/, with the db container up):

  docker compose run --rm \
    -v "$(pwd):/workspace" -w /workspace/backend \
    -e OPENAI_API_KEY="$OPENAI_API_KEY" \
    api python scripts/eval_faithfulness.py --verbose

  --sample N            searchable rows to sample beyond out_of_corpus (default 20)
  --out-of-corpus-only  skip the searchable sample entirely (cheapest run)
  --grade N             fallback grade for rows with no grade column (default 8)
  --verbose             print the judge's reasoning for every unfaithful reply
  --json                machine-readable output

COST: one CHAT_MODEL generation call (real generation, not the cheap planner) plus
one CHEAP_MODEL judge call per evaluated row. Comparable in order of magnitude to a
single eval_retrieval.py pass; not free. Generation here never calls
llm.record_spend(), so evaluation spend does not eat the students' DAILY_SPEND_CAP_USD
(same convention as eval_retrieval.py's record=False planner calls).
"""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

from sqlalchemy import text

# Puts backend/ on sys.path; must come before any `app.*` import.
import _bootstrap  # noqa: F401  isort:skip

from app.config import CHAT_MODEL, CHEAP_MODEL, MAX_OUTPUT_TOKENS, RAG_THRESHOLD
from app.db.session import SessionLocal
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.orchestrator import plan_query
from app.modules.ai_orchestrator.prompts.tutoring_response import build_tutoring_prompt
from app.modules.curriculum import service as curriculum

# backend/eval/, mirrors eval_retrieval.py exactly so both harnesses always test the
# same set unless told otherwise.
DEFAULT_SET = Path(__file__).resolve().parent.parent / "eval" / "ncert_grade8_science.csv"

FAITHFULNESS_JUDGE_SYSTEM = """You are a strict fact-checker reviewing a tutoring AI's reply for a Class 5-10 student.

You are given RETRIEVED CONTEXT (excerpts from the student's own NCERT textbook) and
a REPLY the AI gave, which was shown to the student WITH a chapter citation implying
every fact in it comes from that textbook.

Decide: does the REPLY state any fact, number, name, date, or process that is NOT
present in the RETRIEVED CONTEXT — even if that fact is true in general knowledge?
That is UNFAITHFUL, because the student was told this came from their book.

Generic teaching language is NOT a claim and does not count against faithfulness:
encouragement, restating the question, asking a follow-up, analogies used only to
explain a concept that IS in the context, or saying something is "not in your
textbook yet". Only flag a claim that presents outside information as textbook fact.

Output ONLY a JSON object, no markdown fences, no explanation outside the JSON:
{"faithful": true|false, "unsupported_claim": "<the specific unsupported claim, or null if faithful>"}"""

FAITHFULNESS_JUDGE_USER_TEMPLATE = """RETRIEVED CONTEXT:
{context}

REPLY (shown to the student with a textbook citation):
{reply}

Judge the REPLY."""

JUDGE_MAX_TOKENS = 200


def load_rows(path: Path) -> list[dict]:
    """Mirrors eval_retrieval.py's loader exactly, so both harnesses parse the same
    CSV the same way and cannot silently drift apart on comment-stripping or types."""
    with path.open(newline="", encoding="utf-8") as f:
        body = "".join(line for line in f if not line.lstrip().startswith("#"))
    rows = list(csv.DictReader(io.StringIO(body)))
    for r in rows:
        r["gold_chapter"] = int(r["gold_chapter"])
        r["grade"] = int(r.get("grade") or 0)
    return rows


def _judge(context: str, reply: str) -> dict:
    """One CHEAP_MODEL call. Returns {"faithful": bool|None, "unsupported_claim": str|None}.
    faithful=None means the judge's output did not parse — reported separately from a
    real pass/fail so a parsing bug cannot silently inflate the faithfulness rate."""
    raw, _p, _c = llm.chat(
        CHEAP_MODEL,
        FAITHFULNESS_JUDGE_SYSTEM,
        FAITHFULNESS_JUDGE_USER_TEMPLATE.format(context=context[:4000], reply=reply),
        JUDGE_MAX_TOKENS,
        retry=True,
    )
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        return {
            "faithful": bool(parsed.get("faithful", True)),
            "unsupported_claim": parsed.get("unsupported_claim"),
        }
    except Exception:
        return {"faithful": None, "unsupported_claim": f"judge output unparseable: {raw[:200]!r}"}


def _evaluate_row(db, row: dict, grade: int) -> dict | None:
    """Plans, retrieves, and — only if grounding reaches 'grounded' — generates a real
    reply and judges it. Returns None for a row with nothing to judge (retrieval
    never reached 'grounded', so no citation was ever shown to the student).

    Mirrors orchestrator.orchestrate()'s steps 1-7 but skips moderation (already
    covered by eval_retrieval's planner path and orthogonal to faithfulness) and
    never calls llm.record_spend() / safety.record_flag() / memory writes — this is
    read-only against the live pipeline logic, not a full orchestrate() run, so an
    eval pass leaves no trace in llm_spend or moderation_flags.
    """
    row_grade = row["grade"] or grade
    plan = plan_query(db, row["question"], row.get("context", ""), record=False, retry=True)
    if not plan.needs_textbook:
        return None

    emb = llm.embed(plan.query)
    chunks = curriculum.retrieve(db, row_grade, plan.query, emb)
    top_score = max((c.similarity for c in chunks), default=0.0)
    if not chunks or top_score < RAG_THRESHOLD:
        return None  # not "grounded" — no citation shown, nothing to judge

    retrieved = curriculum.format_for_prompt(chunks)
    system = build_tutoring_prompt(row_grade, "NCERT", retrieved, "(none)", "(first message)", "grounded")
    reply, _p, _c = llm.chat(CHAT_MODEL, system, row["question"], MAX_OUTPUT_TOKENS)

    verdict = _judge(retrieved, reply)
    return {
        "id": row["id"], "kind": row["kind"], "gold": row["gold_chapter"],
        "question": row["question"], "query": plan.query, "grade": row_grade,
        "top_score": top_score, "context": retrieved, "reply": reply,
        **verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--grade", type=int, default=8)
    parser.add_argument("--sample", type=int, default=20, help="searchable rows to sample beyond out_of_corpus")
    parser.add_argument("--out-of-corpus-only", action="store_true", help="skip the searchable sample")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="machine-readable output for CI")
    args = parser.parse_args()

    if not args.set.is_file():
        sys.exit(f"Eval set not found: {args.set}")

    rows = load_rows(args.set)
    out_of_corpus = [r for r in rows if r["gold_chapter"] == -1]
    searchable = [r for r in rows if r["gold_chapter"] > 0][: args.sample] if not args.out_of_corpus_only else []
    target_rows = out_of_corpus + searchable

    db = SessionLocal()
    try:
        corpus = db.execute(text("SELECT count(*) FROM curriculum_chunks")).scalar_one()
        if corpus == 0:
            sys.exit("curriculum_chunks is empty — ingest before evaluating.")
        print(
            f"Corpus: {corpus} chunks. Evaluating {len(out_of_corpus)} out_of_corpus rows"
            f"{'' if args.out_of_corpus_only else f' + {len(searchable)} searchable sample rows'}.\n"
        )

        results = []
        for row in target_rows:
            verdict = _evaluate_row(db, row, args.grade)
            if verdict is not None:
                results.append(verdict)

        judged = [r for r in results if r["faithful"] is not None]
        unparseable = [r for r in results if r["faithful"] is None]
        unfaithful = [r for r in judged if not r["faithful"]]
        oc_unfaithful = [r for r in unfaithful if r["gold"] == -1]

        faithfulness_rate = (len(judged) - len(unfaithful)) / len(judged) if judged else 1.0

        if args.json:
            printable = {
                "n_evaluated": len(results),
                "n_judged": len(judged),
                "n_unparseable": len(unparseable),
                "faithfulness_rate": round(faithfulness_rate, 4),
                "n_unfaithful": len(unfaithful),
                "n_unfaithful_out_of_corpus": len(oc_unfaithful),
                "unfaithful_ids": [r["id"] for r in unfaithful],
            }
            print(json.dumps(printable, indent=2))
        else:
            print(f"Grounded replies evaluated   {len(results)}   "
                  f"(retrieval never reached 'grounded' for the rest — nothing to judge)")
            if unparseable:
                print(f"Judge output unparseable     {len(unparseable)}   (excluded from the rate below)")
            print(f"Faithfulness rate             {faithfulness_rate:.1%}   n={len(judged)}")
            print(f"Unfaithful replies            {len(unfaithful)}   "
                  f"({len(oc_unfaithful)} on out_of_corpus rows — the worst case)")

        if args.verbose and unfaithful:
            print(f"\n--- {len(unfaithful)} UNFAITHFUL REPLIES "
                  f"(cited as grounded, but says something the context doesn't) ---")
            for r in unfaithful:
                print(f"  [{r['kind']}] {r['question']!r}  (top_score={r['top_score']:.3f})")
                print(f"    reply: {r['reply']!r}")
                print(f"    unsupported claim: {r['unsupported_claim']!r}")

        if args.verbose and unparseable:
            print(f"\n--- {len(unparseable)} judge calls returned unparseable output ---")
            for r in unparseable:
                print(f"  [{r['kind']}] {r['question']!r}: {r['unsupported_claim']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
