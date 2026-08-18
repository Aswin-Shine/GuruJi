# Evaluation

How retrieval quality is measured, what each number means, how to reproduce it, and where the current measurement is weak.

The harness is `backend/scripts/eval_retrieval.py`. It is never imported by the application.

---

## 1. Why this exists

Before this harness, every quality claim about GuruJi's retrieval — including the threshold constants in `config.py` — rested on three hand-probed similarity scores. A single bad screenshot could not be attributed to a missing chapter, a broken query, or a badly chosen threshold. Those are three different bugs with three different fixes, and guessing between them wastes days.

The harness tells them apart.

---

## 2. What is measured, and why each metric exists

The pipeline has distinct failure modes, so it needs distinct metrics. A single "accuracy" number would average them into meaninglessness.

| Metric | Question it answers | Failure it catches |
|---|---|---|
| **Recall@5** | Did retrieval surface the right chapter in the top 5? | Chunking, embedding, or filtering is broken. |
| **MRR** | How highly did it rank that chapter? | Retrieval finds the answer but buries it under noise. |
| **Gate accuracy** | Did the planner correctly *skip* retrieval for non-questions? | The "answer this in english" → "not in your textbook" bug. This metric is the one that would have caught it before a student saw it. |
| **Refusal accuracy** | For genuinely out-of-corpus questions, did we end up correctly ungrounded? | **False grounding** — textbook authority asserted for content that is not in the textbook, to a reader who cannot check. The worst failure this product has. |
| **Confident rate** | What share of real in-corpus questions were answered confidently rather than hedged? | Over-tightening. A system that refuses everything scores perfectly on refusal accuracy and is useless. |
| **Retrieval p50** | Median retrieval latency | A pipeline that answers correctly but outside the webhook window. |

**Gold labels are chapter numbers, not chunk ids.** A chunk-level gold set has to be rebuilt every time chunking changes — which is precisely when you most need to measure.

---

## 3. The evaluation set

`backend/eval/ncert_grade8_science.csv` — 191 labelled rows. The filename is historical and, at present, more honest than the coverage suggests.

**Columns:** `id, question, context, grade, gold_chapter, kind`

- `grade` — the class the *student* is in when asking. Read per row, so one file tests all six classes.
- `gold_chapter` — `>0` means in-corpus, `0` means a non-question (gate row), `-1` means genuinely absent from the corpus (refusal row).

**Row kinds:**

| Kind | n | Tests |
|---|---|---|
| `hinglish` | 41 | Code-mixed phrasing, the product's actual register |
| `out_of_corpus` | 36 | Refusal accuracy |
| `cross_class` | 33 | Two-pass retrieval — the (class, chapter) pair must both be right |
| `direct` | 24 | Plainly worded in-corpus questions |
| `vocab_free` | 18 | Questions avoiding the chapter's own terminology, so lexical matching cannot carry it |
| `nonsearch` | 14 | Gate accuracy — greetings, acknowledgments, meta-requests |
| `adjacent` | 13 | Topics near a chapter boundary |
| `followup` | 8 | Context-dependent turns |
| `covered` | 5 | Formerly out-of-corpus, relabelled after later ingestion |
| `typo` | 1 | Robustness |

**Grade distribution — read this before quoting any headline number:**

| Class | Rows | Share |
|---|---|---|
| 5 | 0 | 0% |
| 6 | 8 | 4% |
| 7 | 13 | 7% |
| **8** | **157** | **82%** |
| 9 | 5 | 3% |
| 10 | 8 | 4% |

A 99.3% recall figure over that distribution is strong evidence that adding five classes did not regress Class 8. It is thin evidence about Class 9, and no evidence at all about Class 5. **Broadening this set is the highest-value evaluation work available.** A reasonable target is 20 rows per class before any cross-class claim is treated as settled.

---

## 4. Running it

Requires a populated corpus and a real `OPENAI_API_KEY`.

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  api python scripts/eval_retrieval.py --verbose
```

| Flag | Effect |
|---|---|
| `--verbose` | Print every miss and every false grounding, with retrieved chapters and scores |
| `--sweep` | Re-score at several threshold pairs without re-embedding |
| `--no-rewrite` | Skip the query planner, embed the raw message — the pre-planner A/B baseline, and free |
| `--workers N` | Parallelise planner calls |
| `--grade N` | Override the per-row grade column |
| `--set PATH` | Use a different evaluation set |
| `--json` | Machine-readable output |

**Cost:** roughly **$0.09** per full pass, dominated by one query-planner call per row. Embeddings are about $0.00002 for the whole set. `--no-rewrite` skips the planner entirely and is free.

Planner calls in the harness pass `record=False`, so evaluation spend never lands in `llm_spend` and never counts against the students' `DAILY_SPEND_CAP_USD`. They also pass `retry=True`, unlike production: in a live turn a retry doubles the tail a child waits through, but in an evaluation nothing is waiting, and a silently degraded row quietly corrupts a number someone is about to make a decision from.

---

## 5. Current results

Corpus 3,762 chunks, Classes 5–10, 191 rows, query rewrite ON, lexical rescue ON:

```
Recall@5        99.3%    n=141
MRR             0.972
Gate accuracy   100.0%   n=14
Refusal acc.     88.9%   n=36
Confident rate   97.9%
Retrieval p50    3704 ms
```

Per kind:

| Kind | Score | n |
|---|---|---|
| adjacent | 100% | 12 |
| covered | 100% | 5 |
| cross_class | 97% | 33 |
| direct | 100% | 24 |
| followup | 100% | 8 |
| hinglish | 100% | 40 |
| nonsearch / gate | 100% | 14 |
| **out_of_corpus** | **89%** | **36** |
| typo | 100% | 1 |
| vocab_free | 100% | 18 |

Full verbatim output for every recorded run, including earlier corpus sizes: [`evaluation-runs.md`](evaluation-runs.md).

---

## 6. What the current numbers are telling you

### The single retrieval miss

```
[cross_class] 'temperature kaise naapte hain'
  rewritten -> 'temperature measurement thermometer temperature scales'
  asked as Class 7, wanted ch.7, got (class, ch) [(6,7) x5] scores [0.552 … 0.506]
```

Two-pass retrieval preferring a Class 6 chapter over the Class 7 one, at high confidence. The category has n=33, so one miss is 3% — but the sample is too small to know whether this is an outlier or a pattern.

### The four false groundings

```
'periodic table mein kitne elements hain'   -> grounded on ch[8,8,8] at 0.424
'Indian constitution kab bana tha'          -> grounded on ch[11,11,11] at 0.412
'Harappa civilisation ke baare mein batao'  -> grounded on ch[6,2,6] at 0.445
'sandhi viched kaise karte hain'            -> grounded on ch[9,9,9] at 0.424
```

These are not near-misses. Constitutional history, Indus Valley archaeology and Hindi grammar are different subjects entirely, cited to a child with a class and chapter number they have no way to check.

### The sweep says this is a choice, not a defect

```
weak  grounded   recall     mrr   refusal  confident
0.28      0.35   99.3%   0.972    86.1%    100.0%
0.28      0.40   99.3%   0.972    88.9%     97.9%   <- currently shipped
0.28      0.45   99.3%   0.972   100.0%     90.1%
```

Recall and MRR do not move. Raising the grounded floor to 0.45 eliminates **every** false grounding, paid for with 7.8 points of confident rate — roughly one real question in ten getting a hedge instead of a straight answer.

**Read the sweep as a trade, not an optimum.** Recall depends only on the weak floor, so a rising refusal column at flat recall is not a free win. Confident rate is the price.

Two facts that bear on which side to pick:

1. `config.py` documents its own kill switch: *"if eval shows refusal accuracy dropping below ~90%, set `RAG_LEXICAL_RESCUE` to 0."* The last recorded run is 88.9%. The condition has fired and the switch has not been flipped.
2. `RAG_WEAK_THRESHOLD` is **inert** on this set — every value from 0.20 to 0.35 produces bit-identical metrics on all six numbers. The likely mechanism is lexical rescue keeping matches the weak floor would otherwise drop. Until that is confirmed, the weak floor is a documented knob that does nothing.

Neither has been changed in this repository, because both are product decisions rather than cleanup. They are recorded here and in the README so the choice is explicit rather than inherited by accident.

---

## 7. Extending the set

1. Add rows to `backend/eval/ncert_grade8_science.csv` with the correct `grade` and `kind`.
2. **Verify the gold label against the ingested corpus, not from memory.** Three gold labels in an earlier version were wrong in the same way — a topic assigned to the chapter whose *title* matched, when a lower class actually taught it. Retrieval was right every time and the harness reported a miss. Grep the ingested text before labelling.
3. Prefer relabelling over deleting. When Classes 6 and 7 landed, five rows stopped being out-of-corpus; relabelling kept five working test cases measuring something different, where deleting them would have thrown the cases away.
4. Re-run with `--verbose` and read every miss. A miss the harness reports and you cannot explain is either a real regression or a bad label — and you must decide which before recording the number.

---

## 8. What this harness does *not* measure

It measures **retrieval**. It says nothing about:

- Whether the generated Hinglish is pedagogically sound, correctly pitched, or actually guides rather than dumps the answer.
- Whether the persona survives an adversarial 12-year-old.
- Whether students learn anything.

Those need transcript review with real students, which has not happened yet and is a Phase 1 exit criterion. See [ROADMAP.md](ROADMAP.md).
