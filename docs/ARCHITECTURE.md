# Architecture

Decisions, boundaries, and — as importantly — what was deliberately not built and what would make it worth building.

Every entry that is a guess is labelled as one. A decision recorded without its reasoning is a decision the next person has to re-litigate from scratch.

---

## 1. Constraints that shaped everything

| Constraint | Consequence |
|---|---|
| One-to-two person team | Modular monolith, not microservices. Nobody can staff seventeen on-call rotations. |
| Near-zero budget | pgvector inside the existing Postgres, no Redis, no queue, no dedicated vector database. |
| Users on budget Android phones, metered data | 40 kB gzip JS ceiling, enforced in CI. CSS-only motion. |
| Users are children | Moderation is blocking and mandatory. Soft delete, not hard delete, so a flagged exchange survives for parent review. |
| Answers must be checkable | Retrieval-grounded only. No web-search fallback anywhere, deliberately. |

---

## 2. Module boundaries

The backend is one deployable organised as `app/modules/<domain>/`. Modules own their own tables. **Modules never import another module's SQLAlchemy models** — cross-module calls go through a public service function. Extracting a module into its own service later therefore means moving a folder and swapping a function call for an HTTP call, not rewriting business logic.

| Module | Owns | Depends on |
|---|---|---|
| `identity` | `users`, `revoked_tokens` | nothing — this is the root |
| `student_profile` | `students`, `parent_links` | `identity` |
| `conversation` | `conversations`, `messages`, `processed_webhook_messages` | `identity`, `student_profile`, `ai_orchestrator` |
| `ai_orchestrator` | no tables — sequencing logic and `llm_spend` writes | `curriculum`, `memory`, `safety` |
| `curriculum` | `curriculum_documents`, `curriculum_chunks` | nothing |
| `memory` | `student_memory` | `student_profile` |
| `safety` | `moderation_flags` | `student_profile` |

**Extraction order, if the team ever grows:** `ai_orchestrator` first (highest CPU and latency variance), then `curriculum` (independent release cadence from ingestion). `identity` and `conversation` stay in the core longest — they are on the critical path for every request, and splitting them first would add latency for no benefit.

---

## 3. Data model

One PostgreSQL 16 instance holds both transactional and vector data. The complete schema, including the `search_chunks()` SQL function, is `backend/app/db/schema.sql`, applied idempotently by `init_db()` at boot.

### Notable decisions

**`schema.sql` drops `search_chunks()` before creating it, rather than using `CREATE OR REPLACE`.** Postgres refuses to replace a function whose output columns changed. The moment a column is added to the result set, `init_db()` throws on every database that already had the old version — the application fails to boot, while a fresh database looks perfectly fine. This was found by applying the file twice against a real Postgres, and is the reason CI runs the suite against a real database rather than a mock.

**`similarity` in the search output is always the true cosine score**, computed even for lexical-only hits. The orchestrator's grounded/weak/empty decision reads it and must not be fooled by a strong lexical rank on a semantically unrelated chunk.

**`chunk_tsv` is a `GENERATED ALWAYS` column**, so adding the lexical retrieval leg backfilled every existing row with no re-embedding and no ingestion run.

**Conversation deletion is soft.** `hidden_at` hides the row from the student; the messages remain. `moderation_flags` and the transcript behind it are the evidence for the parent-review promise, and a child who has just asked something they regret must not be able to erase it by tapping a bin icon.

**No ANN index on `embedding`.** At 3,762 chunks a flat cosine scan is not the bottleneck. Revisit when the corpus passes roughly 100,000 chunks or when retrieval latency is measurably dominated by the scan rather than by the round trip. *Caveat: retrieval p50 has already grown from 2.0 s to 3.7 s across recent corpus increases, and that growth has not been attributed to a specific stage. Attribute it before assuming an index is or is not the answer.* [Guess — not profiled]

---

## 4. The retrieval decision that matters most

Two-pass retrieval exists because a single `grade <= N` filter actively harms the product.

NCERT teaches the same topic repeatedly at increasing depth. Force appears in Classes 6, 8 and 9; light in 6, 7, 8 and 10; electricity in 6, 7, 8 and 10. Under a flat filter, a Class 10 query competes against six classes simultaneously and cosine similarity picks the winner. Similarity has no concept of "more advanced", and simpler prose is often *more* lexically direct — so the Class 6 chunk frequently wins.

The student then receives a confident, correctly cited answer pitched four years below their level. That is worse than a refusal, because nothing about it looks wrong.

The resolution is ordering, not exclusion:

- **Pass 1** — the student's own class only.
- **Pass 2** — their class and below, run only when pass 1 found nothing above the grounded floor.

Pass 2 exists because the product promises a Class 10 student can ask a Class 6 question without judgement. It is a fallback, not the default: own class first, wider only on a miss. The cost is one extra query, incurred only when the student was about to be refused anyway.

### A known edge case in the resolution above

Pass 2 fires whenever pass 1's best score is below `RAG_THRESHOLD` — that includes the *weak* band, not only an empty result — and the final choice (`curriculum/service.py`, `retrieve()`) is a bare `wider_top > own_top` comparison with no preference for the student's own grade. This means a pass-1 chunk that is topically correct but merely weak (say 0.35) can be overridden by a pass-2 chunk from a lower, simpler-worded grade that happens to score higher (say 0.55) — one level removed, but the same mechanism as the "simpler prose wins" failure two-pass retrieval exists to prevent.

The one retrieval miss in the current eval run matches this pattern: `'temperature kaise naapte hain'` asked as Class 7 (gold ch.7) was served Class 6 ch.7 at 0.552, because pass 1 never scored high enough to short-circuit pass 2 (see `docs/EVALUATION.md` §6). The `cross_class` eval slice is n=33, so one miss is 3% — too small a sample to call this settled either way. [Guess — plausible mechanism, not confirmed against a larger sample] Before trusting the 97% `cross_class` figure for anything beyond "did not obviously regress," grow that slice past 33 rows (`docs/EVALUATION.md` §7 has the process).

### Superseded approaches, and why they failed

| Removed | Why |
|---|---|
| `SUBJECT_KEYWORDS` keyword routing | Nine hand-written Science keywords covered none of `pressure`, `coal`, `friction`, `magnet`, `solution` or `eclipse` — six of thirteen ingested topics. A query mentioning "poem" routed to English, which had zero rows, and the student got a refusal. Cosine similarity does subject routing correctly and for free. |
| Strict `grade = N` equality filter | Made the product's own stated promise architecturally impossible. |
| Fixed-character chunking with no headers | 94% of chunks ended mid-sentence, and only 80% of a chapter's chunks contained that chapter's own defining term. Replaced with sentence-aware chunking plus a contextual header on every chunk. |
| Raw message as the vector query | `"answer this in english"`, `"samajh nahi aaya"` and `"hi"` all became vector queries, scored below threshold, and hit the not-in-your-textbook refusal. The query planner exists to prevent exactly this. |

---

## 5. Cost and failure controls

| Control | Mechanism |
|---|---|
| Daily spend circuit breaker | `llm_spend` is an append-only ledger written at the correct input/output rate split. Summed and checked before every paid call, including background memory regeneration. Trips to a static fallback and pages `ALERT_WEBHOOK_URL` once per day. |
| Output token cap | `max_completion_tokens=500` on every call — a cost control, not only a formatting one. |
| Query planner bound | 120 tokens, roughly 4× the longest legitimate output, so a runaway costs pennies. |
| Latency tail bound | Retry is opt-out. Background memory summarisation does not retry; validation-failure regeneration is skipped past an 8-second deadline. A safe answer now beats a better one twenty seconds later. |
| Webhook idempotency | A message id is *claimed* before processing and *released* if processing raises — so a crash means Meta's retry reprocesses, rather than the student's question being silently swallowed. |
| Pilot allow-list | Checked before `get_or_create_user()`, which provisions an account on first contact. Without it, anyone who learns the number gets free billed tutoring. |

**Known miscalibration.** The default `$5/day` cap suits roughly 20 pilot students. Estimated to break somewhere around 150–250 active students. [Guess — extrapolated from per-turn cost, not measured at that scale]

---

## 6. Deliberately not built

Each entry states the trigger that would justify building it. An item with no trigger is not a plan, it is a wish.

| Not built | Build it when |
|---|---|
| Redis / caching | A profiled read path is actually hot. Not before. |
| Message queue, async task queue | The synchronous webhook path measurably drops messages under real load. |
| Second LLM provider | Revenue justifies both the contract and the integration time. An OpenAI outage is currently a full product outage — an accepted risk, stated rather than hidden. |
| Dedicated vector database | Corpus past ~100k chunks, or retrieval latency provably dominated by the scan. |
| Kubernetes | Far past the point where a single container is the constraint. |
| Microservice split | A second person owns a module full time. |
| Staging environment | A paying customer exists to protect from a bad deploy. |
| Event bus | Notification or analytics volume warrants decoupling. When it does, SNS/SQS rather than Kafka — Kafka's operational cost is not justified anywhere near this throughput. |
| Prompt registry | More than one person edits prompts concurrently. Until then git history *is* the version history. |
| Five-table memory taxonomy | The single JSONB row proves too coarse in practice. |

---

## 7. Accepted risks

Stated rather than silently absent. All are appropriate at pilot scale and all become inappropriate the moment real traffic exists.

1. **Single Postgres instance, no replica.** Recovery is a manual restore with an untested RTO.
2. **Single application container.** A crash is visible downtime; `restart: unless-stopped` is the entire self-healing story.
3. **Single LLM provider.** No fallback — the degradation path is an honest failure message, not a different model.
4. **Synchronous webhook.** Retrieval p50 alone (3.7 s) is already larger than Meta's acknowledgment window. The asynchronous ack pattern is Phase 2 and is the correct fix.
5. **In-process rate limiter and PIN attempt counter.** Restart resets them. Correct for one container, wrong the moment there are two.
6. **No boot guard on `SECRET_KEY` or `WHATSAPP_APP_SECRET`.** Both have working defaults, and this repository is public. See [SECURITY.md](SECURITY.md).

---

## 8. Deployment path

Phase 1 is local Docker Compose only. Nothing here terminates TLS; a reverse proxy that does must sit in front before anything faces the internet.

The intended next step is a single small cloud instance behind a tunnel — not a multi-AZ managed estate — because at pilot scale the managed estate costs more than it protects. The step after that is ECS Fargate with a managed Postgres, and the trigger for it is real traffic, not a calendar date. See [ROADMAP.md](ROADMAP.md).
