"""Retrieval evaluation harness. NOT part of the app — never imported by anything.

This is the missing instrument. Before it existed, every quality claim about GuruJi's
retrieval — including the threshold values in config.py — rested on three hand-probed
similarity scores, and a single bad screenshot could not be attributed to a missing
chapter, a broken query, or a bad threshold. This tells them apart.

Three things are measured, because the pipeline has three distinct failure modes:

  RECALL@K / MRR   Did retrieval find the right chapter?  (retrieval quality)
  GATE ACCURACY    Did the planner correctly skip retrieval for non-questions?
                   This is the metric that would have caught the "answer this in
                   english" -> "not in your textbook" bug before a student saw it.
  REFUSAL ACCURACY For genuinely out-of-corpus questions, did we correctly end up
                   with NO grounding rather than confidently retrieving noise?

Gold labels are CHAPTER numbers, not chunk ids: a chunk-level gold set has to be
rebuilt every time chunking changes, which is exactly when you most need to measure.

Usage (from backend/, with the db container up):

  docker compose run --rm \
    -v "$(pwd):/workspace" -w /workspace/backend \
    -e DATABASE_URL="postgresql+psycopg2://guruji:gurujiadmin@db:5432/guruji" \
    -e OPENAI_API_KEY="$OPENAI_API_KEY" \
    api python eval_retrieval.py

  --no-rewrite   embed the raw student message (the OLD behaviour) for A/B comparison
  --sweep        re-score at several thresholds without re-embedding, to pick
                 RAG_THRESHOLD / RAG_WEAK_THRESHOLD from data instead of three probes
  --grade N      student grade to evaluate as (default 8)
  --verbose      print every miss with its retrieved chapters

COST (measured 2026-08-13, not estimated): embeddings are ~$0.00002 for the whole
set. The planner is not: 129 rows x a 465-token system prompt is roughly $0.09 per
--rewrite pass. That is cheap enough to run daily and NOT cheap enough to ignore --
an earlier draft of this file called it "well under a cent", which was wrong by 50x.
`--no-rewrite` skips the planner entirely and really is free.

Planner calls here pass record=False, so evaluation spend stays out of llm_spend and
does not eat the students' DAILY_SPEND_CAP_USD.
"""
import argparse
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

from sqlalchemy import text

# Puts backend/ on sys.path; must come before any `app.*` import.
import _bootstrap  # noqa: F401  isort:skip

from app.config import LOWEST_GRADE, RAG_LEXICAL_RESCUE, RAG_THRESHOLD, RAG_TOP_K, RAG_WEAK_THRESHOLD
from app.db.session import SessionLocal
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.orchestrator import plan_query
from app.modules.curriculum import service as curriculum

# backend/eval/, not scripts/eval/ — resolved from this file, so the harness runs
# identically from the repo root, from backend/, or from a bind mount.
DEFAULT_SET = Path(__file__).resolve().parent.parent / "eval" / "ncert_grade8_science.csv"


def load_rows(path: Path) -> list[dict]:
    """csv.DictReader has no comment support, so '#' lines are stripped first.
    The rationale for each block of the eval set lives in those comments and is
    worth more than the two lines it costs to keep them parseable."""
    with path.open(newline="", encoding="utf-8") as f:
        body = "".join(line for line in f if not line.lstrip().startswith("#"))
    rows = list(csv.DictReader(io.StringIO(body)))
    for r in rows:
        r["gold_chapter"] = int(r["gold_chapter"])
        # v4: the student's class is a property of the ROW, not of the run. One file
        # now covers all six classes, which is the only way to test that the same
        # question asked as a Class 7 and a Class 10 student resolves to different
        # chapters. --grade remains as the default for rows that omit it.
        r["grade"] = int(r.get("grade") or 0)
    return rows


def evaluate(
    db, rows: list[dict], grade: int, rewrite: bool, verbose: bool, workers: int = 1
) -> dict:
    """One pass. Returns per-row detail that score() and --sweep consume.

    `grade` is the fallback for rows with no grade column of their own.

    `workers` > 1 runs rows concurrently. Each row is one planner call plus one
    embedding call plus two Postgres queries, and the two network calls are ~95% of
    the wall clock — 191 rows serially is ~9 minutes, nearly all of it waiting.

    Safe to parallelise: rows are independent and results are reassembled by index,
    so ordering and every metric are identical to a serial run. Each worker gets its
    OWN SQLAlchemy session — Session is not thread-safe, and sharing one is the
    classic way to turn a speedup into intermittent, unreproducible corruption in
    the one tool whose entire job is to be trustworthy.
    """
    if workers > 1:
        def run(pair: tuple[int, dict]) -> tuple[int, dict]:
            i, row = pair
            local = SessionLocal()
            try:
                return i, _evaluate_row(local, row, grade, rewrite)
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            out = list(pool.map(run, enumerate(rows)))
        return {"detail": [d for _i, d in sorted(out, key=lambda t: t[0])]}

    return {"detail": [_evaluate_row(db, row, grade, rewrite) for row in rows]}


def _evaluate_row(db, row: dict, grade: int, rewrite: bool) -> dict:
    """One row: plan, embed, retrieve via the shipped two-pass path, plus a wide
    query kept only to feed --sweep."""
    if True:
        started = time.time()
        row_grade = row["grade"] or grade
        if rewrite:
            # retry=True: nothing is waiting on an eval, and one timed-out planner
            # call silently degrades a row to raw-message behaviour — which cost
            # ~0.5% of the metric in the 2026-08-16 run for reasons unrelated to
            # retrieval.
            plan = plan_query(db, row["question"], row.get("context", ""),
                              record=False, retry=True)
            needs, query = plan.needs_textbook, plan.query
        else:
            # The pre-fix behaviour: no gate, raw message straight into the embedder.
            needs, query = True, row["question"]

        chapters: list[int] = []
        grades: list[int] = []
        scores: list[float] = []
        lexical: list[bool] = []
        shipped: list[tuple[int, int]] = []
        if needs:
            # Pull candidates with NO threshold applied, so --sweep can re-score the
            # same retrieval at different thresholds without paying to embed again.
            emb = llm.embed(query)

            # THE SHIPPED PATH — curriculum.retrieve(), not search_chunks() directly.
            # A single wide range query looks like a safe upper bound on recall, but it
            # is exactly the flat filter that two-pass replaced, so measuring it scores
            # behaviour that no longer ships and hides the precise failure two-pass
            # exists to prevent.
            shipped = [
                (c.grade, c.chapter_no)
                for c in curriculum.retrieve(db, row_grade, query, emb)
            ]

            embedding_str = "[" + ",".join(f"{v:.8f}" for v in emb) + "]"
            fetched = db.execute(
                text(
                    "SELECT id, chunk_text, subject, grade, chapter_no, title, similarity, rrf_score, lexical_hit "
                    "FROM search_chunks(CAST(:emb AS vector), :q, :gmin, :gmax, :count)"
                ),
                # The eval measures the WIDE pass deliberately: it is the superset,
                # so recall here is an upper bound on what the two-pass path can
                # return. Grade-preference is measured by the gold labels instead —
                # a row whose gold chapter is in another class only scores if the
                # right class was chosen.
                {"emb": embedding_str, "q": query, "gmin": LOWEST_GRADE,
                 "gmax": row_grade, "count": RAG_TOP_K * 3},
            ).all()
            chapters = [r[4] for r in fetched]
            grades = [r[3] for r in fetched]
            scores = [r[6] for r in fetched]
            lexical = [bool(r[8]) for r in fetched]

        return {
            "id": row["id"], "kind": row["kind"], "gold": row["gold_chapter"],
            "grade": row_grade,
            "question": row["question"], "query": query, "needs": needs,
            "chapters": chapters, "grades": grades, "scores": scores, "lexical": lexical,
            # `shipped` is what retrieve() actually returned; chapters/grades/scores
            # are the unfiltered wide candidates, kept ONLY so --sweep can re-score
            # thresholds without paying to embed again.
            "shipped": shipped,
            "latency_ms": int((time.time() - started) * 1000),
        }


def score(detail: list[dict], threshold: float, weak: float, top_k: int) -> dict:
    """Re-scoreable: applies thresholds to an already-retrieved candidate list."""
    searchable = [d for d in detail if d["gold"] > 0]
    nonsearch = [d for d in detail if d["gold"] == 0]
    out_of_corpus = [d for d in detail if d["gold"] == -1]

    hits = 0
    reciprocal_ranks = []
    misses = []
    def _kept_chapters(d: dict, weak_floor: float) -> list[int]:
        """Mirrors curriculum.retrieve()'s keep rule exactly — including the lexical
        rescue — so the eval measures the pipeline that actually ships, not an
        idealised one that only applies the cosine floor."""
        return [
            ch for ch, sc, lex in zip(d["chapters"], d["scores"], d["lexical"] or [False] * len(d["chapters"]))
            if sc > weak_floor or (RAG_LEXICAL_RESCUE and lex)
        ][:top_k]

    for d in searchable:
        # Score the shipped two-pass result. `_kept_chapters` (the wide candidate
        # list) is still used by --sweep, where varying the threshold is the point.
        kept = [c for _g, c in d.get("shipped", [])] or _kept_chapters(d, weak)
        if d["kind"] == "cross_class":
            # Chapter number alone is not enough here: every class has a ch09, so a
            # Class 10 student answered from Class 6 ch09 would score as a hit. The
            # pair (class, chapter) is the only honest test of grade preference.
            pairs = [
                (g, c) for g, c, sc, lex in zip(
                    d.get("grades", []), d["chapters"], d["scores"],
                    d["lexical"] or [False] * len(d["chapters"]))
                if sc > weak or (RAG_LEXICAL_RESCUE and lex)
            ][:top_k]
            kept = [c for g, c in pairs if g == d["grade"]]
            if d.get("shipped"):
                # (class, chapter) pair, not chapter alone: every class has a ch09,
                # so chapter-only matching would score a Class 10 student answered
                # from Class 6 ch09 as a hit.
                kept = [c for g, c in d["shipped"] if g == d["grade"]]
        if d["gold"] in kept:
            hits += 1
            reciprocal_ranks.append(1.0 / (kept.index(d["gold"]) + 1))
        else:
            reciprocal_ranks.append(0.0)
            misses.append(d)

    # Gate: a non-question must NOT trigger retrieval.
    gate_ok = sum(1 for d in nonsearch if not d["needs"])
    # Refusal: an out-of-corpus question must end with no chunk above the GROUNDED bar.
    refusal_ok = sum(
        1 for d in out_of_corpus
        if not d["needs"] or not any(sc >= threshold for sc in d["scores"][:top_k])
    )
    def _hit(d: dict) -> bool:
        """Did the SHIPPED pipeline return the gold chapter for this row?

        Scored against the two-pass result, the same as headline recall. Scoring this
        against the wide single-pass candidate list instead makes the per-kind and
        headline numbers disagree — and a metric that contradicts itself is worse than
        a missing one, because it looks like data.
        """
        kept = [c for _g, c in d.get("shipped", [])] or _kept_chapters(d, weak)
        if d["kind"] == "cross_class":
            pairs = d.get("shipped") or [
                (g, c) for g, c, sc, lex in zip(
                    d.get("grades", []), d["chapters"], d["scores"],
                    d["lexical"] or [False] * len(d["chapters"]))
                if sc > weak or (RAG_LEXICAL_RESCUE and lex)
            ][:top_k]
            # (class, chapter), never chapter alone: every class has a ch09, so a
            # Class 10 student answered from Class 6 ch09 would otherwise score.
            kept = [c for g, c in pairs if g == d["grade"]]
        return d["gold"] in kept

    by_kind: dict[str, list[bool]] = {}
    for d in searchable:
        by_kind.setdefault(d["kind"], []).append(_hit(d))
    for d in nonsearch:
        by_kind.setdefault(d["kind"] + "/gate", []).append(not d["needs"])
    for d in out_of_corpus:
        by_kind.setdefault(d["kind"], []).append(
            not d["needs"] or not any(sc >= threshold for sc in d["scores"][:top_k])
        )

    # The cost the sweep could not previously see. Recall depends only on `weak`,
    # so raising `grounded` looked free — it is not. It pushes correctly-retrieved
    # textbook content out of the confident band into the hedged one, and a student
    # asking a legitimate chapter question then gets "yeh exactly tumhare chapter
    # mein nahi hai". Measured 2026-08-13: real on-topic content scores 0.42-0.58,
    # so a 0.45 threshold sits INSIDE the distribution it is meant to sit above.
    confident = sum(
        1 for d in searchable
        if any(sc >= threshold for sc in d["scores"][:top_k])
    )

    # --verbose used to list ONLY in-corpus misses, which meant the refusal and gate
    # failures — the ones that put a confidently wrong answer in front of a child —
    # were invisible. You could see the metric move and not see what moved it.
    refusal_failures = [
        d for d in out_of_corpus
        if d["needs"] and any(sc >= threshold for sc in d["scores"][:top_k])
    ]
    gate_failures = [d for d in nonsearch if d["needs"]]

    return {
        "by_kind": by_kind,
        "refusal_failures": refusal_failures,
        "gate_failures": gate_failures,
        "confident_rate": confident / len(searchable) if searchable else 0.0,
        "recall_at_k": hits / len(searchable) if searchable else 0.0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "gate_accuracy": gate_ok / len(nonsearch) if nonsearch else 0.0,
        "refusal_accuracy": refusal_ok / len(out_of_corpus) if out_of_corpus else 0.0,
        "n_searchable": len(searchable), "n_nonsearch": len(nonsearch),
        "n_out_of_corpus": len(out_of_corpus), "misses": misses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--grade", type=int, default=8)
    parser.add_argument("--no-rewrite", action="store_true", help="A/B against the pre-fix raw-message behaviour")
    parser.add_argument("--sweep", action="store_true", help="re-score at several thresholds")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="machine-readable output for CI")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="run rows concurrently (8 is a good value; 191 rows drops ~9min -> ~90s). "
             "Metrics are identical to a serial run; only wall clock changes.",
    )
    args = parser.parse_args()

    if not args.set.is_file():
        sys.exit(f"Eval set not found: {args.set}")

    rows = load_rows(args.set)
    db = SessionLocal()
    try:
        corpus = db.execute(text("SELECT count(*) FROM curriculum_chunks")).scalar_one()
        if corpus == 0:
            sys.exit("curriculum_chunks is empty — ingest before evaluating.")
        print(f"Corpus: {corpus} chunks. Eval set: {len(rows)} rows. "
              f"Query rewrite: {'OFF (pre-fix baseline)' if args.no_rewrite else 'ON'}. "
              f"Lexical rescue: {'ON' if RAG_LEXICAL_RESCUE else 'OFF'}\n")

        result = evaluate(db, rows, args.grade, rewrite=not args.no_rewrite,
                          verbose=args.verbose, workers=max(1, args.workers))
        detail = result["detail"]
        main_score = score(detail, RAG_THRESHOLD, RAG_WEAK_THRESHOLD, RAG_TOP_K)

        if args.json:
            printable = {
                k: v for k, v in main_score.items()
                if k not in ("misses", "refusal_failures", "gate_failures")
            }
            printable["by_kind"] = {
                k: round(sum(v) / len(v), 4) for k, v in main_score["by_kind"].items()
            }
            print(json.dumps(printable, indent=2))
        else:
            print(f"Recall@{RAG_TOP_K}      {main_score['recall_at_k']:.1%}   "
                  f"(target >80%)   n={main_score['n_searchable']}")
            print(f"MRR             {main_score['mrr']:.3f}   (target >0.70)")
            print(f"Gate accuracy   {main_score['gate_accuracy']:.1%}   "
                  f"(non-questions correctly skipped)   n={main_score['n_nonsearch']}")
            print(f"Refusal acc.    {main_score['refusal_accuracy']:.1%}   "
                  f"(out-of-corpus correctly ungrounded)   n={main_score['n_out_of_corpus']}")
            print(f"Confident rate  {main_score['confident_rate']:.1%}   "
                  f"(in-corpus questions answered confidently, not hedged)")
            p50 = sorted(d["latency_ms"] for d in detail)[len(detail) // 2]
            print(f"Retrieval p50   {p50} ms")

            # Per-kind, because an aggregate hides exactly what you need to see:
            # the easy term-matching rows carry the average while the vocab_free
            # and adjacent rows — the ones that actually discriminate — fail
            # underneath it. Read this table, not the headline number.
            print(f"\n{'kind':<22}{'score':>8}{'n':>5}")
            for kind, results in sorted(main_score["by_kind"].items()):
                print(f"{kind:<22}{sum(results) / len(results):>7.0%}{len(results):>5}")

        if args.verbose and main_score["misses"]:
            print(f"\n--- {len(main_score['misses'])} retrieval misses (wrong chapter) ---")
            for m in main_score["misses"]:
                print(f"[{m['kind']}] {m['question']!r}")
                print(f"    rewritten -> {m['query']!r}")
                got = m.get("shipped") or list(zip(m.get("grades", [])[:5], m["chapters"][:5]))
                print(f"    asked as Class {m.get('grade','?')}, wanted ch.{m['gold']}, got (class, ch) {got} "
                      f"scores {[round(sc, 3) for sc in m['scores'][:5]]}")

        if args.verbose and main_score["refusal_failures"]:
            print(f"\n--- {len(main_score['refusal_failures'])} FALSE GROUNDINGS "
                  f"(out-of-corpus answered confidently) ---")
            print("    The worst failure this product has: textbook authority asserted for")
            print("    content that is not in the textbook, to a reader who cannot check.")
            for m in main_score["refusal_failures"]:
                # RAG_THRESHOLD, not `threshold`: that name is a parameter of score()
                # and was never in scope here. main() always reports at the configured
                # threshold, which is the one the live app uses.
                over = [round(sc, 3) for sc in m["scores"][:5] if sc >= RAG_THRESHOLD]
                print(f"  {m['question']!r}")
                print(f"    rewritten -> {m['query']!r}")
                print(f"    grounded on ch{m['chapters'][:3]} at {over} "
                      f"(threshold {RAG_THRESHOLD})")

        if args.verbose and main_score["gate_failures"]:
            print(f"\n--- {len(main_score['gate_failures'])} GATE FAILURES "
                  f"(non-question sent to retrieval) ---")
            for m in main_score["gate_failures"]:
                print(f"  {m['question']!r} -> searched for {m['query']!r}")

        if args.sweep:
            print("\n--- threshold sweep (same retrieval, re-scored) ---")
            print(f"{'weak':>6} {'grounded':>9} {'recall':>8} {'mrr':>7} {'refusal':>9} {'confident':>10}")
            for weak in (0.20, 0.25, 0.28, 0.30, 0.35):
                for grounded in (0.35, 0.40, 0.45):
                    if grounded < weak:
                        continue
                    s = score(detail, grounded, weak, RAG_TOP_K)
                    print(f"{weak:>6.2f} {grounded:>9.2f} {s['recall_at_k']:>7.1%} "
                          f"{s['mrr']:>7.3f} {s['refusal_accuracy']:>8.1%} "
                          f"{s['confident_rate']:>9.1%}")
            print("\nRead this as a TRADE, not an optimum. `recall` cannot move with")
            print("`grounded` (it depends only on `weak`), so a rising refusal column with")
            print("flat recall is NOT a free win. `confident` is the price: it is the share")
            print("of real in-corpus questions still answered confidently rather than hedged.")
            print("Every point of refusal accuracy above ~75% is bought with confident-rate.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
