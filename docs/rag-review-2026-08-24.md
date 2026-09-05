# RAG pipeline review — changes made, 2026-08-24

Record of what changed after an internal review of the retrieval and generation pipeline (`ecc:rag-pipeline-reviewer`). Four changes: two config fixes, one new evaluation instrument, two documentation additions. Kept as a dated record rather than folded silently into the docs it touches, so the reasoning and the "unmeasured" caveats survive the next person's read.

**Files touched:** `backend/app/config.py`, `.env.example`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md` (edited); `backend/scripts/eval_faithfulness.py` (new, 250 lines).

---

## 1. `RAG_THRESHOLD` default: 0.35 → 0.40

**File:** `backend/app/config.py:43`

```diff
- RAG_THRESHOLD: float = float(os.environ.get("RAG_THRESHOLD", "0.35"))
+ RAG_THRESHOLD: float = float(os.environ.get("RAG_THRESHOLD", "0.40"))
```

`config.py`'s bare default disagreed with `.env.example`, and every recorded evaluation run used 0.40. An environment that never read the example file was silently running an unevaluated, worse-performing threshold.

**Status: measured.** The sweep already on record (`docs/EVALUATION.md` §6) shows 0.40 → 88.9% refusal accuracy vs. 86.1% at 0.35, with recall and MRR unchanged either way.

---

## 2. `RAG_LEXICAL_RESCUE` default: 1 → 0

**File:** `backend/app/config.py:55`

```diff
- RAG_LEXICAL_RESCUE: bool = os.environ.get("RAG_LEXICAL_RESCUE", "1") == "1"
+ RAG_LEXICAL_RESCUE: bool = os.environ.get("RAG_LEXICAL_RESCUE", "0") == "1"
```

The code's own comment states the trigger: *"set to 0 if refusal accuracy drops below ~90%."* The last recorded run measured 88.9%. The condition had fired and the switch had not been flipped — this enacts the codebase's own written policy rather than introducing a new one.

**Status: unmeasured.** No recorded sweep varies this flag — the sweep table in `docs/EVALUATION.md` only ever varies `RAG_THRESHOLD`/`RAG_WEAK_THRESHOLD` at `RAG_LEXICAL_RESCUE=1`. Recall may drop if rescue was carrying real exact-term matches ("cyclone", "electromagnet") that the dense leg missed. **Re-run before trusting this in production:**

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  api python scripts/eval_retrieval.py --verbose --sweep
```

`.env.example` was updated to match, with a dated comment explaining why.

---

## 3. New: generation faithfulness harness

**File:** `backend/scripts/eval_faithfulness.py` (new)

The CRITICAL finding from the review: `orchestrator.py` attaches a chapter citation whenever the top cosine score clears `RAG_THRESHOLD` — independent of whether the model's actual reply stayed inside the retrieved text. The only defense is a single prompt clause (`GROUNDED_INSTRUCTION`'s "STAY INSIDE THE CONTEXT"). Nothing measured this, because `eval_retrieval.py` never runs generation — it scores retrieval only.

The new script runs real generation, then asks a second, cheap model (`CHEAP_MODEL`) to judge each reply against its own retrieved context: does every claim in the reply actually appear in the source excerpt.

**What it does:**
- Runs the shipped pipeline logic (plan → embed → retrieve → build prompt → generate), matching `orchestrator.orchestrate()`'s steps 1–7, skipping only moderation and DB writes so an eval pass leaves no trace in `llm_spend` or `moderation_flags`.
- Skips rows where retrieval never reached `grounded` — weak/empty/not_needed replies never show a citation, so there's no faithfulness claim to check.
- Judges the reply against its retrieved context only, via a strict JSON-output judge prompt.
- Reports judge-output parsing failures separately, never silently folded into a pass.

**Default scope:** all 36 `out_of_corpus` rows (the worst-case surface — a false grounding there means textbook authority asserted for content that genuinely isn't in the textbook) plus a capped sample of 20 `grounded` rows (`--sample`) for a general baseline. Flags (`--sample`, `--out-of-corpus-only`, `--verbose`, `--json`) mirror `eval_retrieval.py`'s conventions.

**Status: instrumented, not yet run.** This sandbox has no DB or `OPENAI_API_KEY`, so no baseline exists yet. Run it and record the result in `docs/evaluation-runs.md` next to the retrieval numbers:

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  api python scripts/eval_faithfulness.py --verbose
```

Full detail in the script's module docstring and in `docs/EVALUATION.md` §9.

---

## 4. Documented: two-pass grade-widening edge case

**File:** `docs/ARCHITECTURE.md` §4 (new subsection, no code change)

Pass 2 can override a pass-1 result that's merely *weak* — not only empty — if a lower, simpler-worded grade scores higher on the wider search. That's the same "simpler prose wins" failure two-pass retrieval exists to prevent, one level removed. The one recorded eval miss (`'temperature kaise naapte hain'`, Class 7 → served Class 6 ch.7) matches this pattern exactly, but the `cross_class` slice is n=33, so one miss isn't enough to call it a trend.

**Status: named, not fixed.** No code change — documented next to the two-pass rationale so it's found on purpose rather than rediscovered as a surprise. Growing the `cross_class` eval slice past 33 rows (process in `docs/EVALUATION.md` §7) is the real next step before trusting the 97% figure either way.

---

## What's still open

1. **Run `eval_faithfulness.py` for the first time** and record the result in `docs/evaluation-runs.md`. Until that exists, the faithfulness gap is instrumented, not closed.
2. **Re-run `eval_retrieval.py --sweep`** with rescue off to get the real refusal-accuracy/recall trade for the new default.
3. **Grow the `cross_class` eval slice** past 33 rows before treating the two-pass grade-widening edge case as settled either way.
4. **Latency review** (handed to `performance-optimizer` by the original review, not started here): retrieval p50 has grown from ~2.0s to ~3.7s and now exceeds the WhatsApp webhook ack window on a synchronous pipeline.

None of the above has been executed in this environment — no live database, no API key, no eval run.
