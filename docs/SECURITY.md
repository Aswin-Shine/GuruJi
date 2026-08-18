# Security

Threat model, enforced controls, and known gaps for a system that processes children's data.

Gaps are listed with the same prominence as controls. A security document that only lists what works is marketing.

---

## Reporting a vulnerability

Open a **private security advisory** on this repository. Please do not open a public issue for anything exploitable.

---

## 1. What is at stake

| Asset | Why it matters |
|---|---|
| Student conversation transcripts | A minor's questions, including anything they were embarrassed to ask a teacher |
| Phone numbers | Direct contact details for children, and the sole identity factor in this system |
| Moderation flags | The most sensitive rows in the database — verbatim content of exchanges that tripped safety filters |
| OpenAI API key | Direct financial loss if leaked |
| Session tokens | Full impersonation of a student or parent |

The realistic adversaries are, in order of likelihood: an opportunist who finds the WhatsApp number and racks up API spend; a curious student who edits a UUID in a request; a scraper hitting a public endpoint; and a targeted attacker after transcripts. The controls below are weighted accordingly.

---

## 2. Enforced controls

### Authentication

**Two mechanisms, deliberately separate.** Do not unify them.

- **REST routes** — `Authorization: Bearer <token>`. The token is an HMAC-signed `user_id:jti:expiry:sig` string; stdlib only, no JWT dependency. TTL is 7 days. The `jti` allows a specific token to be revoked server-side, and `/v1/auth/logout` does exactly that, immediately, before natural expiry.
- **The WhatsApp webhook** — no bearer token exists, because Meta calls it directly. It verifies `X-Hub-Signature-256` (HMAC-SHA256 over the raw body, using `hmac.compare_digest`) and then resolves the student by inbound phone number. Forcing a bearer check onto this route would break it; it needs its own verification path.

Auth is **header-based only, never cookies**. Because no cookie carries authentication, no CSRF machinery is required. Converting this to a cookie session without adding CSRF protection would be a real regression, not a refactor.

### Authorization

Every per-student route checks **role *and* ownership**. A role-only check (`if role == "student": allow`) lets any authenticated student read any other student's data by supplying a different UUID — an IDOR, and a failure this codebase has been bitten by before.

Ownership is enforced by SQL predicate where possible rather than by a Python branch after the fetch, so there is no window in which the wrong row has been loaded:

```python
select(Conversation).where(
    Conversation.id == target_id,
    Conversation.student_id == student_id,   # the ownership predicate
    Conversation.hidden_at.is_(None),
)
```

| Role | Can reach |
|---|---|
| `student` | Own profile, own conversations, own memory |
| `parent` | A **verified**-linked student's summary and moderation flags. Never raw transcripts, by design. |
| `admin` | Ingestion triggers and internal diagnostics |

A conversation id from a client is untrusted input. A request for someone else's conversation returns **404, not 403** — a 403 would confirm the row exists, which is itself a small leak.

### Child safety

- **Moderation is blocking and mandatory** on every inbound message, before it can reach the tutoring model. This is not optimised away for cost or latency, ever.
- **Output is moderated too.** Before this, the only guard on what the model said to a child in Hinglish was a four-phrase English blocklist. A flagged reply gets a safe fallback, never a retry — a model that produced flagged text does not get a second roll of the dice.
- **Flagged exchanges are persisted**, not merely logged, to `moderation_flags` with direction and verbatim content. A log line in a cloud console is not something a parent can review; the product promises they can.
- **Conversation deletion is soft.** The row is hidden from the student; the messages remain for parent review. A child who has just asked something they regret must not be able to erase the evidence by tapping a bin icon.
- **No image uploads.** Avatars are preset glyph keys, not files. There is no object store, no image moderation, and no lawful basis under DPDP to hold a photograph of a minor.

### Cost and abuse

- **Pilot phone allow-list**, checked *before* `get_or_create_user()` — that call provisions an account on first contact, so an unknown number must never reach it.
- **Rate limiting**, 20 messages/minute per user, in-process.
- **Daily spend circuit breaker** over an append-only `llm_spend` ledger, gating tutoring *and* background memory regeneration. Trips to a static fallback and pages `ALERT_WEBHOOK_URL` once per day.
- **PIN brute-force lockout.** Five wrong parent-link PINs delete the link row, which invalidates the PIN itself (it is HMAC-derived from that row's id). A new invite means a new row, therefore a new PIN.
- **Webhook idempotency.** A message id is claimed before processing and released if processing raises — a duplicate costs no LLM call, and a crash means Meta's retry reprocesses rather than the question vanishing.

### Input handling

- Every request body validated by a Pydantic schema at the framework boundary, before business logic.
- Every SQL statement parameterised. No string interpolation into queries anywhere.
- Inbound message length capped at 2,000 characters on both channels.
- PII patterns (Indian mobile numbers, email addresses) checked on outbound replies. The phone pattern is deliberately narrow — NCERT answers are full of large numbers (populations, distances, constants) that must not false-trip it.

### Infrastructure

- Containers run **non-root** with `cap_drop: ALL`, `no-new-privileges:true`, and **read-only root filesystems** (`web` and `api`), with `tmpfs` mounted only where the process genuinely needs scratch space.
- The database sits on an `internal: true` Docker network with **no route to the internet**. A compromised database container cannot exfiltrate.
- Postgres uses `scram-sha-256` authentication, not the historical md5 default.
- Log rotation is configured on every service. An unrotated log filling the disk is the most common way a small self-hosted deployment dies.
- Only the web container is published, and only on loopback.

### Secrets

- The app **refuses to boot** when `SECRET_KEY`, `WHATSAPP_APP_SECRET`, or `WHATSAPP_VERIFY_TOKEN` is still at its built-in default and `APP_ENV != "local"` — the same fail-closed shape as the OTP bypass. These defaults are published in a public repository, so "obscure" is not a property any of them has, and unlike an open allow-list there is no legitimate reason to run this way outside local dev.
- Every placeholder is named in **one** message, so a misconfigured deploy fails once rather than three times.
- The default values are named constants in `config.py`, not string literals repeated in `main.py` — a duplicated literal would let someone change a default and silently disarm the check that exists to catch it.
- Secrets are never baked into images and never committed; `.env` is gitignored at the repository root.

### Provenance

- A chapter citation and its passage excerpt are attached **only** when retrieval cleared the grounded floor. The client renders a chapter chip identically whatever score sat behind it, so shipping one for a weak match would put the hedge in the prompt text and nowhere the student can see. This is a child-safety control, not a UI detail: the reader cannot check the claim.

### Supply chain

- `npm ci --ignore-scripts` in both the Dockerfile and CI. Lifecycle scripts execute arbitrary code from every transitive dependency at install time — the mechanism behind recent npm compromises.
- `npm audit --audit-level=high --omit=dev` gates the build. Dev dependencies are excluded deliberately: a vulnerability in the build toolchain is real but does not ship to a user's browser, and mixing the two trains everyone to ignore the output.
- Trivy scans both images and the Compose configuration, on every push **and on a daily schedule** — because an image that was clean when you shipped it and is not clean today is the failure that actually matters. `--ignore-unfixed` is set, because failing on a CVE with no available patch gives the team nothing to do except disable the gate.
- Backend dependencies are version-pinned; `requirements.txt` carries runtime only, so `pytest` and `ruff` never reach the production image.

---

## 3. Known gaps

Ordered by severity. None of these are hypothetical.

### 3.1 DPDP Rules 2025 parental consent is not implemented

India's DPDP Rules require **verifiable parental consent before** processing a minor's data. The current flow is student-initiated: the student invites a parent and relays a PIN out of band. A child can simply never link a parent, or link a friend's number.

This must be resolved before any real student's data is processed. It is a blocker for the pilot, not a Phase 2 nicety.

`scripts/delete_user.py` covers erasure-on-request across every table, which is necessary but not sufficient.

### 3.2 No TLS anywhere in this repository

Nothing here speaks HTTPS. The HSTS header nginx sets only means something once something upstream actually terminates TLS. A reverse proxy that does — Caddy, an ALB, a Cloudflare Tunnel — must sit in front before anything faces the internet.

### 3.3 No retention or cleanup jobs

`revoked_tokens`, `processed_webhook_messages` and `moderation_flags` accumulate indefinitely. Rows are only removed by `delete_user.py`. `moderation_flags` in particular holds verbatim sensitive content with no expiry — acceptable at pilot scale, wrong at any other.

### 3.4 In-process state does not survive a restart or a second container

The rate limiter (`_rate_buckets`), the PIN attempt counter (`_pin_attempts`) and the alert dedupe flag are module-level dictionaries. A restart resets them: worst case, an attacker gets five more PIN attempts per restart, and one duplicate page per day. Correct for one container; wrong the moment there are two. Both also grow without eviction — a few hundred bytes at pilot scale, noted so nobody "fixes" it into a Redis dependency prematurely.

### 3.5 Race on first contact

`get_or_create_user()` has no `ON CONFLICT` clause. Two concurrent first messages from the same new number race on the `phone_number` unique constraint. Low impact at pilot scale, but it is on the first code path an inbound message touches.

### 3.6 Open allow-list by default

`ALLOWED_PHONE_NUMBERS` empty means open. The application warns loudly at boot when this is true outside local dev, but does not refuse. Set it for any closed pilot.

---

## 4. Pre-deployment checklist

Before anything faces the internet:

- [ ] `SECRET_KEY` and `WHATSAPP_APP_SECRET` set to independent 48-byte random values
- [ ] `POSTGRES_PASSWORD` set to an independent random value
- [ ] `APP_ENV` set to something other than `local`
- [ ] `DEV_OTP_BYPASS=0` (and confirm the app boots, which proves the guard works)
- [ ] `ALLOWED_PHONE_NUMBERS` populated with the pilot cohort
- [ ] `DAILY_SPEND_CAP_USD` sized for the actual cohort, and `ALERT_WEBHOOK_URL` set and tested
- [ ] TLS terminator in front, HSTS verified
- [ ] `ALLOWED_WEB_ORIGINS` set to exact origins, never `*`
- [ ] A restore from `pg_dump` actually performed once, not assumed
- [ ] DPDP parental consent flow implemented and reviewed
- [ ] `git log -p -- .env` returns nothing, and no key has ever been committed