"""Curriculum retrieval.

Two design points carry most of the weight, both explained at their call sites:
hybrid dense+lexical fusion in _search(), and the two-pass grade strategy in retrieve().

Subject routing is done by cosine similarity, not by a keyword map. An earlier
hand-written keyword list covered none of `pressure`, `coal`, `friction`, `magnet`,
`solution` or `eclipse`, and routed a query mentioning "poem" to a subject with zero
rows. Re-introduce a filter only when several subjects exist AND the eval set shows
cross-subject noise costing recall.

Threshold classification lives in the orchestrator, not here: this returns everything
above the WEAK floor and lets the caller decide what "grounded" means.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import (
    LOWEST_GRADE,
    RAG_CANDIDATE_MULTIPLIER,
    RAG_LEXICAL_RESCUE,
    RAG_THRESHOLD,
    RAG_TOP_K,
    RAG_WEAK_THRESHOLD,
)

log = logging.getLogger("guruji.curriculum")


@dataclass
class Chunk:
    id: int
    chunk_text: str
    similarity: float
    # Every class has a "Chapter 6", so a citation without the class is ambiguous.
    grade: int = 0
    # Defaulted so positional construction Chunk(id, text, sim) still works in tests.
    subject: str = ""
    chapter_no: int = 0
    title: str = ""
    rrf_score: float = 0.0
    lexical_hit: bool = False

    def citation(self) -> str:
        """Human-readable provenance for the prompt. Empty when metadata is absent
        so it degrades to plain text rather than 'Chapter 0'."""
        if not self.title:
            return ""
        cls = f"Class {self.grade} " if self.grade else ""
        return f"[{cls}{self.subject} — Chapter {self.chapter_no}: {self.title}]"


def _search(
    db: Session,
    grade_min: int,
    grade_max: int,
    message: str,
    query_embedding: list[float],
    subject: str | None = None,
) -> list[Chunk]:
    """One hybrid pass over a grade RANGE. Callers use retrieve(), not this."""
    embedding_str = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"
    rows = db.execute(
        text(
            "SELECT id, chunk_text, subject, grade, chapter_no, title, similarity, rrf_score, lexical_hit "
            "FROM search_chunks(CAST(:emb AS vector), :q, :gmin, :gmax, :count, :subj)"
        ),
        {
            "emb": embedding_str,
            "q": message or "",
            "gmin": grade_min,
            "gmax": grade_max,
            "count": RAG_TOP_K * RAG_CANDIDATE_MULTIPLIER,
            "subj": subject,
        },
    ).all()
    candidates = [
        Chunk(
            id=r[0], chunk_text=r[1], subject=r[2] or "", grade=r[3] or 0,
            chapter_no=r[4] or 0, title=r[5] or "", similarity=r[6],
            rrf_score=r[7], lexical_hit=bool(r[8]),
        )
        for r in rows
    ]
    return [
        c for c in candidates
        if c.similarity > RAG_WEAK_THRESHOLD or (RAG_LEXICAL_RESCUE and c.lexical_hit)
    ][:RAG_TOP_K]


def retrieve(
    db: Session,
    grade: int,
    message: str,
    query_embedding: list[float],
    subject: str | None = None,
) -> list[Chunk]:
    """Hybrid retrieve across every subject at or below the student's grade.

    `message` should be the REWRITTEN standalone English query from the orchestrator's
    planner, not the raw student message — the lexical leg uses the 'english' text
    search config, and raw Hinglish ("pressure kya hota hai") stems poorly. Passing
    the raw message still works; it just gives up the lexical half of the fusion.

    Returns chunks above RAG_WEAK_THRESHOLD, best-fused-rank first. An empty list
    means genuinely nothing plausible exists in the corpus.

    TWO PASSES, because one pass over `grade <= N` stops working the moment more than
    one class is ingested:

      Pass 1  the student's OWN class only.
      Pass 2  their class and below — run only if pass 1 found nothing GROUNDED.

    Why it matters. NCERT Science teaches the same topics repeatedly at increasing
    depth — force in 6/8/9, light in 6/7/8/10, electricity in 6/7/8/10. Under a
    flat `grade <= N` filter a Class 10 query competes against six grades at once
    and cosine similarity picks the winner. Similarity has no concept of "more
    advanced", and simpler prose is often MORE lexically direct, so the Class 6
    chunk frequently wins. The student then gets a confident, correctly-cited
    answer pitched four years too low — worse than a refusal, because nothing
    about it looks wrong.

    Pass 2 still exists because a Class 10 student must be able to ask a Class 6
    question without judgement. It is a fallback, not the default: own class first,
    wider only on a miss.

    Cost of the second pass is one extra Postgres query (~50-100ms on a flat scan
    at this corpus size) and it runs only when the first pass came back weak or
    empty — i.e. exactly when the student was about to be refused anyway.
    """
    own = _search(db, grade, grade, message, query_embedding, subject)
    own_top = max((c.similarity for c in own), default=0.0)

    if own_top >= RAG_THRESHOLD:
        log.info(
            "retrieval pass=own grade=%d subject=%s query=%r kept=%d top=%.3f chapters=%s",
            grade, subject or "any", message[:80], len(own), own_top,
            [(c.grade, c.chapter_no) for c in own],
        )
        return own

    # Pass 2 widens the CLASS but keeps the subject. A student who opened a Maths
    # chat and asks something their class does not cover should get the lower
    # class's Maths, not a Science chapter that happens to score — the subject was
    # stated, not inferred, so overriding it would be overriding them.
    wider = _search(db, LOWEST_GRADE, grade, message, query_embedding, subject)
    wider_top = max((c.similarity for c in wider), default=0.0)

    # Keep whichever pass actually scored better. Pass 2 is a superset of pass 1,
    # so it can only tie or win on score — but comparing explicitly means a future
    # change to the filter cannot silently make the fallback worse than the thing
    # it replaced.
    chosen, which = (wider, "wider") if wider_top > own_top else (own, "own")
    log.info(
        "retrieval pass=%s grade=%d subject=%s query=%r kept=%d own_top=%.3f wider_top=%.3f chapters=%s",
        which, grade, subject or "any", message[:80], len(chosen), own_top, wider_top,
        [(c.grade, c.chapter_no) for c in chosen],
    )
    return chosen


def format_for_prompt(chunks: list[Chunk], max_chars: int = 5600) -> str:
    """Chapter-labelled context block. The label is what lets GuruJi say
    'yeh chapter 6 mein hai' instead of reciting anonymous prose."""
    parts: list[str] = []
    for c in chunks:
        cite = c.citation()
        parts.append(f"{cite}\n{c.chunk_text}" if cite else c.chunk_text)
    return "\n---\n".join(parts)[:max_chars]


def subjects_by_grade(db: Session) -> list[dict]:
    """What is actually ingested, per class.

    The UI must offer only real options: listing a subject whose book has not been
    ingested promises a book GuruJi cannot open. Derived from the corpus rather than a
    hardcoded list, so it can never drift from it.
    """
    rows = db.execute(
        text(
            "SELECT d.grade, d.subject, count(DISTINCT d.id) AS chapters "
            "FROM curriculum_documents d "
            "JOIN curriculum_chunks c ON c.document_id = d.id "
            "GROUP BY d.grade, d.subject ORDER BY d.grade, d.subject"
        )
    ).all()
    return [{"grade": r[0], "subject": r[1], "chapters": r[2]} for r in rows]
