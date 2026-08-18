"""Offline NCERT ingestion: PDF -> chunks -> embeddings -> Postgres.
Run manually per new chapter. NEVER imported by the live app.

Usage:
  python scripts/ingest_curriculum.py chapter.pdf --subject Science --grade 8 \
      --chapter-no 3 --title "Coal and Petroleum"

Two decisions here determine retrieval quality more than anything downstream:

  1. CONTEXTUAL CHUNK HEADERS. Every chunk carries
     "Class 8 Science — Chapter 6: Pressure, Winds, Storms, and Cyclones" as a prefix
     that is both embedded and stored. Without it, only ~80% of a chapter's chunks
     contain that chapter's own defining term, so a fifth of the best chapter is
     invisible to the query it exists to answer. The header also gives the tutoring
     prompt something to cite.

  2. PARAGRAPH-AWARE CHUNKING. A fixed-character window left 94% of chunks ending
     mid-sentence. NCERT is activity-led — "Activity 6.3", "Probe and ponder" are
     self-contained units a blind window shreds.

Changing either means RE-INGESTING: existing rows were built by the old logic.
Re-embedding the whole corpus costs about a cent, so treat it as free.
"""
import argparse
import re
import sys
import uuid

from pypdf import PdfReader
from sqlalchemy import text

# Puts backend/ on sys.path; must come before any `app.*` import.
import _bootstrap  # noqa: F401  isort:skip

from app.config import EMBEDDING_MODEL  # SAME model as query time — non-negotiable
from app.db.session import SessionLocal, init_db
from app.modules.ai_orchestrator import llm

CHUNK_SIZE = 900       # 800-1000 chars: NCERT explanations need context per chunk
CHUNK_OVERLAP = 150    # ~17% of chunk size, inside the standard 10-20% band


def clean_pdf_noise(raw_text: str) -> str:
    """Strips page-production boilerplate that NCERT's InDesign export embeds
    as real, selectable text on every page — not the "not to be republished /
    (c) NCERT" watermark, which is a rasterized image and never reaches
    pypdf's extract_text() at all (checked directly: zero occurrences across
    a real chapter). This is a different, unrelated artifact: the .indd
    filename+timestamp stamp and the "Reprint YYYY-YY" footer, both of which
    land mid-sentence at page boundaries and would otherwise get embedded and
    later injected into a tutoring prompt verbatim.

    Two date formats confirmed present in the same PDF (checked all 7 pages
    of a real chapter, not assumed from one): "6/28/2025   11:38:53 AM" on
    most pages, "10-Sep-25   2:13:15 PM" on the last one — InDesign stamps
    whatever the machine's last-save time was, in whatever format that
    machine used, so this isn't guaranteed consistent even within one file.

    The running page header repeats on every page. Left in, it lands in roughly one
    chunk in two and adds a constant vector component to half the corpus, compressing
    the score gap between on-topic and off-topic chunks — the exact gap the threshold
    has to sit inside. Chapter identity is not lost: it is re-added deliberately, once,
    as the contextual header in chunk_document().
    """
    raw_text = re.sub(r"Chapter \d+\.indd\s+\d+", "", raw_text)
    raw_text = re.sub(
        r"(?:\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-[A-Za-z]{3}-\d{2})\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM)?",
        "",
        raw_text,
    )
    raw_text = re.sub(r"Reprint \d{4}-\d{2}", "", raw_text)
    # Running headers/footers, both directions of the em-dash NCERT uses.
    raw_text = re.sub(r"Curiosity\s*[—-]\s*Textbook of Science for Grade \d+", "", raw_text)
    raw_text = re.sub(r"Chapter\s*\d+\s*[—-]\s*[A-Z][^\n]{0,80}\n", "\n", raw_text)
    raw_text = re.sub(r"[ \t]{2,}", " ", raw_text)   # collapse gaps the removals leave behind
    raw_text = re.sub(r"\n{3,}", "\n\n", raw_text)   # same, for blank lines
    return raw_text


def build_header(subject: str, grade: int, chapter_no: int, title: str) -> str:
    """The contextual prefix carried by every chunk of this document."""
    return f"Class {grade} {subject} — Chapter {chapter_no}: {title}"


def _split_oversized(paragraph: str, size: int, overlap: int) -> list[str]:
    """A single paragraph longer than one chunk. Split on sentence boundaries so the
    cut lands between sentences rather than mid-word, falling back to a hard window
    only for text with no sentence punctuation at all (tables, formula runs)."""
    sentences = re.split(r"(?<=[.?!])\s+", paragraph)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > size:  # no usable boundary — hard-window this one
            if current:
                out.append(current)
                current = ""
            for start in range(0, len(sentence), size - overlap):
                piece = sentence[start:start + size].strip()
                if piece:
                    out.append(piece)
            continue
        if current and len(current) + len(sentence) + 1 > size:
            out.append(current)
            current = current[-overlap:].lstrip() + " " + sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def chunk_text(full_text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware packing to a ~size target, with a character overlap carried
    from the tail of the previous chunk.

    Chunks may slightly exceed `size` only via the carried overlap prefix; the body
    packed into each chunk never does. Callers that need a hard ceiling should read
    `size + overlap` as the real bound.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", full_text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> str:
        """Close the current chunk and return the overlap tail to seed the next."""
        nonlocal current
        if not current:
            return ""
        chunks.append(current)
        tail = current[-overlap:] if overlap else ""
        current = ""
        return tail.lstrip()

    for paragraph in paragraphs:
        if len(paragraph) > size:
            flush()
            chunks.extend(_split_oversized(paragraph, size, overlap))
            continue
        if current and len(current) + len(paragraph) + 2 > size:
            tail = flush()
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph
    flush()
    return [c for c in chunks if c.strip()]


def chunk_document(full_text: str, header: str) -> list[str]:
    """Chunks with the contextual header prepended to each. The header goes into the
    STORED text as well as the embedded text, so the tutoring prompt can cite the
    chapter and the lexical index can match on the chapter title."""
    return [f"{header}\n\n{piece}" for piece in chunk_text(full_text)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--grade", type=int, required=True)
    parser.add_argument("--chapter-no", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Chunk and print stats only. No embeddings, no database writes, nothing spent.",
    )
    args = parser.parse_args()

    full_text = "\n".join((page.extract_text() or "") for page in PdfReader(args.pdf_path).pages)
    full_text = clean_pdf_noise(full_text)
    if not full_text.strip():
        sys.exit("No extractable text in PDF (scanned image? needs OCR — out of Phase 1 scope).")

    header = build_header(args.subject, args.grade, args.chapter_no, args.title)
    chunks = chunk_document(full_text, header)

    if args.dry_run:
        bodies = [c.split("\n\n", 1)[-1] for c in chunks]
        mid_end = sum(1 for b in bodies if b and b.rstrip()[-1] not in ".?!\"")
        print(f"{len(chunks)} chunks, mean {sum(map(len, bodies)) // max(len(bodies), 1)} chars")
        print(f"ending mid-sentence: {mid_end}/{len(bodies)} ({mid_end / max(len(bodies), 1):.0%})")
        print(f"header: {header!r}")
        print("Dry run — nothing embedded, nothing written, nothing spent.")
        return

    print(f"Embedding {len(chunks)} chunks with {EMBEDDING_MODEL}...")

    init_db()
    db = SessionLocal()
    try:
        doc_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO curriculum_documents (id, subject, grade, chapter_no, title, source_file_url) "
                "VALUES (:id, :subject, :grade, :chapter_no, :title, :url)"
            ),
            {"id": str(doc_id), "subject": args.subject, "grade": args.grade,
             "chapter_no": args.chapter_no, "title": args.title, "url": args.pdf_path},
        )
        for i, piece in enumerate(chunks):
            emb = llm.embed(piece)
            db.execute(
                text(
                    "INSERT INTO curriculum_chunks (document_id, chunk_text, embedding, chunk_index, token_count) "
                    "VALUES (:doc, :txt, CAST(:emb AS vector), :idx, :tok)"
                ),
                {"doc": str(doc_id), "txt": piece,
                 "emb": "[" + ",".join(f"{v:.8f}" for v in emb) + "]",
                 "idx": i, "tok": len(piece) // 4},
            )
        db.commit()
        print(f"Ingested document {doc_id}: {len(chunks)} chunks.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
