# Roadmap

What Phase 1 has to prove before Phase 2 starts, what Phase 2 contains, and what is deliberately far away.

Phase boundaries here are defined by **evidence**, not by dates. A phase ends when its exit criteria are met, and none of them are met by writing more code alone.

---

## Phase 1 — validate the core loop *(current)*

**Goal:** find out whether a retrieval-grounded Hinglish tutor actually helps a real student, using the cheapest infrastructure that can answer that question.

### Exit criteria

None of these are complete. All must be true before Phase 2 work begins.

| # | Criterion | State |
|---|---|---|
| 1 | Core tutoring loop runs end to end against a real NCERT corpus | **Done** — 73 chapters, 3,762 chunks, Classes 5–10 |
| 1b | A student can pick the class a chat is for, without overwriting a sibling's profile | **Done** — class pill, `conversations.grade`/`subject` |
| 2 | Retrieval quality is measured, not asserted | **Done** — harness, 191-row labelled set, recorded runs |
| 3 | 15–20 real students have used it for at least a week | **Not started** — blocked by 4 and 5 |
| 4 | Outbound WhatsApp delivery works | **Not started** — the single largest gap |
| 5 | DPDP verifiable parental consent implemented | **Not started** — legal blocker, must precede any real child's data |
| 6 | Transcripts reviewed for factual accuracy and tone | **Not started** — needs 3 |
| 7 | The safety prompt adversarially tested by real children | **Not started** — theoretical red-teaming is not the same as what an actual 12-year-old tries |
| 8 | Real cost-per-conversation and latency numbers from actual usage | **Not started** — every current figure is an estimate |

### Immediate work queue

Ordered by what unblocks the most.

1. **Resolve DPDP parental consent.** Legal, not technical, and it gates everything involving a real child. Nothing below matters if this is unsolved.
2. **Implement outbound WhatsApp send.** `conversation/router.py` currently logs the reply and returns it in the webhook response body; Meta does not deliver that body to the user. One integration against the WhatsApp Cloud API unblocks criteria 3, 4, 6, 7 and 8 simultaneously.
3. **Settle the grounded threshold.** `config.py` says 0.35, `.env.example` says 0.40, the recorded runs used 0.40, and the sweep shows 0.45 eliminates every false grounding at unchanged recall. Pick one, record why, and make the three sources agree.
4. **Investigate the inert weak threshold.** `RAG_WEAK_THRESHOLD` produces identical metrics across its whole tested range. Either it does nothing and should be removed, or lexical rescue is masking it and that interaction should be documented.
5. **Broaden the evaluation set.** 20 rows per class before any cross-class claim is treated as settled. Currently 82% Class 8, zero Class 5.
6. **Attribute the retrieval latency growth.** p50 has moved 2,045 ms → 3,704 ms across two corpus increases and nobody has profiled which stage owns it.

---

## Phase 2 — make it reachable and reliable

Starts only when Phase 1 exits. Triggered by evidence from real students, not by a calendar.

**Channel**
- Asynchronous webhook acknowledgment. Retrieval p50 alone already exceeds Meta's window; the synchronous pipeline will drop messages under real load. Acknowledge immediately, push the reply when ready.
- Real OTP delivery over WhatsApp, replacing the dev bypass.
- Refresh-token rotation.

**Operations**
- Deployment behind a TLS terminator. The intended first step is a single small instance behind a tunnel — not a multi-AZ managed estate, which at pilot scale costs more than it protects.
- A tested restore from `pg_dump`. Untested backups are not backups.
- Retention and cleanup jobs for `revoked_tokens`, `processed_webhook_messages` and `moderation_flags`.
- A staging environment, once a paying customer exists to protect from a bad deploy.

**Product**
- Parent dashboard beyond the summary endpoint.
- Three-question onboarding (name, class, topic of interest), which needs per-user turn-state tracking.
- Quiz generation and grading.

**Quality**
- Generation quality evaluation, not just retrieval. The current harness measures whether the right chapter was found; it says nothing about whether the Hinglish that follows is pedagogically sound.

---

## Phase 3+ — deliberately far away

Each of these has a trigger. An item without a trigger is a wish, not a plan.

| Item | Trigger |
|---|---|
| ECS Fargate, managed Postgres, multi-AZ | Real traffic that a single instance cannot serve |
| Redis / caching | A profiled read path that is genuinely hot |
| Message queue, event bus (SNS/SQS, not Kafka) | Notification or analytics volume that warrants decoupling |
| Second LLM provider | Revenue that justifies the contract and the integration time |
| Dedicated vector database | Corpus past ~100k chunks, or latency provably dominated by the scan |
| Microservice extraction | A second person owning a module full time |
| Kubernetes | Far past the point where a single container is the constraint |
| Teacher-facing tooling | Explicitly out of scope for the first six months |
| Multilingual UI beyond response-language adaptation | Demand evidenced by real users |
| Voice interaction, offline mode | Long-term vision, not a near-term build |

---

## Things that will not be built

- **Web-search fallback.** Not deferred — rejected. Answering from the open internet is the exact behaviour this product exists to avoid, and adding it would dissolve the only claim that distinguishes GuruJi from any other chatbot.
- **Photo uploads for minors.** No object store, no image moderation, and no lawful basis under DPDP.
- **Raw transcript access for parents by default.** Summaries and flagged exchanges only. A child who believes every word is read will stop asking the questions that matter.# Roadmap

What Phase 1 has to prove before Phase 2 starts, what Phase 2 contains, and what is deliberately far away.

Phase boundaries here are defined by **evidence**, not by dates. A phase ends when its exit criteria are met, and none of them are met by writing more code alone.

---

## Phase 1 — validate the core loop *(current)*

**Goal:** find out whether a retrieval-grounded Hinglish tutor actually helps a real student, using the cheapest infrastructure that can answer that question.

### Exit criteria

None of these are complete. All must be true before Phase 2 work begins.

| # | Criterion | State |
|---|---|---|
| 1 | Core tutoring loop runs end to end against a real NCERT corpus | **Done** — 73 chapters, 3,762 chunks, Classes 5–10 |
| 1b | A student can pick the class a chat is for, without overwriting a sibling's profile | **Done** — class pill, `conversations.grade`/`subject` |
| 2 | Retrieval quality is measured, not asserted | **Done** — harness, 191-row labelled set, recorded runs |
| 3 | 15–20 real students have used it for at least a week | **Not started** — blocked by 4 and 5 |
| 4 | Outbound WhatsApp delivery works | **Not started** — the single largest gap |
| 5 | DPDP verifiable parental consent implemented | **Not started** — legal blocker, must precede any real child's data |
| 6 | Transcripts reviewed for factual accuracy and tone | **Not started** — needs 3 |
| 7 | The safety prompt adversarially tested by real children | **Not started** — theoretical red-teaming is not the same as what an actual 12-year-old tries |
| 8 | Real cost-per-conversation and latency numbers from actual usage | **Not started** — every current figure is an estimate |

### Immediate work queue

Ordered by what unblocks the most.

1. **Resolve DPDP parental consent.** Legal, not technical, and it gates everything involving a real child. Nothing below matters if this is unsolved.
2. **Implement outbound WhatsApp send.** `conversation/router.py` currently logs the reply and returns it in the webhook response body; Meta does not deliver that body to the user. One integration against the WhatsApp Cloud API unblocks criteria 3, 4, 6, 7 and 8 simultaneously.
3. **Settle the grounded threshold.** `config.py` says 0.35, `.env.example` says 0.40, the recorded runs used 0.40, and the sweep shows 0.45 eliminates every false grounding at unchanged recall. Pick one, record why, and make the three sources agree.
4. **Investigate the inert weak threshold.** `RAG_WEAK_THRESHOLD` produces identical metrics across its whole tested range. Either it does nothing and should be removed, or lexical rescue is masking it and that interaction should be documented.
5. **Broaden the evaluation set.** 20 rows per class before any cross-class claim is treated as settled. Currently 82% Class 8, zero Class 5.
6. **Attribute the retrieval latency growth.** p50 has moved 2,045 ms → 3,704 ms across two corpus increases and nobody has profiled which stage owns it.

---

## Phase 2 — make it reachable and reliable

Starts only when Phase 1 exits. Triggered by evidence from real students, not by a calendar.

**Channel**
- Asynchronous webhook acknowledgment. Retrieval p50 alone already exceeds Meta's window; the synchronous pipeline will drop messages under real load. Acknowledge immediately, push the reply when ready.
- Real OTP delivery over WhatsApp, replacing the dev bypass.
- Refresh-token rotation.

**Operations**
- Deployment behind a TLS terminator. The intended first step is a single small instance behind a tunnel — not a multi-AZ managed estate, which at pilot scale costs more than it protects.
- A tested restore from `pg_dump`. Untested backups are not backups.
- Retention and cleanup jobs for `revoked_tokens`, `processed_webhook_messages` and `moderation_flags`.
- A staging environment, once a paying customer exists to protect from a bad deploy.

**Product**
- Parent dashboard beyond the summary endpoint.
- Three-question onboarding (name, class, topic of interest), which needs per-user turn-state tracking.
- Quiz generation and grading.

**Quality**
- Generation quality evaluation, not just retrieval. The current harness measures whether the right chapter was found; it says nothing about whether the Hinglish that follows is pedagogically sound.

---

## Phase 3+ — deliberately far away

Each of these has a trigger. An item without a trigger is a wish, not a plan.

| Item | Trigger |
|---|---|
| ECS Fargate, managed Postgres, multi-AZ | Real traffic that a single instance cannot serve |
| Redis / caching | A profiled read path that is genuinely hot |
| Message queue, event bus (SNS/SQS, not Kafka) | Notification or analytics volume that warrants decoupling |
| Second LLM provider | Revenue that justifies the contract and the integration time |
| Dedicated vector database | Corpus past ~100k chunks, or latency provably dominated by the scan |
| Microservice extraction | A second person owning a module full time |
| Kubernetes | Far past the point where a single container is the constraint |
| Teacher-facing tooling | Explicitly out of scope for the first six months |
| Multilingual UI beyond response-language adaptation | Demand evidenced by real users |
| Voice interaction, offline mode | Long-term vision, not a near-term build |

---

## Things that will not be built

- **Web-search fallback.** Not deferred — rejected. Answering from the open internet is the exact behaviour this product exists to avoid, and adding it would dissolve the only claim that distinguishes GuruJi from any other chatbot.
- **Photo uploads for minors.** No object store, no image moderation, and no lawful basis under DPDP.
- **Raw transcript access for parents by default.** Summaries and flagged exchanges only. A child who believes every word is read will stop asking the questions that matter.