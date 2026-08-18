# Corpus Transfer

How to ingest the NCERT corpus once on a workstation and move it to a server, instead of ingesting on the server.

---

## Why not just ingest on the server

Three reasons, in order of how much they cost you.

**Memory.** Ingestion raises the `api` container's limit to 2 GB (`docker-compose.ingest.yml`). Added to Postgres, nginx and Caddy that is a ~3.6 GB peak, against ~2.2 GB in normal operation. Sizing a server for a batch job it runs a handful of times a year means paying for that headroom every hour of every day.

**Money and time.** Ingestion computes an embedding for every chunk. Doing it on the server means paying for those calls again, and re-running the whole batch if a server is ever rebuilt.

**Blast radius.** A chunking bug, a mislabelled subject, a PDF with no text layer — all of these are better discovered on a laptop than on the machine students are using.

Moving the finished corpus instead removes all three. The embedding vectors survive the transfer byte-for-byte, so a restored corpus retrieves identically to the one it came from.

---

## The workflow

### 1. Ingest locally

Normal ingestion, on the machine that has the PDFs. See [IMPLEMENTATION.md §5](../IMPLEMENTATION.md#5-ingest-a-corpus).

```bash
./scripts/corpus.sh status
```

```
 grade | subject | chapters | chunks
-------+---------+----------+--------
     5 | EVS     |       10 |    520
     6 | Science |       12 |    624
     ...
```

Check this before exporting. `status` reads the same tables the class picker does, so if a class is missing or mislabelled here, it is wrong in the product too.

### 2. Export

```bash
./scripts/corpus.sh export
# exported 3796 chunks -> corpus-2026-08-18.dump (20M)
```

Roughly **20 MB** for a six-class corpus. The uncompressed form is 53 MB; the vectors are float text and compress about 2.6×.

### 3. Transfer

```bash
scp corpus-2026-08-18.dump ec2-user@your-host:~/
```

### 4. Import on the server

The stack must have booted at least once, because `init_db()` owns the schema and the dump carries data only.

```bash
./scripts/corpus.sh import corpus-2026-08-18.dump
```

Restore takes about **4 seconds** for 3,796 chunks.

---

## What the dump contains

Exactly two tables: `curriculum_documents` and `curriculum_chunks`.

**Not** a full `pg_dump`. A whole-database dump would carry local test students, their conversations and their `moderation_flags` onto the server — noise at best, and test data about children at worst.

Three things are handled for you, each of which breaks this kind of transfer when done by hand:

| | |
|---|---|
| `chunk_tsv` | A `GENERATED ALWAYS` column. `pg_dump` omits it from the column list and Postgres rebuilds it on insert. Including it makes the restore fail outright. |
| `curriculum_chunks.id` | `BIGSERIAL`. The dump carries a `setval` for the sequence, so ingesting more chapters after a restore does not collide on duplicate keys. |
| Foreign keys | Documents are written before chunks, so `curriculum_chunks.document_id` always resolves. |

---

## Verifying a restore

```bash
./scripts/corpus.sh status
```

Chapter and chunk counts must match the source exactly. For a deeper check:

```sql
-- the generated column rebuilt for every row
SELECT count(*) FILTER (WHERE chunk_tsv IS NOT NULL), count(*) FROM curriculum_chunks;

-- vectors arrived intact and at full width
SELECT vector_dims(embedding) FROM curriculum_chunks LIMIT 1;   -- 1536

-- no chunk lost its parent document
SELECT count(*) FROM curriculum_chunks c
  LEFT JOIN curriculum_documents d ON d.id = c.document_id
 WHERE d.id IS NULL;                                            -- 0
```

The real test is the product: open a chat, ask a question you know is in the corpus, and confirm the citation chip names the right chapter.

---

## Importing over an existing corpus

`pg_restore --data-only` **appends**. Running an import twice gives you every chapter twice, and duplicate chunks distort retrieval without raising an error — the failure is silent and looks like a quality regression.

`corpus.sh import` detects a non-empty corpus, refuses to proceed silently, and offers to truncate first. Answering anything but `y` aborts and changes nothing.

Doing it by hand, truncate yourself:

```sql
TRUNCATE curriculum_chunks, curriculum_documents RESTART IDENTITY CASCADE;
```

---

## Keep the dumps

Name them by date and keep them. A corpus dump is a **versioned artifact**:

- Rolling back a bad re-ingest costs a restore instead of re-embedding everything.
- Rebuilding a server from scratch is boot, import, done.
- A dump paired with an evaluation run makes that run reproducible — otherwise a number in [evaluation-runs.md](evaluation-runs.md) refers to a corpus you can no longer reconstruct.

They contain no personal data — only NCERT text and its embeddings — so they are safe to keep alongside backups. They are **not** safe to commit: `*.dump` is gitignored, and 20 MB of vectors does not belong in git history.

---

## Command reference

```
./scripts/corpus.sh status            what is currently ingested
./scripts/corpus.sh export [file]     write the corpus to a .dump
./scripts/corpus.sh import <file>     load a .dump into this stack
```

Overridable by environment variable: `DB_SERVICE` (default `db`), `POSTGRES_USER` and `POSTGRES_DB` (default `guruji`).
