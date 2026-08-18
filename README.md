# GuruJi

**A retrieval-grounded AI tutor for Class 5–10 NCERT students, built to answer only from the textbook the student actually owns.**

GuruJi is a Hinglish tutoring assistant for Tier-2/3 India. It runs over WhatsApp and a lightweight web client, and it is architecturally constrained to answer from an ingested NCERT corpus rather than from the model's own parametric knowledge. When retrieval finds nothing, it says so instead of guessing.

This repository is the **Phase 1** system: a complete, locally deployable stack — FastAPI backend, PostgreSQL + pgvector, and a Preact web client — with a measured retrieval pipeline and an evaluation harness. It is **not** yet in production. See [Project status](#project-status) for exactly what does and does not work.

---

## Contents

- [Why this exists](#why-this-exists)
- [Project status](#project-status)
- [Architecture](#architecture)
- [Retrieval pipeline](#retrieval-pipeline)
- [Evaluation](#evaluation)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API surface](#api-surface)
- [The corpus](#the-corpus)
- [Security](#security)
- [Known limitations](#known-limitations)
- [Documentation index](#documentation-index)
- [License](#license)

---

## Why this exists

A general-purpose chatbot will happily answer a Class 7 question at graduate level, from a source the student cannot check, in a register they do not speak. For a child in a Tier-2 city whose parents cannot verify the answer, a confident wrong answer is worse than no answer.

GuruJi makes three commitments that shape every design decision in this repository:

| Commitment | How it is enforced in code |
|---|---|
| **Answers come from the student's textbook** | Every tutoring turn retrieves from an ingested NCERT corpus first. If nothing clears the similarity floor, the prompt requires an explicit "not in your textbook yet" acknowledgment, and response validation rejects a reply that omits it. There is no web-search fallback anywhere in the pipeline — deliberately. |
| **A student is never judged for asking below their level** | Retrieval runs two passes: the student's own class first, then widening to lower classes on a miss. A Class 10 student can ask a Class 6 question and get a Class 6 answer, cited as such. |
| **The system says what it does not know** | Provenance (grounding state, cited chapter, source excerpt) is computed server-side and persisted on the message row. The client renders it; it never infers it. |

---

## Project status

**Phase 1 — locally complete, not deployed. Not yet used by real students.**

| Area | State |
|---|---|
| Core tutoring loop (web client) | Working end to end |
| Retrieval pipeline + evaluation harness | Working, measured — see [Evaluation](#evaluation) |
| Corpus ingestion (73 NCERT chapters, Classes 5–10) | Working |
| Per-chat class and subject selection | Working — pill in the chat header, editable while a chat is empty |
| Backend test suite | 97 tests, green, runs against a real Postgres in CI |
| WhatsApp **inbound** webhook | Working — signature verification, idempotency, allow-list |
| WhatsApp **outbound** send | **Not implemented.** The reply is logged and returned in the webhook response body; nothing calls the WhatsApp Cloud API. This is the single largest gap. |
| Real OTP delivery | Not implemented (dev bypass only, fails closed outside `APP_ENV=local`) |
| DPDP Rules 2025 verifiable parental consent | **Not implemented.** Must be resolved before any real child's data is processed. |
| Production deployment | Not done. Nothing here terminates TLS. |

Phase 1 exit criteria and the Phase 2 boundary are in [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Architecture

One deployable backend, one database, one LLM provider, two client channels.

```
  WhatsApp (Meta)                     Browser
        |                                |
        | POST /v1/webhooks/whatsapp     | HTTPS
        | (HMAC X-Hub-Signature-256)     |
        |                                v
        |                        +---------------+
        |                        | nginx  (web)  |  static bundle + /api proxy
        |                        +-------+-------+
        +----------------+---------------+
                         v
              +----------------------+
              |  FastAPI  (api)      |  modular monolith
              |                      |
              |  identity            |  phone identity, tokens, revocation
              |  student_profile     |  grade, board, parent links
              |  conversation        |  sessions, messages, both channels
              |  ai_orchestrator     |  the pipeline described below
              |  curriculum          |  two-pass hybrid retrieval
              |  memory              |  one JSONB row per student
              |  safety              |  moderation flag persistence
              +-------+--------------+
                      |                        +--------------+
                      +----------------------->|  OpenAI API  |
                      |                        +--------------+
                      v
          +---------------------------+
          | PostgreSQL 16 + pgvector  |  transactional + vector, one instance
          +---------------------------+
```

**Why a modular monolith rather than services.** A one-to-two person team cannot operate seventeen deployables — seventeen health checks, seventeen pipelines, seventeen places a bug can hide. Modules own their own tables and never import each other's models; cross-module calls go through a public service function. Extracting a module later means moving a folder and swapping a function call for an HTTP call, not a rewrite.

**Why pgvector rather than a dedicated vector database.** The corpus is 3,762 chunks. A flat cosine scan over that is not the bottleneck, and a second datastore is a second thing to operate, back up and pay for. The threshold for revisiting is recorded in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Full detail, including the module table and every deferred decision: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Retrieval pipeline

A tutoring turn runs nine steps. Steps 3 and 4 are the two that most distinguish this from a naive RAG chatbot.

1. **Moderation (blocking).** Every inbound message hits the OpenAI moderation endpoint before it can reach the tutoring model. A flagged message is never forwarded, is persisted to `moderation_flags`, and returns a fixed safe reply.
2. **Spend circuit breaker.** The running daily spend ledger is summed and checked before any paid call.
3. **Query planning.** A cheap-model call decides *whether this message is a textbook question at all*, and rewrites it into a standalone English search query. This is what stops `"answer this in english"` or `"samajh nahi aaya"` from being embedded as a vector query and refused as out-of-corpus. It fails open: a planner outage degrades to embedding the raw message, which is exactly the pre-planner behaviour.
4. **Two-pass hybrid retrieval.**
   - *Pass 1* searches the student's **own class only**.
   - *Pass 2* widens to their class **and below**, and runs only when pass 1 found nothing above the grounded floor.

   Each pass fuses a dense leg (pgvector cosine) with a lexical leg (Postgres `tsvector` / `ts_rank_cd`) using Reciprocal Rank Fusion. RRF is used rather than a weighted score blend because cosine similarity and `ts_rank_cd` sit on incomparable scales — ranks are comparable, raw scores are not, and a blend would need a weight nobody has measured.

   *Why two passes.* NCERT teaches the same topic repeatedly at increasing depth — force in Classes 6, 8 and 9; light in 6, 7, 8 and 10. Under a flat `grade <= N` filter a Class 10 query competes against six classes at once and similarity picks the winner. Similarity has no concept of "more advanced", and simpler prose is often *more* lexically direct, so the Class 6 chunk frequently wins. The student then gets a confident, correctly cited answer pitched four years too low — worse than a refusal, because nothing about it looks wrong.
5. **Grounding classification.** The best cosine score resolves to one of `grounded` / `weak` / `empty` / `not_needed`.
6. **Prompt assembly.** Persona, retrieved chunks with chapter labels, memory summary, recent turns — token budgeted.
7. **Generation.** One call, `max_completion_tokens=500` as a hard cost cap.
8. **Output moderation and validation.** The generated reply is moderated too. Validation checks length, a blocked-phrase list, a PII pattern pass, and — when retrieval came back empty — the presence of an uncertainty marker. One stricter regeneration is allowed, bounded by a latency deadline; a second failure returns a static safe fallback.
9. **Persist and log.** One structured JSON line per request, carrying request id, grounding, search query, citation, top score, token split, cost, latency and validation result.

---

## Evaluation

Retrieval quality is measured, not asserted. The harness is `backend/scripts/eval_retrieval.py`, run against a 191-row labelled set at `backend/eval/ncert_grade8_science.csv`.

Latest recorded run — corpus 3,762 chunks, Classes 5–10:

| Metric | Value | What it measures |
|---|---|---|
| Recall@5 | **99.3%** (n=141) | Did retrieval find the right chapter? |
| MRR | **0.972** | How highly did it rank it? |
| Gate accuracy | **100%** (n=14) | Did the planner correctly skip retrieval for non-questions? |
| Refusal accuracy | **88.9%** (n=36) | For genuinely out-of-corpus questions, did we correctly end up ungrounded? |
| Confident rate | **97.9%** | Share of real in-corpus questions answered confidently rather than hedged |
| Retrieval p50 | **3,704 ms** | Median retrieval latency |

**Read these honestly, and read the caveats:**

- **The set is 82% Class 8.** 157 of 191 rows are Class 8; Class 9 has 5 rows, Class 10 has 8, Class 5 has none. The headline recall figure is strong evidence that adding five classes did not regress Class 8. It is *thin* evidence that two-pass retrieval works well for a Class 9 student. Broadening the set is the top evaluation priority.
- **Refusal accuracy of 88.9% means four false groundings** — out-of-corpus questions answered with textbook authority. The harness prints each one, because asserting a chapter citation for content that is not in that chapter is the worst failure this product has.
- **`--sweep` shows this is a tunable trade, not a bug.** Raising the grounded floor from 0.40 to 0.45 takes refusal accuracy to 100% with recall and MRR unchanged, at a cost of 7.8 points of confident rate. The shipped default currently sits on the permissive side of that trade. See [Known limitations](#known-limitations).
- **Retrieval p50 has grown with the corpus** (2,045 ms to 3,704 ms over the last two ingestion rounds) and is now larger than Meta's webhook acknowledgment window on its own.

Every recorded run, with full per-kind breakdowns and miss listings, is preserved verbatim in [`docs/evaluation-runs.md`](docs/evaluation-runs.md). Methodology and reproduction steps: [`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## Repository layout

```
.
├── README.md
├── IMPLEMENTATION.md            step-by-step local setup and verification
├── .env.example                 every configuration key, documented
├── docker-compose.yml           hardened topology: web + api + db
├── docker-compose.dev.yml       local override — exposes ports, enables OTP bypass
├── docker-compose.ingest.yml    raises the api memory limit for batch ingestion
│
├── .github/workflows/ci.yml     backend tests · frontend build · bundle budget · image scans
│
├── backend/
│   ├── Dockerfile               multi-stage, non-root, read-only root filesystem
│   ├── pyproject.toml           ruff + pytest configuration
│   ├── requirements.txt         runtime dependencies only
│   ├── requirements-dev.txt     adds pytest and ruff
│   ├── app/
│   │   ├── main.py              application factory, boot guards, health check
│   │   ├── config.py            single source of truth for every tunable
│   │   ├── db/
│   │   │   ├── schema.sql       full schema plus the search_chunks() SQL function
│   │   │   └── session.py       engine, session factory, idempotent init_db()
│   │   └── modules/
│   │       ├── identity/            phone identity, tokens, revocation
│   │       ├── student_profile/     grade, board, parent links, summaries
│   │       ├── conversation/        both channels, sessions, messages
│   │       ├── ai_orchestrator/     the pipeline, the LLM client, prompts/
│   │       ├── curriculum/          two-pass hybrid retrieval
│   │       ├── memory/              one JSONB row per student
│   │       └── safety/              moderation flag persistence
│   ├── scripts/                 operator tooling — never imported by the app
│   │   ├── ingest_curriculum.py     one chapter: PDF → chunks → embeddings → Postgres
│   │   ├── ingest_book.py           batch wrapper over a CSV manifest
│   │   ├── eval_retrieval.py        the evaluation harness
│   │   ├── delete_user.py           DPDP erasure across every table
│   │   └── send_test_webhook.py     signed mock WhatsApp payload, no Meta account needed
│   ├── manifests/               per-book chapter manifests (CSV)
│   ├── eval/                    labelled evaluation set
│   └── tests/                   97 tests against a real Postgres
│
├── frontend/                    Preact + TypeScript + Vite, served by nginx
│   ├── src/screens/             Auth · Onboarding · Chat · Profile · Parent
│   ├── src/components/          Shell and shared UI
│   ├── src/api.ts               typed client
│   └── src/backend.ts           mirrors backend constants; no state inferred from prose
│
└── docs/
    ├── ARCHITECTURE.md          decisions, module boundaries, what was deferred and why
    ├── EVALUATION.md            harness methodology, metric definitions, reproduction
    ├── SECURITY.md              threat model, controls, known gaps
    ├── ROADMAP.md               Phase 1 exit criteria, Phase 2 scope
    └── evaluation-runs.md       verbatim harness output, every recorded run
```

---

## Quick start

**Prerequisites:** Docker Engine with Compose v2.24 or newer, and an OpenAI API key.

```bash
git clone <your-fork-url> guruji && cd guruji

cp .env.example .env
# Fill in at minimum: OPENAI_API_KEY, POSTGRES_PASSWORD, SECRET_KEY.
# Generate the secrets with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Open <http://127.0.0.1:8080>. Sign in with any 10-digit Indian mobile number (starting 6–9) and OTP `000000` — the dev override sets `DEV_OTP_BYPASS=1`, and the application **refuses to boot** with that flag set unless `APP_ENV=local`.

A fresh database has no corpus, so every question will correctly return "not in your textbook yet" until a chapter is ingested. Ingestion, verification steps, running the tests, and the fast frontend-only loop are all covered in **[IMPLEMENTATION.md](IMPLEMENTATION.md)**.

---

## Configuration

Every key lives in `.env` and is documented inline in [`.env.example`](.env.example). The ones that change behaviour most:

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. |
| `POSTGRES_PASSWORD` | — | Required; Compose fails fast if unset. |
| `SECRET_KEY` | `dev-only-change-me` | Signs every session token **and** derives parent-link PINs. The app refuses to boot on this default unless `APP_ENV=local`. |
| `WHATSAPP_APP_SECRET` | `dev-app-secret` | Verifies Meta's webhook signature. Same boot refusal as above. |
| `APP_ENV` | `local` | Any other value makes the application refuse to boot with `DEV_OTP_BYPASS=1`. |
| `DAILY_SPEND_CAP_USD` | `5` | Suits roughly 20 pilot students. Estimated to break somewhere around 150–250 active students; raise it deliberately rather than discovering the fallback message in a transcript. |
| `ALLOWED_PHONE_NUMBERS` | *(empty)* | Empty means **open**: anyone who learns the WhatsApp number gets a provisioned account and billed tutoring. Set it for a closed pilot. |
| `RAG_THRESHOLD` | `0.35` (code) / `0.40` (`.env.example`) | Grounded floor. These two disagree; see [Known limitations](#known-limitations). |
| `RAG_WEAK_THRESHOLD` | `0.28` | Floor for the hedged band. |
| `RAG_LEXICAL_RESCUE` | `1` | Keeps a lexical-only match that scored below the weak floor. |
| `ALLOWED_WEB_ORIGINS` | *(empty)* | Closed by default. Never `*` — bearer tokens plus a wildcard with credentials is a real vulnerability. |

---

## API surface

REST, versioned under `/v1`, served directly by FastAPI. Interactive documentation is auto-generated at `/docs` and `/redoc` when the dev override is active.

Two authentication mechanisms, deliberately separate:

- **REST routes** — `Authorization: Bearer <token>`, resolved to a `(user_id, role, student_id)` triple. Every per-student route checks **role *and* ownership**; a role-only check would let any authenticated student read any other student's data by supplying a different UUID.
- **The WhatsApp webhook** — no bearer token exists, because Meta calls it directly. It verifies `X-Hub-Signature-256` against the app secret, then resolves the student by inbound phone number.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/auth/otp/request` | none | Request an OTP (dev bypass only in Phase 1) |
| `POST` | `/v1/auth/otp/verify` | none | Exchange an OTP for a session token |
| `POST` | `/v1/auth/logout` | student / parent | Revoke the presented token server-side, immediately |
| `GET` | `/v1/students/me` | student | Own profile, resolved from the token |
| `POST` | `/v1/students` | student | Create profile (grade, board) |
| `GET`, `PATCH` | `/v1/students/{id}` | student (owner) | Read / update own profile |
| `POST` | `/v1/students/{id}/link-parent` | student (owner) | Create an unverified parent link, returns a PIN |
| `POST` | `/v1/students/{id}/verify-parent-link` | parent | Submit the PIN to activate the link |
| `GET` | `/v1/students/{id}/summary` | parent (linked) | Progress summary — never raw transcripts |
| `GET` | `/v1/students/{id}/flagged` | parent (linked) | Exchanges caught by moderation |
| `GET` | `/v1/curriculum/subjects` | any | `(class, subject)` pairs that actually have embedded chunks |
| `GET` | `/v1/curriculum/chapters` | any | Chapters available for a class |
| `GET` | `/v1/webhooks/whatsapp` | verify token | Meta subscription handshake |
| `POST` | `/v1/webhooks/whatsapp` | HMAC | Inbound message |
| `POST` | `/v1/conversations/messages` | student | Web-channel send — identity from the token, never the body. Accepts `grade`/`subject`, applied only when a conversation is created |
| `GET` | `/v1/conversations` | student | Own conversations, paginated |
| `GET` | `/v1/conversations/{id}/messages` | student (owner) | Transcript |
| `DELETE` | `/v1/conversations/{id}` | student (owner) | **Soft** delete — hidden from the student, intact for parent review |
| `GET` | `/health` | none | Executes `SELECT 1`; returns 503 on database failure |

---

## The corpus

**NCERT source PDFs are not distributed with this repository.** `Books/` is gitignored. What ships here are the *manifests* — the chapter-by-chapter index of what to ingest and what each chapter is called — not the copyrighted texts. Supply your own copies and place them where the manifests expect.

Current manifest coverage: **73 chapters** across six classes.

| Class | Subject | Chapters |
|---|---|---|
| 5 | EVS (*Our Wondrous World* — integrated, not Science) | 10 |
| 6 | Science | 12 |
| 7 | Science | 12 |
| 8 | Science | 13 |
| 9 | Science | 13 |
| 10 | Science | 13 |

Chunking is 900 characters with 150 characters of overlap, sentence-boundary aware, with a contextual header (class, subject, chapter number, title) prepended to every chunk before embedding. Embeddings are `text-embedding-3-small` at 1536 dimensions, **using the same model at ingest time and at query time** — a mismatch there is the single most common RAG defect and is treated here as non-negotiable.

Ingestion commands are in [IMPLEMENTATION.md](IMPLEMENTATION.md).

---

## Security

Full threat model and control inventory: [`docs/SECURITY.md`](docs/SECURITY.md). Summary of what is enforced today:

- **Child-safety moderation is blocking and mandatory** on both inbound messages and generated replies. Flagged exchanges are persisted, not merely logged, because the parent-review promise depends on them being readable later.
- **IDOR protection** on every per-student route: role *and* ownership, enforced by SQL predicate rather than by an `if role ==` branch.
- **Webhook authenticity** via HMAC signature verification, plus idempotency — a claimed message id is released if processing raises, so a crash means Meta's retry reprocesses rather than the question being silently swallowed.
- **Containers** run non-root with `cap_drop: ALL`, `no-new-privileges`, and read-only root filesystems. The database sits on an internal Docker network with no route to the internet.
- **Supply chain:** `npm ci --ignore-scripts` in both the Dockerfile and CI, `npm audit` gated on production dependencies, and daily Trivy scans of both images and the Compose configuration.
- **Secrets** are never baked into images and never committed. `.env` is gitignored at the repository root, and the app **refuses to boot** on a placeholder `SECRET_KEY`, `WHATSAPP_APP_SECRET`, or `WHATSAPP_VERIFY_TOKEN` outside local dev.
- **Provenance is gated on grounding.** A chapter citation and its passage excerpt are attached only when retrieval cleared the grounded floor, so a weak match cannot render as a confident textbook claim.

**Reporting a vulnerability:** open a private security advisory on this repository rather than a public issue.

---

## Known limitations

Listed here rather than buried, because anyone evaluating this codebase should not have to go looking.

1. **GuruJi cannot send a WhatsApp message.** Inbound is complete; outbound is not implemented. Meta does not deliver the webhook response body to the user, so on the channel the product is named for, no student currently receives a reply. This blocks real OTP delivery, any public deployment, and the Phase 1 exit criteria.
2. **DPDP Rules 2025 parental consent is unresolved.** The current parent-link flow is student-initiated and student-controlled, which is not verifiable parental consent. This must be settled before a real child's data is processed.
3. **Refusal accuracy is 88.9% and the code's own kill switch has fired.** `config.py` documents "if refusal accuracy drops below ~90%, set `RAG_LEXICAL_RESCUE=0`". The last recorded run is below that line and the switch has not been flipped. The `--sweep` output shows a grounded floor of 0.45 reaching 100% refusal accuracy at unchanged recall.
4. **`RAG_THRESHOLD` disagrees between `config.py` (0.35) and `.env.example` (0.40).** The recorded evaluation runs were performed at 0.40. Reconcile these before quoting results anywhere else.
5. **`RAG_WEAK_THRESHOLD` has no measurable effect** on the current evaluation set — every value from 0.20 to 0.35 produces bit-identical metrics, most likely because lexical rescue keeps matches the weak floor would otherwise drop.
6. **Retrieval p50 is 3.7 s and rising with corpus size**, on a synchronous pipeline that must answer inside Meta's acknowledgment window. The asynchronous ack pattern is a Phase 2 item.
7. **The evaluation set is 82% Class 8**, with no Class 5 rows at all.
8. **No staging environment.** Every deployment would be its first real-world test.

---

## Documentation index

| Document | What it covers |
|---|---|
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | Setting the system up locally, ingesting a chapter, running the tests, verifying the hardening |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module boundaries, data model, and every deferred decision with its reason |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Harness methodology, metric definitions, how to reproduce and extend the set |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, enforced controls, known gaps |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phase 1 exit criteria, Phase 2 scope, deployment path |
| [docs/evaluation-runs.md](docs/evaluation-runs.md) | Verbatim harness output for every recorded run |

---

## License

No license file is present yet. **Until one is added, default copyright applies and nobody may reuse this code.** Add a `LICENSE` before treating this repository as open source.

NCERT textbook content is separately copyrighted and is deliberately not distributed here.