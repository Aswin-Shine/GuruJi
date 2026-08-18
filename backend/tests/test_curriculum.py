"""Curriculum ingestion + retrieval tests.

Tests here READ threshold constants from config rather than restating them. Asserting
a literal makes the suite go red guarding a value that has already moved, and config
must stay the single place the number lives.

Deleted with their subject: every test of SUBJECT_KEYWORDS / detect_subjects /
anchor_text. Those covered a routing layer that no longer exists.
"""
import math

import pytest
from sqlalchemy import text as _t

from app.config import LOWEST_GRADE, RAG_LEXICAL_RESCUE, RAG_THRESHOLD, RAG_WEAK_THRESHOLD
from app.modules.curriculum import service as curriculum
from ingest_curriculum import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    build_header,
    chunk_document,
    chunk_text,
    clean_pdf_noise,
)


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def test_chunking_respects_size_budget():
    """Body never exceeds CHUNK_SIZE; the carried overlap prefix is the only slack."""
    body = "\n\n".join("Sentence number %d about pressure and force. " % i * 3 for i in range(40))
    chunks = chunk_text(body)
    assert chunks
    assert all(len(c) <= CHUNK_SIZE + CHUNK_OVERLAP for c in chunks)


def test_chunking_prefers_paragraph_boundaries():
    """Whole paragraphs must survive intact when they fit.

    A fixed-character window left 94% of chunks ending mid-sentence."""
    paragraphs = ["Pressure is force per unit area." * 3, "A cyclone forms over warm water." * 3]
    chunks = chunk_text("\n\n".join(paragraphs), size=900, overlap=0)
    assert len(chunks) == 1  # both fit in one chunk
    assert chunks[0].rstrip().endswith(".")


def test_oversized_paragraph_splits_on_sentence_boundary():
    long_para = " ".join(f"This is sentence {i} of a very long paragraph." for i in range(60))
    chunks = chunk_text(long_para, size=300, overlap=50)
    assert len(chunks) > 1
    # Most cuts land after a full stop rather than mid-word.
    clean_ends = sum(1 for c in chunks[:-1] if c.rstrip().endswith("."))
    assert clean_ends >= len(chunks) // 2


def test_chunking_empty():
    assert chunk_text("   ") == []


def test_every_chunk_carries_its_chapter_header():
    """Measured on the real ch06.pdf: only 80% of chunks contained the word
    'pressure' at all. The header takes that to 100% and is what lets the tutoring
    prompt cite a chapter instead of quoting anonymous prose."""
    header = build_header("Science", 8, 6, "Pressure, Winds, Storms and Cyclones")
    chunks = chunk_document("Para one about balloons.\n\nPara two about pipes.", header)
    assert chunks
    assert all(c.startswith(header) for c in chunks)
    assert all("Chapter 6" in c for c in chunks)


def test_clean_pdf_noise_strips_running_headers_and_stamps():
    raw = (
        "Curiosity — Textbook of Science for Grade 8\n"
        "Pressure is force per unit area.\n"
        "Chapter 6.indd   81\n6/28/2025   11:38:53 AM\nReprint 2025-26\n"
        "10-Sep-25   2:13:15 PM\n"
    )
    cleaned = clean_pdf_noise(raw)
    assert "Pressure is force per unit area." in cleaned
    for artifact in ("Curiosity —", ".indd", "11:38:53", "Reprint 2025", "2:13:15"):
        assert artifact not in cleaned


# --------------------------------------------------------------------------
# Retrieval (real pgvector — compose `db` must be up)
# --------------------------------------------------------------------------

def _vec(a: float, b: float, c: float = 0.0) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in [a, b, c] + [0.0] * 1533) + "]"


# A nonsense token no NCERT chapter contains, so the lexical leg can be exercised
# without colliding with the real ingested corpus sitting in the same database.
MARK = "zqxfix26"


def _own(chunks) -> list:
    """Only the rows this fixture created.

    These tests run against the SAME database that holds the real 700+ chunk NCERT
    corpus — the original suite assumed an otherwise-empty table, which made it pass
    on a fresh volume and fail on a developer machine that had actually ingested
    something. Filtering to owned rows makes every assertion below independent of
    whatever else is in the table."""
    return [c for c in chunks if MARK in c.chunk_text]


@pytest.fixture()
def seeded(db):
    """Two docs at different grades so the grade-range filter is observable.
    Owns and wipes only rows it creates."""
    def wipe():
        db.execute(_t(
            "DELETE FROM curriculum_chunks WHERE document_id IN "
            "(SELECT id FROM curriculum_documents WHERE title LIKE 'fix26-%')"
        ))
        db.execute(_t("DELETE FROM curriculum_documents WHERE title LIKE 'fix26-%'"))
        db.commit()

    wipe()
    g6 = db.execute(_t(
        "INSERT INTO curriculum_documents (subject, grade, chapter_no, title) "
        "VALUES ('Science', 6, 3, 'fix26-lower-grade') RETURNING id"
    )).scalar_one()
    g8 = db.execute(_t(
        "INSERT INTO curriculum_documents (subject, grade, chapter_no, title) "
        "VALUES ('Science', 8, 6, 'fix26-same-grade') RETURNING id"
    )).scalar_one()
    rows = [
        (g8, f"{MARK}-A-exact pressure force per unit area", _vec(1.0, 0.0)),          # sim 1.0
        (g8, f"{MARK}-B-lexical {MARK}cyclone winds", _vec(0.6, 0.8)),                 # sim 0.6
        (g8, f"{MARK}-C-far unrelated text", _vec(0.3, math.sqrt(1 - 0.09))),          # sim 0.3
        (g6, f"{MARK}-D-grade6 magnets and compass", _vec(0.9, math.sqrt(1 - 0.81))),  # sim 0.9
        # a LOWER-class chunk that outscores the student's own class on
        # the same topic. This is the multi-grade failure the two-pass fix exists
        # to prevent — under a flat `grade <= N` filter this row wins and a Class 8
        # student is taught the Class 6 version, confidently and with a citation.
        (g6, f"{MARK}-E-grade6 pressure simple version", _vec(0.98, math.sqrt(1 - 0.9604))),
        # On a THIRD axis, so a query pointed at it scores exactly 0 against every
        # Class 8 row. That is the only way to make "own class is a genuine miss"
        # provable — in the 2-D plane the Class 6 and Class 8 vectors sit close
        # enough that no query can ground one without grounding the other.
        (g6, f"{MARK}-F-grade6 only lives here photosynthesis", _vec(0.0, 0.0, 1.0)),
    ]
    for i, (doc, txt, emb) in enumerate(rows):
        db.execute(_t(
            "INSERT INTO curriculum_chunks (document_id, chunk_text, embedding, chunk_index) "
            "VALUES (:d, :t, CAST(:e AS vector), :i)"
        ), {"d": str(doc), "t": txt, "e": emb, "i": i})
    db.commit()
    yield
    wipe()


def test_retrieve_orders_by_relevance_and_applies_weak_floor(db, seeded):
    query = [1.0, 0.0] + [0.0] * 1534
    mine = _own(curriculum.retrieve(db, 8, f"{MARK} pressure force per unit area", query))
    assert mine, "fixture rows did not survive retrieval"
    assert f"{MARK}-A-exact" in mine[0].chunk_text
    # Ordering is by fused rank; the invariant that matters is that the best cosine
    # leads and nothing below the floor survives without lexical evidence.
    assert all(c.similarity > RAG_WEAK_THRESHOLD or c.lexical_hit for c in mine)


def test_lower_grade_content_is_retrievable(db, seeded):
    """The Workbook requires a Class 8 student to be able to ask a Class 6 question
    without judgement. Relaxing `grade =` to a range made that possible; making the
    range a FALLBACK rather than the default keeps lower-class chunks from outranking
    the student's own book.

    So the promise is now tested through the path that actually delivers it: a
    query with nothing grounded in the student's own class must still reach the
    lower class. The previous version of this test asserted the flat behaviour and
    would have locked in the bug — it is rewritten, not deleted, because the
    product promise it guards has not changed."""
    # Orthogonal to every Class 8 row, so pass 1 is a genuine miss (top similarity
    # 0.0) and pass 2 must fire for the student to get anything at all.
    query = [0.0, 0.0, 1.0] + [0.0] * 1533
    chunks = _own(curriculum.retrieve(db, 8, f"{MARK}-F-grade6 photosynthesis", query))
    assert chunks, "fallback pass returned nothing"
    assert any(c.grade == 6 and "-F-grade6" in c.chunk_text for c in chunks)


def test_higher_grade_content_stays_hidden(db, seeded):
    """The range filter must still be a filter: grade 6 must not see grade 8 rows."""
    query = [1.0, 0.0] + [0.0] * 1534
    texts = {c.chunk_text for c in _own(curriculum.retrieve(db, 6, f"{MARK} pressure force", query))}
    assert not any("-A-exact" in t for t in texts)
    assert not any("-B-lexical" in t for t in texts)


def test_lexical_leg_finds_exact_term_dense_would_rank_low(db, seeded):
    """Hybrid retrieval's reason to exist: an orthogonal query embedding gives every
    chunk a near-zero cosine score, but the exact term still surfaces its chunk
    through the tsvector leg of the fusion."""
    # Axis 4: axis 3 is chunk-F's home, so a query there is no longer orthogonal
    # to the corpus and these tests would measure a dense hit instead of a
    # lexical one.
    orthogonal = [0.0, 0.0, 0.0, 1.0] + [0.0] * 1532
    rows = db.execute(
        _t("SELECT chunk_text, similarity, lexical_hit "
           "FROM search_chunks(CAST(:e AS vector), :q, :gmin, :gmax, :c)"),
        {"e": "[" + ",".join(f"{v:.8f}" for v in orthogonal) + "]",
         "q": f"{MARK}cyclone winds", "gmin": 5, "gmax": 8, "c": 10},
    ).all()
    hit = [r for r in rows if f"{MARK}cyclone" in r[0]]
    assert hit, "exact term not surfaced by the lexical leg"
    assert hit[0][2] is True                # flagged as a lexical match
    assert hit[0][1] < RAG_WEAK_THRESHOLD   # and dense alone would have dropped it


@pytest.mark.skipif(not RAG_LEXICAL_RESCUE, reason="lexical rescue disabled in config")
def test_lexical_rescue_survives_the_cosine_floor(db, seeded):
    """Without the rescue, a lexical-only match is discarded by the cosine floor and
    the lexical half of the fusion does nothing at retrieve() level. With it, the
    chunk is kept — and _grounding() still reads max cosine, so it lands in the WEAK
    band (hedged answer), never GROUNDED (confident answer)."""
    # Axis 4: axis 3 is chunk-F's home, so a query there is no longer orthogonal
    # to the corpus and these tests would measure a dense hit instead of a
    # lexical one.
    orthogonal = [0.0, 0.0, 0.0, 1.0] + [0.0] * 1532
    mine = _own(curriculum.retrieve(db, 8, f"{MARK}cyclone winds", orthogonal))
    assert any(f"{MARK}cyclone" in c.chunk_text for c in mine)
    assert max(c.similarity for c in mine) < RAG_THRESHOLD  # cannot claim grounding


def test_chunk_metadata_reaches_the_prompt(db, seeded):
    query = [1.0, 0.0] + [0.0] * 1534
    mine = _own(curriculum.retrieve(db, 8, f"{MARK} pressure force per unit area", query))
    top = mine[0]
    assert top.subject == "Science" and top.chapter_no == 6
    assert "Chapter 6" in top.citation()
    assert "Chapter 6" in curriculum.format_for_prompt(mine)


def test_own_class_wins_over_a_higher_scoring_lower_class_chunk(db, seeded):
    """The whole point of two-pass retrieval.

    chunk-E is Class 6 and scores 0.98; chunk-A is Class 8 and scores 1.0 — but
    the Class 6 row would still crowd the top-k under a single `grade <= N` pass.
    A Class 8 student must be answered from Class 8 material whenever Class 8
    material is grounded at all."""
    query = [1.0, 0.0] + [0.0] * 1534
    mine = _own(curriculum.retrieve(db, 8, f"{MARK} pressure force per unit area", query))
    assert mine, "nothing retrieved"
    assert all(c.grade == 8 for c in mine), [(c.grade, c.chunk_text[:24]) for c in mine]


def test_falls_back_to_lower_class_when_own_class_has_nothing(db, seeded):
    """The Workbook requires a Class 10 student to be able to ask a Class 6
    question. Pass 2 must still fire when the student's own class is a miss.

    This test needs grade 7 to be EMPTY, which is true of a fixture-only database
    and false the moment Class 7 is ingested — the real corpus has ~502 chunks
    there and pass 1 would ground on them, so pass 2 never fires and the assertion
    fails for a reason that has nothing to do with the code under test. Skipping is
    honest; asserting would make a green suite depend on which classes happen to be
    loaded. The same guarantee is covered unconditionally by
    test_lower_grade_content_is_retrievable, which uses an axis no real chunk
    occupies."""
    real_at_7 = db.execute(_t(
        "SELECT count(*) FROM curriculum_chunks c JOIN curriculum_documents d "
        "ON d.id = c.document_id WHERE d.grade = 7 AND d.title NOT LIKE 'fix26-%'"
    )).scalar_one()
    if real_at_7:
        pytest.skip(f"grade 7 has {real_at_7} ingested chunks; needs an empty grade")
    query = [0.9, math.sqrt(1 - 0.81)] + [0.0] * 1534
    mine = _own(curriculum.retrieve(db, 7, f"{MARK} magnets and compass", query))
    assert mine, "fallback pass returned nothing"
    assert all(c.grade < 7 for c in mine)


def test_citation_names_the_class(db, seeded):
    """Every class has a Chapter 6. A citation without the class is ambiguous, and
    ambiguous in exactly the direction that matters."""
    query = [1.0, 0.0] + [0.0] * 1534
    top = _own(curriculum.retrieve(db, 8, f"{MARK} pressure force per unit area", query))[0]
    assert top.grade == 8
    assert "Class 8" in top.citation() and "Chapter 6" in top.citation()


def test_lowest_grade_floor_is_sane():
    assert 1 <= LOWEST_GRADE <= 5


def test_thresholds_are_ordered_and_come_from_config():
    """Guards the drift that made the old suite red: no test restates the number."""
    assert 0.0 < RAG_WEAK_THRESHOLD <= RAG_THRESHOLD < 1.0
