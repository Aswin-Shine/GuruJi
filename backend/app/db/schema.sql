CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'parent', 'admin')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL REFERENCES users(id),
    grade INT NOT NULL CHECK (grade BETWEEN 5 AND 10),
    board TEXT NOT NULL DEFAULT 'NCERT',
    preferred_language TEXT DEFAULT 'hinglish',
    onboarded_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parent_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_user_id UUID NOT NULL REFERENCES users(id),
    student_id UUID NOT NULL REFERENCES students(id),
    verified_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id),
    channel TEXT NOT NULL CHECK (channel IN ('whatsapp', 'web')),
    started_at TIMESTAMPTZ DEFAULT now(),
    last_message_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    sender TEXT NOT NULL CHECK (sender IN ('student', 'assistant')),
    content TEXT NOT NULL,
    tokens_used INT,
    model_used TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS student_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID UNIQUE NOT NULL REFERENCES students(id),
    summary_jsonb JSONB NOT NULL DEFAULT '{}',
    confidence_score FLOAT DEFAULT 0.5,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS curriculum_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    grade INT NOT NULL,
    chapter_no INT NOT NULL,
    title TEXT NOT NULL,
    source_file_url TEXT,
    ingested_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS curriculum_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES curriculum_documents(id),
    chunk_text TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    chunk_index INT NOT NULL,
    token_count INT
);

-- Lexical half of the hybrid retrieval fusion. A GENERATED column, so existing rows
-- backfill with no re-embedding and no ingest run. NCERT prose is terminology-dense
-- and students type exact chapter nouns ("pressure", "convex mirror"); an exact-term
-- match is what cosine similarity is worst at and a text index is best at.
ALTER TABLE curriculum_chunks
    ADD COLUMN IF NOT EXISTS chunk_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector(CAST('english' AS regconfig), chunk_text)) STORED;

CREATE INDEX IF NOT EXISTS curriculum_chunks_tsv_idx ON curriculum_chunks USING GIN (chunk_tsv);
-- The grade filter is a range scan, so the join column earns an index even at pilot
-- size. Still no ANN index on `embedding`: at this corpus size a flat cosine scan is
-- not the bottleneck. Revisit past ~100k chunks.
CREATE INDEX IF NOT EXISTS curriculum_documents_grade_idx ON curriculum_documents (grade);

-- Student-authored profile. display_name is what the child types, NOT the
-- phone-derived identity: a student sharing a parent's phone with a sibling needs
-- their own name on screen. `avatar` is a chosen glyph key, never an upload — there
-- is no object store, no image moderation, and no lawful basis under DPDP to store a
-- photograph of a minor.
ALTER TABLE students ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS avatar TEXT;

-- A conversation can be closed by the student ("start fresh") or removed from their
-- list. Soft-delete, not DELETE: messages are the evidence behind the parent-review
-- promise, and a child tapping a bin icon must not be able to erase a flagged
-- exchange. Hidden from the student, intact for the parent.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title TEXT;

-- The class belongs to the CONVERSATION, not only the student. One phone is shared by
-- siblings in different classes; with grade held only on students, the younger sibling
-- would have to overwrite the older one's profile to reach their own textbook.
-- NULL means "use the student's profile grade".
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS grade INT;

-- Subject, same treatment as grade. NULL means "any subject". Stamped at creation,
-- ignored thereafter.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS subject TEXT;

-- Subject is now a filter predicate, so the join column earns an index.
CREATE INDEX IF NOT EXISTS curriculum_documents_subject_idx
  ON curriculum_documents (subject, grade);

-- Provenance persisted ON the message, not just returned once. Without these, a
-- citation is renderable only during the live turn and lost on reload — History would
-- show the same replies stripped of the one thing that distinguishes GuruJi from any
-- other chatbot. Nullable, because inventing provenance is worse than showing none.
--   grounding: grounded | weak | empty | not_needed  (NULL for student messages)
--   citation:  display string, e.g. "Science - Chapter 6: Pressure, Winds..."
ALTER TABLE messages ADD COLUMN IF NOT EXISTS grounding TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS citation TEXT;

-- Append-only spend ledger. One row per paid chat call, tutoring AND memory
-- summarisation, priced at write time with the correct input/output split.
-- Embedding spend is deliberately not recorded — noise at any realistic volume.
-- No index on created_at: the table stays tiny at pilot scale.
CREATE TABLE IF NOT EXISTS llm_spend (
    id BIGSERIAL PRIMARY KEY,
    cost_usd DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Flagged exchanges are PERSISTED, not just logged: parents are promised they can
-- review what their child tried to ask, and a log line is not that. Content is stored
-- verbatim because a redacted flag is useless to a parent.
-- RETENTION: erasure via scripts/delete_user.py only. No automatic expiry yet.
CREATE TABLE IF NOT EXISTS moderation_flags (
    id BIGSERIAL PRIMARY KEY,
    student_id UUID NOT NULL REFERENCES students(id),
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    content TEXT NOT NULL,
    flagged_at TIMESTAMPTZ DEFAULT now()
);

-- confidence_score was never written by any code path. Dead schema is a lie about
-- intent, so it is dropped rather than left standing as a promise.
ALTER TABLE student_memory DROP COLUMN IF EXISTS confidence_score;

-- Webhook idempotency. Meta delivers at-least-once and retries anything slower than
-- its ~3-5s ack window, which the synchronous pipeline routinely is. A row is CLAIMED
-- before processing and DELETED if processing raises, so a retry reprocesses rather
-- than the message being silently swallowed.
-- No TTL or cleanup job yet — same accepted debt as revoked_tokens below.
CREATE TABLE IF NOT EXISTS processed_webhook_messages (
    whatsapp_message_id TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT now()
);

-- Session revocation (Phase 1 addition, does not alter any locked table).
-- A token is invalid if its jti appears here. Rows expire naturally past token TTL.
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    revoked_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- match_chunks() is retired, not kept as an overload: leaving both would let a stale
-- call site silently keep the old grade-equality behaviour. Dropped by full signature
-- so this file stays idempotent on a fresh database too.
DROP FUNCTION IF EXISTS match_chunks(VECTOR(1536), FLOAT, INT, INT, TEXT);

-- search_chunks is DROPped before every CREATE rather than CREATE OR REPLACE'd.
-- Postgres refuses to replace a function whose OUT columns changed, so the moment a
-- column is added to the result set, init_db() throws on every database that already
-- has the old version and the app fails to boot. A fresh database would never show
-- this. Dropping first leaves the return set free to evolve; no data lives in a
-- function, and every call site is in this repository.

-- Hybrid search: dense (cosine) and lexical (ts_rank_cd) legs fused with Reciprocal
-- Rank Fusion. RRF is used rather than a weighted score blend because cosine
-- similarity and ts_rank_cd are on incomparable scales — ranks are comparable, raw
-- scores are not, and a blend needs a weight nobody has measured.
--
-- k=60 is the standard RRF damping constant from the original Cormack et al. paper;
-- it is not tuned for this corpus and does not need to be at 579 chunks.
--
-- `similarity` in the output is always the true cosine score (computed for lexical-only
-- hits too), because the orchestrator's grounded/weak/empty decision reads it and must
-- not be fooled by a strong lexical rank on a semantically unrelated chunk.
DROP FUNCTION IF EXISTS search_chunks(VECTOR(1536), TEXT, INT, INT);
-- Superseded signatures. The grade filter became a RANGE so retrieval can ask for
-- "this student's own class only" and, separately, "their class or below"; a later
-- change added filter_subject. Each older signature is dropped by name.
DROP FUNCTION IF EXISTS search_chunks(VECTOR(1536), TEXT, INT, INT, INT);
DROP FUNCTION IF EXISTS search_chunks(VECTOR(1536), TEXT, INT, INT, INT, TEXT);

CREATE FUNCTION search_chunks (
    query_embedding VECTOR(1536),
    query_text TEXT,
    grade_min INT,
    grade_max INT,
    match_count INT,
    -- NULL means every subject. A chosen subject is the student's stated intent,
    -- which is a different thing from inferring one from the question text.
    filter_subject TEXT DEFAULT NULL
)
RETURNS TABLE (
    id BIGINT,
    chunk_text TEXT,
    subject TEXT,
    grade INT,
    chapter_no INT,
    title TEXT,
    similarity FLOAT,
    rrf_score FLOAT,
    lexical_hit BOOLEAN
)
LANGUAGE sql STABLE AS $$
    WITH dense AS (
        SELECT c.id AS cid,
               1 - (c.embedding <=> query_embedding) AS sim,
               ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding ASC) AS rnk
        FROM curriculum_chunks c
        JOIN curriculum_documents d ON d.id = c.document_id
        WHERE d.grade BETWEEN grade_min AND grade_max
          AND (filter_subject IS NULL OR d.subject = filter_subject)
        ORDER BY c.embedding <=> query_embedding ASC
        LIMIT match_count * 4
    ),
    lexical AS (
        SELECT c.id AS cid,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(c.chunk_tsv, plainto_tsquery(CAST('english' AS regconfig), query_text)) DESC
               ) AS rnk
        FROM curriculum_chunks c
        JOIN curriculum_documents d ON d.id = c.document_id
        WHERE d.grade BETWEEN grade_min AND grade_max
          AND (filter_subject IS NULL OR d.subject = filter_subject)
          AND length(btrim(query_text)) > 0
          AND c.chunk_tsv @@ plainto_tsquery(CAST('english' AS regconfig), query_text)
        ORDER BY ts_rank_cd(c.chunk_tsv, plainto_tsquery(CAST('english' AS regconfig), query_text)) DESC
        LIMIT match_count * 4
    ),
    fused AS (
        SELECT COALESCE(dense.cid, lexical.cid) AS cid,
               COALESCE(1.0 / (60 + dense.rnk), 0.0)
             + COALESCE(1.0 / (60 + lexical.rnk), 0.0) AS rrf,
               (lexical.cid IS NOT NULL) AS lex
        FROM dense
        FULL OUTER JOIN lexical ON dense.cid = lexical.cid
    )
    SELECT c.id,
           c.chunk_text,
           d.subject,
           d.grade,
           d.chapter_no,
           d.title,
           1 - (c.embedding <=> query_embedding) AS similarity,
           f.rrf AS rrf_score,
           f.lex AS lexical_hit
    FROM fused f
    JOIN curriculum_chunks c ON c.id = f.cid
    JOIN curriculum_documents d ON d.id = c.document_id
    ORDER BY f.rrf DESC, similarity DESC
    LIMIT match_count;
$$;
