# Implementation Guide

How to get GuruJi running locally, ingest a corpus, verify it actually works, and iterate on it.

This document assumes nothing beyond a working Docker installation. Every command is copy-pasteable and every step states what success looks like, so a failure is visible immediately rather than three steps later.

> **Read [§0](#0-before-you-start) first.** Two of the four points there will bite you otherwise.

---

## Contents

| § | Section |
|---|---|
| 0 | [Before you start](#0-before-you-start) |
| 1 | [Prerequisites](#1-prerequisites) |
| 2 | [Configure `.env`](#2-configure-env) |
| 3 | [First build and boot](#3-first-build-and-boot) |
| 4 | [Verify the stack is healthy](#4-verify-the-stack-is-healthy) |
| 5 | [Ingest a corpus](#5-ingest-a-corpus) |
| 6 | [Walk through the product by hand](#6-walk-through-the-product-by-hand) |
| 7 | [Connect a real WhatsApp number](#7-connect-a-real-whatsapp-number) |
| 8 | [Run the test suite](#8-run-the-test-suite) |
| 9 | [Run the evaluation harness](#9-run-the-evaluation-harness) |
| 10 | [Fast frontend iteration](#10-fast-frontend-iteration) |
| 11 | [Production-shaped local run](#11-production-shaped-local-run) |
| 12 | [Verify the hardening applied](#12-verify-the-hardening-applied) |
| 13 | [Data deletion (DPDP)](#13-data-deletion-dpdp) |
| 14 | [Reset to a clean slate](#14-reset-to-a-clean-slate) |
| 15 | [Troubleshooting](#15-troubleshooting) |
| 16 | [Day-to-day loop](#16-day-to-day-loop) |

---

## 0. Before you start

**Ingestion costs real money; nothing else does.** The test suite mocks OpenAI entirely — no test spends a cent. Ingestion does not: it computes real `text-embedding-3-small` embeddings for every chunk. The full 73-chapter corpus is roughly **$0.01** at current embedding prices. The evaluation harness is more expensive than it looks, because it makes one query-planner call per row: about **$0.09** per full `--rewrite` pass. Cheap enough to run daily, not cheap enough to run in a loop and forget about.

**`SECRET_KEY` fails silently, unlike `POSTGRES_PASSWORD`.** Compose refuses to start without `POSTGRES_PASSWORD`. It does **not** check `SECRET_KEY` — `config.py` quietly falls back to `dev-only-change-me`, which means anyone who has read this repository can forge a session token for any student and derive any parent-link PIN. There is no boot guard for this yet. Check it by hand in §2.

**A fresh database has no corpus.** Until you complete §5, every question correctly returns "not in your textbook yet". That is the system working, not failing.

**Compose v2.24 or newer is required.** `docker-compose.dev.yml` uses the `!override` YAML tag, which older versions do not understand.

---

## 1. Prerequisites

| Requirement | Check | Notes |
|---|---|---|
| Docker Engine | `docker --version` | Docker Desktop is fine on macOS and Windows |
| Docker Compose v2.24+ | `docker compose version` | Required for `!override` |
| Python 3.12+ | `python3 --version` | Only for the host-side test and eval loops (§8, §9). Not needed to run the stack. |
| Node.js 24+ | `node --version` | Only for the fast frontend loop (§10) |
| An OpenAI API key | — | With billing enabled |
| NCERT PDFs | — | Not distributed here. See §5. |

---

## 2. Configure `.env`

```bash
cp .env.example .env
```

Generate two independent secrets:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
```

Paste those into `.env` and add your `OPENAI_API_KEY`.

**Verify no placeholder survived.** This must print nothing:

```bash
grep -nE '^(OPENAI_API_KEY|POSTGRES_PASSWORD|SECRET_KEY)=(REPLACE|sk-REPLACE|dev-only-change-me)?$|REPLACE-ME' .env
```

If it prints a line, that key is still a placeholder.

**Confirm `.env` is ignored by git.** This must print `.env`:

```bash
git check-ignore .env
```

If it prints nothing, stop — do not commit until `.gitignore` is fixed. A committed `.env` is the highest-severity mistake available in this repository.

---

## 3. First build and boot

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The first build takes a few minutes: two multi-stage image builds and a Postgres initialisation. Subsequent boots are seconds.

**What success looks like** — in the logs, in this order:

```
guruji-db-1   ... database system is ready to accept connections
guruji-api-1  ... SECURITY: DEV_OTP_BYPASS is ON — auth accepts the fixed dev OTP. Local dev only.
guruji-api-1  ... Uvicorn running on http://0.0.0.0:8000
guruji-web-1  ... start worker processes
```

The `DEV_OTP_BYPASS` warning is expected and correct under the dev override. Seeing it in any other environment means something is badly misconfigured — the application is designed to refuse to boot in that case.

**What the dev override changes:**

| | Base (`docker-compose.yml`) | With `docker-compose.dev.yml` |
|---|---|---|
| Web | `127.0.0.1:8080` | `127.0.0.1:8080` |
| API | not exposed | `127.0.0.1:8000` (for `/docs`) |
| Postgres | not exposed | `127.0.0.1:5433` |
| OTP | real delivery required (not implemented) | bypass enabled, code `000000` |

---

## 4. Verify the stack is healthy

Run each of these. Every one states its expected output.

```bash
# 1. Containers up and healthy
docker compose ps
# expect: web, api, db — all "running", db "(healthy)"

# 2. API health check — this genuinely executes SELECT 1 against Postgres
curl -s http://127.0.0.1:8000/health
# expect: {"status":"ok"}

# 3. Same check through nginx, which is the path a browser takes
curl -s http://127.0.0.1:8080/api/health
# expect: {"status":"ok"}

# 4. Security headers are actually applied
curl -sI http://127.0.0.1:8080/ | grep -i 'content-security-policy'
# expect: a header containing default-src 'self'

# 5. Interactive API docs
open http://127.0.0.1:8000/docs        # macOS; use xdg-open on Linux
```

**Prove the health check is real, not hardcoded.** Stop the database and watch it turn red:

```bash
docker compose stop db
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
# expect: 503
docker compose start db
```

---

## 5. Ingest a corpus

Ingestion is an offline operation. It never shares a process with the live request path.

### 5a. Place the PDFs

NCERT PDFs are not distributed with this repository. The manifests in `backend/manifests/` expect them at paths relative to `backend/`:

```
<repo-root>/
├── Books/                          # gitignored — supply this yourself
│   ├── class-5/EVS/ch01.pdf …
│   ├── class-6/Science/ch01.pdf …
│   ├── class-7/Science/ch01.pdf …
│   ├── class-8/Science/ch01.pdf …
│   ├── class-9/Science/ch01.pdf …
│   └── class-10/Science/ch01.pdf …
└── backend/manifests/
    ├── EVS/class05_manifest.csv
    └── science/class06..10_manifest.csv
```

Each manifest is a three-column CSV: `pdf_path,chapter_no,title`. Adding a book means adding a manifest, not writing code.

### 5b. Ingest one chapter

Run inside the `api` container against a bind mount, so the script executes the code on disk rather than a copy baked into an image:

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python scripts/ingest_curriculum.py ../Books/class-8/Science/ch01.pdf \
      --subject Science --grade 8 --chapter-no 1 \
      --title "Exploring the Investigative World of Science"
```

Add `--dry-run` to see the chunk count and a sample chunk without spending anything or writing to the database. Worth doing once on a new book to confirm the PDF has a text layer.

### 5c. Ingest a whole book

```bash
docker compose -f docker-compose.yml -f docker-compose.ingest.yml run --rm \
  -v "$(pwd):/workspace" -w /workspace/backend \
  api python scripts/ingest_book.py manifests/science/class08_manifest.csv \
      --subject Science --grade 8
```

The `docker-compose.ingest.yml` overlay raises the `api` memory limit to 2 GB, which larger books need. `ingest_book.py` skips chapters already present (override with `--force`) and calls `ingest_curriculum.py` once per chapter as a subprocess — it does not reimplement chunking or embedding, so there is exactly one code path that decides how a chapter enters the corpus.

**Recommended order: 8 → 9 → 10 → 7 → 6 → 5.** Class 9 is the largest book and overlaps most with Class 8, which makes it the best stress test for two-pass retrieval. Class 5 is EVS, not Science, and must be labelled `--subject EVS`.

### 5d. Verify the corpus

```bash
docker compose exec db psql -U guruji -d guruji -c \
  "SELECT d.grade, d.subject, count(DISTINCT d.id) AS chapters, count(c.id) AS chunks
     FROM curriculum_documents d
     JOIN curriculum_chunks c ON c.document_id = d.id
    GROUP BY d.grade, d.subject ORDER BY d.grade;"
```

Expect one row per (class, subject) pair, with chunk counts in the hundreds per book.

---

## 6. Walk through the product by hand

Automated tests prove the parts work. This proves the whole thing works.

### 6a. Sign in

1. Open <http://127.0.0.1:8080>.
2. Enter any 10-digit Indian mobile number starting 6–9, e.g. `9999900001`.
3. Enter OTP `000000`.
4. Choose a class on the onboarding screen.

**Expect:** you land in the chat view with class-appropriate suggested openers.

### 6b. Ask a grounded question

Ask something you know is in the class you ingested — for Class 8, "pressure kya hota hai?".

**Expect:** a short Hinglish reply, under about 50 words, that guides rather than dumps the answer, with a chapter chip beneath it. Tapping the chip opens the actual textbook passage the answer was drawn from.

### 6c. Ask an ungrounded question

Ask something definitely not in the corpus — "IPL kaun jeeta tha?".

**Expect:** an honest "not in your textbook yet" style reply, and **no** chapter chip. If you get a confident answer with a citation, that is a false grounding — see [`docs/EVALUATION.md`](docs/EVALUATION.md).

### 6d. Ask a non-question

Send "samajh nahi aaya" or "thanks".

**Expect:** a conversational reply, **not** a not-in-your-textbook refusal. This is the query planner doing its job: it decides this message is not a textbook lookup and skips retrieval entirely. A refusal here is the highest-signal regression in the whole system.

### 6e. Parent linking, end to end

```bash
# As the student (grab the token from browser devtools → Application → local storage)
curl -X POST http://127.0.0.1:8000/v1/students/<STUDENT_ID>/link-parent \
  -H "Authorization: Bearer $STUDENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"parent_phone_number": "+919999900002"}'
# -> {"link_pin": "123456"}

# As the parent: sign in with +919999900002 / 000000 in a private window, then
curl -X POST http://127.0.0.1:8000/v1/students/<STUDENT_ID>/verify-parent-link \
  -H "Authorization: Bearer $PARENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"link_pin": "123456"}'
# -> 204

curl http://127.0.0.1:8000/v1/students/<STUDENT_ID>/summary \
  -H "Authorization: Bearer $PARENT_TOKEN"
# -> a summary. Never a raw transcript — that is by design.
```

Five wrong PINs delete the link row entirely, which invalidates the PIN (it is HMAC-derived from that row's id). The student must re-invite, generating a new row and therefore a new PIN.

---

## 7. Connect a real WhatsApp number

The webhook does two things: it **acks** Meta immediately (`{"status": "accepted"}`), then runs the tutoring turn in the background and **sends** the reply through Meta's Cloud API. Without a token the second half degrades to a log line, so §7a works with no Meta account at all; §7b–7d are what it takes to put a reply on a real phone.

### 7a. Simulate a message locally (no Meta account)

`scripts/send_test_webhook.py` builds a correctly shaped payload and signs it with your `WHATSAPP_APP_SECRET`.

```bash
cd backend
export WHATSAPP_APP_SECRET="$(grep '^WHATSAPP_APP_SECRET=' ../.env | cut -d= -f2-)"
export BASE_URL=http://127.0.0.1:8000

python3 scripts/send_test_webhook.py "+919999900001" "8"                     # onboarding: sets Class 8
python3 scripts/send_test_webhook.py "+919999900001" "pressure kya hota hai?"  # a tutoring turn
```

**Expect:** HTTP 200 and `{"status": "accepted", "accepted": 1}` — the body is an ack, not the answer. The reply itself is in the api log:

```bash
docker compose logs api | grep outbound_reply
# ... outbound_reply (log-only, WhatsApp outbound not configured) to=+919999900001 reply='...'
```

Re-run with `WAMID` pinned to exercise idempotency:

```bash
WAMID=wamid.fixed.001 python3 scripts/send_test_webhook.py "+919999900001" "test"
WAMID=wamid.fixed.001 python3 scripts/send_test_webhook.py "+919999900001" "test"
# second call -> {"status": "duplicate", "accepted": 0}, and no second LLM charge
```

> With `WHATSAPP_ACCESS_TOKEN` set, this script sends a **real** WhatsApp message to whatever number you pass. Use one of the test number's verified recipients (§7b step 3), and only after that phone has messaged the business number first.

### 7b. Meta-side setup, from nothing

Items marked *[verify]* are Meta UI details that move; confirm them in the dashboard rather than trusting this page.

1. **Developer account and business portfolio.** Sign up at developers.facebook.com; in Meta Business Suite create a Business portfolio (a personal Facebook account is required to hold both).
2. **App.** My Apps → Create App → type **Business** → attach the portfolio → Add product → **WhatsApp**.
3. **Test number.** WhatsApp → API Setup. Meta provides a free **test phone number**. Note two IDs on this page: the **Phone number ID** (→ `WHATSAPP_PHONE_NUMBER_ID`) and the WABA ID. Add recipient numbers under "To" — each one confirms via an OTP on that phone. Up to 5 recipients *[verify]*. Traffic to and from the test number is free *[verify]*.
4. **Access token — the permanent one.** The token shown on API Setup expires in 24 hours; do not deploy it. Instead: Business Settings → Users → **System Users** → Add (role Admin) → **Assign Assets**: the app (full control) and the WhatsApp account → **Generate Token**: select the app, expiry **Never**, permissions `whatsapp_business_messaging` and `whatsapp_business_management`. That string is `WHATSAPP_ACCESS_TOKEN`. It sends messages as the business — handle it like `SECRET_KEY`.
5. **Graph API version.** The curl sample on API Setup shows a URL like `https://graph.facebook.com/vNN.0/...`. Copy that `vNN.0` exactly into `WHATSAPP_GRAPH_API_VERSION`. There is deliberately no default in the code: a guessed version is how sends silently start failing on a Meta deprecation date.
6. **App secret.** App Settings → Basic → **App Secret** → `WHATSAPP_APP_SECRET`. This is what signs `X-Hub-Signature-256`; the webhook rejects anything not signed with it.
7. **Webhook.** WhatsApp → Configuration → Webhook → Edit. Callback URL `https://<GURUJI_HOSTNAME>/api/v1/webhooks/whatsapp`, Verify token = your `WHATSAPP_VERIFY_TOKEN`. **Verify and save** — Meta calls the GET handler and expects the challenge echoed back (the stack must already be running, §7c). Then **Manage** → subscribe to the **messages** field.
8. **Path to a real number, later.** Add a business phone under Phone numbers (SMS/voice verification), submit the display name for review, and complete Business verification in Business Settings. A newly verified number starts on a limited tier of business-initiated conversations per 24 h that grows with quality rating *[verify — roughly 250]*. None of this affects replies to a student who messaged first.

**The 24-hour rule.** Free-form text can only be sent to a number that messaged the business within the last 24 hours. Every GuruJi reply is a reply to a student's message, so this never bites the tutoring loop — but it is why the test recipient must text first, why a late `send_test_webhook.py` gets Meta error `131047`, and why OTP delivery to the web login is not just "call `send_text`": that is business-initiated and needs an approved *authentication* template.

### 7c. Configure the deployed box

Terraform provisions no secrets store on purpose (`terraform/user_data.sh`, `terraform/outputs.tf`). The three values are hand-edited into `.env` on the instance:

```bash
aws ssm start-session --target <instance-id>       # from `terraform output`
cd /opt/guruji && sudo nano .env                    # add the three WHATSAPP_* outbound keys
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.caddy.yml \
  up -d --build api web caddy
docker compose logs api | grep -i whatsapp          # expect NO "outbound not configured" warning
```

The `web` and `caddy` rebuild picks up the webhook route's own body-size and rate-limit overrides (`frontend/nginx.conf`, `Caddyfile`). Meta delivers every student's message from a handful of Meta IPs, so the webhook must not share the per-IP `api` rate bucket.

### 7d. Verify end to end

```bash
# 1. Handshake reachable through Caddy, no basic-auth prompt (webhooks are outside the pilot gate)
curl -s "https://$HOST/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=$WHATSAPP_VERIFY_TOKEN&hub.challenge=123"
# expect: 123

# 2. From a verified recipient phone, WhatsApp the test number: "hi"
# expect on the phone within a few seconds: the class prompt
# then send: "8"                       -> "Class 8 — set!"
# then send: "pressure kya hota hai?"  -> a Hinglish reply within ~5-25s

# 3. Nothing failed on the way out
docker compose logs api | grep -E "whatsapp send failed|outbound_reply sent"
# expect: only "outbound_reply sent" lines
```

A `whatsapp send failed` line names Meta's error: `131047` means the phone did not message first (24 h window); `190` means the token is dead — regenerate the System User token; `131026` usually means the recipient is not in the test number's verified list.

---

## 8. Run the test suite

125 tests. OpenAI and Meta's Cloud API are mocked throughout; no test spends money or sends a message. Postgres is **not** mocked, because `search_chunks()` is SQL — mocking it would test the mock. The suite is safe to run repeatedly against the same database: tests that need a never-seen phone number generate one.

### In Docker (matches CI most closely)

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api sh -c "pip install -q -r requirements-dev.txt && pytest"
```

### On the host (faster loop)

Requires the dev override, which exposes Postgres on `127.0.0.1:5433`.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL="postgresql+psycopg2://guruji:<YOUR_POSTGRES_PASSWORD>@127.0.0.1:5433/guruji"
export OPENAI_API_KEY="sk-test-not-a-real-key"   # never reached; every call is patched
export APP_ENV=local

pytest
```

**Expect:** `125 passed`.

Also run the linter, which is the same command CI runs:

```bash
ruff check .
# expect: All checks passed!
```

The rule set is deliberately narrow (`F` and `E9` only) and the reasoning is documented in `backend/pyproject.toml`. Its purpose is catching an undefined name left behind by a botched edit — a failure class this codebase has shipped before, and one that `python -m py_compile` does not catch.

---

## 9. Run the evaluation harness

This measures retrieval, not the whole product. It needs a populated corpus (§5) and a real `OPENAI_API_KEY`.

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  api python scripts/eval_retrieval.py --verbose
```

| Flag | Effect |
|---|---|
| `--verbose` | Print every miss and every false grounding with its retrieved chapters and scores |
| `--sweep` | Re-score at several threshold pairs **without re-embedding**, so thresholds are chosen from data |
| `--no-rewrite` | Skip the query planner and embed the raw message — the pre-planner A/B baseline, and free |
| `--workers N` | Parallelise planner calls |
| `--grade N` | Override the per-row grade column |
| `--json` | Machine-readable output |
| `--set PATH` | Use a different evaluation set |

**Cost:** roughly $0.09 per full pass, dominated by the per-row planner call. `--no-rewrite` is free.

Read `--sweep` output as a **trade, not an optimum**: recall depends only on the weak floor, so a rising refusal-accuracy column at flat recall is not a free win — confident rate is what pays for it. Metric definitions and how to extend the set are in [`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## 10. Fast frontend iteration

Rebuilding a Docker image for a CSS change is a poor loop. Vite's dev server proxies `/api` to the running backend, so you keep the Compose stack up and only run the frontend on the host.

```bash
# Terminal 1 — keep the stack running (api and db are what you need)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Terminal 2
cd frontend
npm ci --ignore-scripts     # --ignore-scripts matches the Dockerfile and CI, deliberately
npm run dev                 # http://127.0.0.1:5173, proxies /api -> 127.0.0.1:8000
```

Before opening a pull request, run what CI runs:

```bash
npm run typecheck
npm run build
gzip -9 -c dist/assets/*.js | wc -c
# must be under 40000 — CI fails the build above that ceiling
```

**The 40 kB gzip budget is a hard gate, not a chart.** This application targets budget Android phones on metered data. That budget is why the UI uses CSS-only motion: GSAP, Framer Motion and Lottie are each 70–250 kB, which would blow the ceiling several times over and contradict the accessibility claim the product is built on.

---

## 11. Production-shaped local run

Drop the dev override to see the real topology:

```bash
docker compose down
docker compose up --build
```

Now only `127.0.0.1:8080` is reachable. The API is behind nginx, Postgres is on an internal network with no route to the internet, and the OTP bypass is off — so the sign-in flow **will not work**, because real OTP delivery is not implemented. That is expected, and it is the honest state of Phase 1.

**Nothing in this repository terminates TLS.** Before anything faces the internet, put Caddy, an ALB, or a Cloudflare Tunnel in front. The HSTS header nginx sets only means something once something upstream actually speaks HTTPS.

---

## 12. Verify the hardening applied

Compose declarations are claims. These commands are the test.

```bash
# Containers run as non-root
docker compose exec web id
# expect: uid=101(nginx) …   — NOT uid=0(root)
docker compose exec api id
# expect: uid=10001(guruji) …  — NOT uid=0(root)

# Root filesystems are read-only
docker compose exec api touch /probe
# expect: touch: cannot touch '/probe': Read-only file system
docker compose exec web touch /probe
# expect: the same

# The database has no route to the internet
docker compose exec db getent hosts api.openai.com
# expect: no output, non-zero exit
```

---

## 13. Data deletion (DPDP)

This is children's data. The erasure path must exist before anyone asks for it.

```bash
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python scripts/delete_user.py <USER_ID>
```

It prompts for confirmation, then deletes across `messages`, `conversations`, `student_memory`, `moderation_flags`, `parent_links`, `students` and `users` in one transaction.

> This covers erasure on request. It does **not** constitute DPDP Rules 2025 compliance, which additionally requires verifiable parental consent *before* a minor's data is processed. That is unresolved — see the README's Known limitations.

---

## 14. Reset to a clean slate

```bash
# Back up first if any transcript matters — there is no export feature.
docker compose exec db pg_dump -U guruji guruji > backup-$(date +%F).sql

# Containers, networks, AND the named volume (every student, message and chunk)
docker compose down -v --remove-orphans

# This project's own images
docker image rm -f guruji-api:local guruji-web:local 2>/dev/null

# Confirm nothing survives
docker volume ls --filter "name=guruji"
```

Re-ingestion is then required. At current embedding prices the full corpus is around $0.01.

> Do **not** run `docker system prune -a` unless this is the only project on the machine — it deletes images for everything else too.

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `POSTGRES_PASSWORD must be set` | `.env` missing or the key is empty | §2 |
| API exits with `DEV_OTP_BYPASS=1 is forbidden` | `APP_ENV` is not `local` while the bypass is on | Intended safety behaviour — set `APP_ENV=local` for local dev only |
| Every answer is "not in your textbook yet" | Empty corpus | §5 |
| A *non-question* gets a not-in-your-textbook reply | Query planner failing or being skipped | Check the `grounding` field in the API log line: `not_needed` is correct, `empty` means the planner classified it as a lookup |
| `ModuleNotFoundError: No module named 'app'` | A script was run without `backend/` on the path | Run from `backend/`; `scripts/_bootstrap.py` handles the rest |
| Tests fail with `connection refused` | Postgres not exposed to the host | Start with the dev override; port is **5433**, not 5432 |
| `unknown tag !override` | Compose older than v2.24 | Upgrade Docker Compose |
| Frontend build fails the bundle budget | A dependency was added | Remove it, or justify raising the ceiling in a pull request — do not silently raise it |
| Ingestion produces zero chunks | Scanned PDF with no text layer | Confirm with `--dry-run`; OCR is out of scope for Phase 1 |

Read the API logs as structured JSON. One line per request carries `request_id`, `grounding`, `search_query`, `citation`, `retrieval_top_score`, `latency_ms` and `validation_result` — enough to tell a planner misfire from a retrieval miss without adding instrumentation:

```bash
docker compose logs -f api | grep grounding
```

---

## 16. Day-to-day loop

```bash
# Backend change
docker compose up -d --build api

# Frontend change, Docker path
docker compose up -d --build web

# Frontend change, fast path (recommended for active UI work)
# just keep `npm run dev` from §10 running — no rebuild at all

# Before opening a pull request
cd backend && ruff check . && pytest
cd ../frontend && npm run typecheck && npm run build

# Done for the day
docker compose down          # keeps the data volume

# Back tomorrow
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```
